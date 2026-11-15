import Foundation
import TTSKit

/// Shared mute state. The ears check this before every POST to the hub so the
/// app never transcribes its own voice (SPEC "Mute rule": while speaking + 400 ms).
final class MuteGate: @unchecked Sendable {
    static let shared = MuteGate()
    private let lock = NSLock()
    private var speaking = false
    private var muteUntil = Date.distantPast
    private(set) var lastSpoken = ""
    let tail: TimeInterval = 0.4

    func begin(_ text: String) {
        lock.withLock { speaking = true; lastSpoken = text }
    }

    func end() {
        lock.withLock { speaking = false; muteUntil = Date().addingTimeInterval(tail) }
    }

    /// True while TTS is playing or within the 400 ms tail after it stops.
    var isMuted: Bool {
        lock.withLock { speaking || Date() < muteUntil }
    }

    /// Time the mute window last closed (or will close). Used to drop audio captured during speech.
    var mutedUntil: Date {
        lock.withLock { speaking ? .distantFuture : muteUntil }
    }
}

/// Wraps TTSKit. Loaded once at startup; requests are serialized by the actor.
actor Voice {
    private let tts: TTSKit
    private let speaker: Qwen3Speaker
    private let instruction: String?
    /// Test mode: generate the speech and wait as long as it would take to play, but make no sound.
    private var silent = ProcessInfo.processInfo.environment["NOD_SILENT"] == "1"

    func setSilent(_ on: Bool) { silent = on }
    var isSilent: Bool { silent }

    static func modelsRoot() -> URL {
        FileManager.default.homeDirectoryForCurrentUser
            .appending(path: "Documents/huggingface/models")
    }

    init(model: TTSModelVariant, speaker: Qwen3Speaker, instruction: String?) async throws {
        let root = Voice.modelsRoot()
        let modelFolder = root.appending(path: "argmaxinc/ttskit-coreml")
        let tokenizerFolder = root.appending(path: "Qwen/Qwen3-0.6B")
        let haveLocal = FileManager.default.fileExists(atPath: modelFolder
            .appending(path: "qwen3_tts/text_projector/12hz-\(model.rawValue)-customvoice").path)
            && FileManager.default.fileExists(atPath: tokenizerFolder.appending(path: "tokenizer.json").path)

        // Offline-first: point at the local cache so no network is touched at runtime.
        // If the cache is missing (first run, online), let TTSKit download it.
        if !haveLocal { print("[sidecar] TTS \(model.rawValue) not cached yet, downloading (needs network once)") }
        let config = TTSKitConfig(
            model: model,
            modelFolder: haveLocal ? modelFolder : nil,
            tokenizerFolder: haveLocal ? tokenizerFolder : nil,
            load: true
        )
        self.tts = try await TTSKit(config)
        self.speaker = speaker
        self.instruction = model == .qwen3TTS_1_7b ? instruction : nil
    }

    private func options() -> GenerationOptions {
        var o = GenerationOptions()
        o.instruction = instruction
        o.repetitionPenalty = 1.05
        o.temperature = 0.7
        return o
    }

    /// Generate silently once so CoreML graphs are compiled and the first real reply is fast.
    func warmUp() async {
        _ = try? await tts.generate(text: "Hey.", speaker: speaker, language: .english, options: options())
    }

    /// Speak and return only after playback has finished.
    func speak(_ text: String) async throws -> (audioSeconds: Double, firstAudio: Double, total: Double) {
        let start = Date()
        let firstAudioBox = FirstAudio()
        MuteGate.shared.begin(text)
        defer { MuteGate.shared.end() }
        // TTSKit's .auto strategy sizes its pre-buffer for the 20 s maximum utterance (~2 s of
        // silence before a short reply). Our replies are short, so size it for this text:
        // generation runs ~10% slower than real time, budget 20% of the estimated length.
        if silent {
            let result = try await tts.generate(text: text, speaker: speaker, language: .english, options: options())
            let generated = Date().timeIntervalSince(start)
            let remaining = result.audioDuration + 0.5 - generated
            if remaining > 0 { try? await Task.sleep(nanoseconds: UInt64(remaining * 1_000_000_000)) }
            return (result.audioDuration, 0.5, Date().timeIntervalSince(start))
        }
        let words = Double(text.split(separator: " ").count)
        let estimatedSeconds = words * 0.45 + 0.8
        let buffer = min(2.0, max(0.25, estimatedSeconds * 0.2))
        let result = try await tts.play(
            text: text, speaker: speaker, language: .english, options: options(),
            playbackStrategy: .buffered(seconds: buffer),
            callback: { _ in
                firstAudioBox.mark(since: start)
                return nil
            }
        )
        return (result.audioDuration, firstAudioBox.value, Date().timeIntervalSince(start))
    }
}

/// Records when the first audio chunk was generated (the callback runs off the actor).
private final class FirstAudio: @unchecked Sendable {
    private let lock = NSLock()
    private var seconds: Double = -1
    func mark(since start: Date) { lock.withLock { if seconds < 0 { seconds = Date().timeIntervalSince(start) } } }
    var value: Double { lock.withLock { seconds } }
}
