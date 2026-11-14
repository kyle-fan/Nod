import AVFoundation
import Foundation
import WhisperKit

/// Mic -> WhisperKit -> POST hub /heard.
///
/// Utterance-based: an energy gate finds where the friend starts and stops talking.
/// While they talk we re-transcribe the utterance so far and send it as a partial
/// (`final: false`); after a pause we send exactly one `final: true`.
/// Nothing is sent while MuteGate says the app itself is speaking (+400 ms).
final class Ears: @unchecked Sendable {
    private let whisper: WhisperKit       // accurate model: final sentences
    private let fast: WhisperKit          // small model: live partial captions
    private let hubURL: URL
    private let session: URLSession

    // Audio arrives on the audio thread; the loop drains it.
    private let lock = NSLock()
    private var pending: [[Float]] = []

    // Tunables (seconds / dB). Override with env for noisy rooms.
    private let endSilence: Double        // pause that ends an utterance
    private let minSpeech = 0.25          // shorter bursts are clicks, not speech
    private let maxUtterance = 20.0
    private let preRoll = 0.4
    private let gateDB: Float             // dB above the noise floor that counts as speech
    private let absMinDB: Float = -55
    private let partialGap = 0.25         // => at most ~4 partials per second
    private let frameSize = 480           // 30 ms analysis frames
    private let floorWindow = 167         // frames kept for the noise floor (~5 s)
    private let stableEnd = 1.5           // caption unchanged this long => the sentence is over
    private let emptyDrop = 2.0           // no words after this long => it was noise, not speech

    // Loop state
    private var frameRemainder: [Float] = []
    private var recentDB: [Float] = []        // last ~5 s of frame levels, for the noise floor
    private var recentIndex = 0
    private var noiseDB: Float = -60
    private var framesSinceFloor = 0
    private var fastPasses = 0                // fast-model passes on the current utterance
    private var stableSince = Date()          // when the partial text last changed
    private var clearCaptionAfterMute = false
    private var preRollBuf: [Float] = []
    private var utterance: [Float] = []
    private var inUtterance = false
    private var speechSeconds = 0.0
    private var silenceSeconds = 0.0
    private var lastPartialAt = Date.distantPast
    private var lastPartialSamples = 0
    private var lastPartialText = ""
    private var wasMuted = false
    // Final-quality transcript started early, during the pause, so the final lands sooner.
    private var candidateText: String?
    private var candidateSamples = 0
    private let candidateSilence = 0.3
    private var hubDownLogged = false

    private let debug: Bool
    static let sampleRate = 16000.0

    init(whisper: WhisperKit, fast: WhisperKit?, hubURL: URL, env: [String: String]) {
        self.whisper = whisper
        self.fast = fast ?? whisper
        self.hubURL = hubURL
        self.endSilence = Double(env["NOD_END_SILENCE"] ?? "") ?? 0.7
        self.gateDB = Float(env["NOD_VAD_DB"] ?? "") ?? 9
        self.debug = env["NOD_DEBUG"] == "1"
        let cfg = URLSessionConfiguration.ephemeral
        cfg.timeoutIntervalForRequest = 2
        self.session = URLSession(configuration: cfg)
    }

    /// Load WhisperKit from the local cache when present so nothing touches the network.
    static func loadWhisper(model: String) async throws -> WhisperKit {
        let folder = Voice.modelsRoot().appending(path: "argmaxinc/whisperkit-coreml/openai_whisper-\(model)")
        let local = FileManager.default.fileExists(atPath: folder.appending(path: "AudioEncoder.mlmodelc").path)
        if !local { print("[sidecar] Whisper \(model) not cached yet, downloading (needs network once)") }
        let gpu = ProcessInfo.processInfo.environment["NOD_WHISPER_COMPUTE"] == "gpu"
        let config = WhisperKitConfig(
            model: model,
            modelFolder: local ? folder.path : nil,
            computeOptions: gpu ? ModelComputeOptions(audioEncoderCompute: .cpuAndGPU, textDecoderCompute: .cpuAndGPU) : nil,
            verbose: false,
            prewarm: false,
            load: true,
            download: !local
        )
        return try await WhisperKit(config)
    }

    // MARK: Input

    /// Feed 16 kHz mono samples (from the mic, or from a test file).
    func ingest(_ buffer: [Float]) {
        lock.withLock { pending.append(buffer) }
    }

    func startMic() async throws {
        guard await AudioProcessor.requestRecordPermission() else {
            throw NSError(domain: "Ears", code: 1, userInfo: [NSLocalizedDescriptionKey:
                "Microphone permission denied. Allow it for your terminal in System Settings > Privacy & Security > Microphone."])
        }
        try whisper.audioProcessor.startRecordingLive(inputDeviceID: nil) { [weak self] buffer in
            self?.ingest(buffer)
        }
    }

    /// Dev/test only: play a wav file into the pipeline in real time, as if it were the mic.
    func simulate(file: String) async throws {
        let samples = try AudioProcessor.loadAudioAsFloatArray(fromPath: file)
        let chunk = 1600
        var i = 0
        while i < samples.count {
            ingest(Array(samples[i..<min(i + chunk, samples.count)]))
            i += chunk
            try await Task.sleep(nanoseconds: 100_000_000)
        }
        for _ in 0..<20 { // trailing silence so the utterance closes
            ingest([Float](repeating: 0, count: chunk))
            try await Task.sleep(nanoseconds: 100_000_000)
        }
    }

    // MARK: Loop

    func run() async {
        var sinceCleanup = 0
        while !Task.isCancelled {
            let buffers = lock.withLock { () -> [[Float]] in
                let b = pending; pending.removeAll(keepingCapacity: true); return b
            }

            if MuteGate.shared.isMuted {
                // We are speaking (or just finished): throw away everything we heard.
                if !wasMuted {
                    clearCaptionAfterMute = !lastPartialText.isEmpty
                    reset(); frameRemainder.removeAll(); wasMuted = true
                }
            } else {
                if wasMuted {
                    reset(); frameRemainder.removeAll(); wasMuted = false
                    // A half-heard sentence was on screen when we started talking: clear it.
                    if clearCaptionAfterMute { clearCaptionAfterMute = false; await post("", final: false) }
                }
                for b in buffers { consume(b) }
                await step()
            }

            // WhisperKit's AudioProcessor also keeps every sample; we don't need its copy.
            sinceCleanup += 1
            if sinceCleanup >= 100 {
                sinceCleanup = 0
                whisper.audioProcessor.purgeAudioSamples(keepingLast: 0)
            }
            try? await Task.sleep(nanoseconds: 50_000_000)
        }
    }

    private func reset() {
        utterance.removeAll(keepingCapacity: true)
        preRollBuf.removeAll(keepingCapacity: true)
        inUtterance = false
        speechSeconds = 0
        silenceSeconds = 0
        lastPartialSamples = 0
        lastPartialText = ""
        candidateText = nil
        fastPasses = 0
        stableSince = Date()
    }

    /// Run both models once so the first real sentence is not slow.
    func warmUp() async {
        var noise = [Float](repeating: 0, count: 24000)
        for i in noise.indices { noise[i] = Float.random(in: -0.001...0.001) }
        _ = await transcribe(noise, using: fast)
        _ = await transcribe(noise, using: whisper)
    }

    /// Split incoming audio into fixed 30 ms frames so the gate behaves the same
    /// whatever buffer size the microphone delivers.
    private func consume(_ buffer: [Float]) {
        guard !buffer.isEmpty else { return }
        frameRemainder.append(contentsOf: buffer)
        var start = 0
        while frameRemainder.count - start >= frameSize {
            consumeFrame(Array(frameRemainder[start..<start + frameSize]))
            start += frameSize
        }
        if start > 0 { frameRemainder.removeFirst(start) }
    }

    private func consumeFrame(_ frame: [Float]) {
        let seconds = Double(frame.count) / Ears.sampleRate
        var sum: Float = 0
        for s in frame { sum += s * s }
        let db = 10 * log10(max(sum / Float(frame.count), 1e-10))

        // Noise floor = 10th percentile of the last ~5 s. It follows the room both ways:
        // a pause pulls it down within half a second, and steady background noise
        // (fans, a crowd) pulls it up within a few seconds, so the gate can never stick open.
        if recentDB.count < floorWindow { recentDB.append(db) } else {
            recentDB[recentIndex] = db
            recentIndex = (recentIndex + 1) % floorWindow
        }
        framesSinceFloor += 1
        if framesSinceFloor >= 5 || recentDB.count < 20 {
            framesSinceFloor = 0
            let sorted = recentDB.sorted()
            noiseDB = max(sorted[sorted.count / 10], -75)
        }
        // Hysteresis: a sentence must start clearly above the room noise, but once it has
        // started, quieter syllables still count, so soft or distant speech is not chopped up.
        let margin = inUtterance ? max(gateDB - 4, 3) : gateDB
        let isSpeech = db > max(absMinDB, noiseDB + margin)

        if inUtterance {
            utterance.append(contentsOf: frame)
            if isSpeech { speechSeconds += seconds; silenceSeconds = 0; candidateText = nil } else { silenceSeconds += seconds }
        } else if isSpeech {
            inUtterance = true
            utterance = preRollBuf + frame
            preRollBuf.removeAll(keepingCapacity: true)
            speechSeconds = seconds
            silenceSeconds = 0
            lastPartialSamples = 0
            lastPartialText = ""
            fastPasses = 0
            stableSince = Date()
        } else {
            preRollBuf.append(contentsOf: frame)
            let keep = Int(preRoll * Ears.sampleRate)
            if preRollBuf.count > keep { preRollBuf.removeFirst(preRollBuf.count - keep) }
        }
    }

    private func step() async {
        guard inUtterance else { return }
        let length = Double(utterance.count) / Ears.sampleRate

        // Safety nets for rooms where the energy gate can't find the pause:
        // 1. the caption stopped changing -> the friend stopped talking;
        // 2. we keep "hearing" something but there are no words -> it was noise, drop it.
        let captionStable = !lastPartialText.isEmpty && fastPasses >= 2
            && Date().timeIntervalSince(stableSince) >= stableEnd
        if lastPartialText.isEmpty, fastPasses >= 2, length >= emptyDrop, silenceSeconds < endSilence {
            if debug { print("[sidecar] dropped \(String(format: "%.1f", length))s of sound with no words") }
            reset()
            return
        }

        if silenceSeconds >= endSilence || length >= maxUtterance || captionStable {
            let audio = utterance
            let hadSpeech = speechSeconds >= minSpeech
            let hadPartial = !lastPartialText.isEmpty
            let candidate = candidateText
            reset()
            guard hadSpeech else { return }
            let text: String
            if let candidate { text = candidate } else { text = await transcribe(audio, using: whisper) }
            if !text.isEmpty { await post(text, final: true) }
            else if hadPartial { await post("", final: false) } // clear stale caption
            return
        }

        // The speaker paused: start the accurate pass now. If they keep talking it is discarded
        // (consume() clears it); if the pause holds, the final is ready the moment it ends.
        if silenceSeconds >= candidateSilence, candidateText == nil, speechSeconds >= minSpeech {
            candidateSamples = utterance.count
            candidateText = await transcribe(utterance, using: whisper)
            return
        }

        // Partial: only when there is new audio and we are not flooding the hub.
        let newSamples = utterance.count - lastPartialSamples
        if speechSeconds >= minSpeech,
           Double(newSamples) / Ears.sampleRate >= 0.3,
           Date().timeIntervalSince(lastPartialAt) >= partialGap {
            lastPartialSamples = utterance.count
            let text = await transcribe(utterance, using: fast)
            lastPartialAt = Date()
            fastPasses += 1
            if Ears.normalize(text) != Ears.normalize(lastPartialText) { stableSince = Date() }
            if !text.isEmpty, text != lastPartialText, inUtterance {
                lastPartialText = text
                await post(text, final: false)
            }
        }
    }

    // MARK: Whisper

    private func transcribe(_ audio: [Float], using model: WhisperKit) async -> String {
        var options = DecodingOptions()
        options.language = "en"
        options.temperature = 0
        options.temperatureFallbackCount = 0
        options.withoutTimestamps = true
        options.skipSpecialTokens = true
        options.wordTimestamps = false
        do {
            let t0 = Date()
            let results = try await model.transcribe(audioArray: audio, decodeOptions: options)
            if debug { print(String(format: "[sidecar] \(model === whisper ? "final" : "fast") transcribe %.2fs audio in %.2fs", Double(audio.count) / Ears.sampleRate, Date().timeIntervalSince(t0))) }
            return Ears.clean(results.map(\.text).joined(separator: " "))
        } catch {
            print("[sidecar] transcribe failed: \(error)")
            return ""
        }
    }

    /// Strip Whisper's non-speech tags and the stock phrases it hallucinates on noise.
    static func clean(_ raw: String) -> String {
        var text = raw
        for pattern in ["\\[[^\\]]*\\]", "\\([^\\)]*\\)", "<\\|[^|]*\\|>"] {
            text = text.replacingOccurrences(of: pattern, with: " ", options: .regularExpression)
        }
        text = text.replacingOccurrences(of: "\\s+", with: " ", options: .regularExpression)
            .trimmingCharacters(in: .whitespacesAndNewlines)
        let bare = normalize(text)
        let junk: Set<String> = ["", "you", "thank you", "thanks for watching", "thank you for watching",
                                 "bye", "so", "uh", "um", "hmm", "mm"]
        if junk.contains(bare) { return "" }
        return text
    }

    static func normalize(_ s: String) -> String {
        s.lowercased().replacingOccurrences(of: "[^a-z0-9 ]", with: "", options: .regularExpression)
            .replacingOccurrences(of: "\\s+", with: " ", options: .regularExpression)
            .trimmingCharacters(in: .whitespaces)
    }

    /// Echo guard: right after we spoke, drop anything that is mostly our own words.
    private func isEcho(_ text: String) -> Bool {
        let gate = MuteGate.shared
        guard Date().timeIntervalSince(gate.mutedUntil) < 4 else { return false }
        let heard = Set(Ears.normalize(text).split(separator: " "))
        let spoken = Set(Ears.normalize(gate.lastSpoken).split(separator: " "))
        guard !heard.isEmpty, !spoken.isEmpty else { return false }
        return Double(heard.intersection(spoken).count) / Double(heard.count) >= 0.7
    }

    // MARK: Hub

    private func post(_ text: String, final: Bool) async {
        // Mute rule, checked immediately before every POST.
        guard !MuteGate.shared.isMuted else { return }
        if !text.isEmpty, isEcho(text) {
            print("[sidecar] dropped echo of our own speech: \(text)")
            return
        }
        var request = URLRequest(url: hubURL)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "content-type")
        request.httpBody = try? JSONSerialization.data(withJSONObject: ["text": text, "final": final])
        if final { print("[sidecar] heard: \(text)") }
        do {
            _ = try await session.data(for: request)
            hubDownLogged = false
        } catch {
            if !hubDownLogged {
                print("[sidecar] hub not reachable at \(hubURL.absoluteString) (will keep trying)")
                hubDownLogged = true
            }
        }
    }
}
