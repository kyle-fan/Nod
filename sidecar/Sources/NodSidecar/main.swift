import FlyingFox
import Foundation
import TTSKit
import WhisperKit

// Nod sidecar: ears (WhisperKit), voice (TTSKit), hands (Accessibility API).
// Contract lives in ../SPEC.md. Localhost only, no cloud calls.

setvbuf(stdout, nil, _IOLBF, 0)
let env = ProcessInfo.processInfo.environment
let port = UInt16(env["NOD_PORT"] ?? "") ?? 8790

func log(_ s: String) { print("[sidecar] \(s)") }

func json(_ object: Any, status: HTTPStatusCode = .ok) -> HTTPResponse {
    let data = (try? JSONSerialization.data(withJSONObject: object)) ?? Data("{}".utf8)
    return HTTPResponse(statusCode: status, headers: [.contentType: "application/json"], body: data)
}

func fail(_ message: String, status: HTTPStatusCode = .ok) -> HTTPResponse {
    json(["ok": false, "error": message], status: status)
}

func body(_ request: HTTPRequest) async -> [String: Any] {
    guard let data = try? await request.bodyData,
          let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return [:] }
    return obj
}

/// The character's voice style lives in hub/profile.json ("voice"). Read-only.
func profileVoice() -> String? {
    let cwd = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
    let candidates = [env["NOD_PROFILE"].map { URL(fileURLWithPath: $0) },
                      cwd.appending(path: "../hub/profile.json"),
                      cwd.appending(path: "hub/profile.json")].compactMap { $0 }
    for url in candidates {
        if let data = try? Data(contentsOf: url),
           let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           let voice = obj["voice"] as? String, !voice.isEmpty { return voice }
    }
    return nil
}

// MARK: Voice (loaded once)

let ttsModel = TTSModelVariant(rawValue: env["NOD_TTS_MODEL"] ?? "1.7b") ?? .qwen3TTS_1_7b
let ttsSpeaker = Qwen3Speaker(rawValue: env["NOD_TTS_SPEAKER"] ?? "aiden") ?? .aiden
let voiceStyle = env["NOD_TTS_STYLE"] ?? profileVoice()

log("loading TTS \(ttsModel.rawValue), speaker \(ttsSpeaker.rawValue), style: \(voiceStyle ?? "none")")
let loadStart = Date()
let voice = try await Voice(model: ttsModel, speaker: ttsSpeaker, instruction: voiceStyle)
await voice.warmUp()
log(String(format: "TTS ready in %.1fs", Date().timeIntervalSince(loadStart)))

// MARK: Ears (mic -> WhisperKit -> hub /heard)

let hubURL = URL(string: env["NOD_HUB_URL"] ?? "http://127.0.0.1:8000/heard")!
let whisperModel = env["NOD_WHISPER_MODEL"] ?? "large-v3-v20240930_626MB"
var ears: Ears?
if env["NOD_EARS"] != "0" {
    log("loading Whisper \(whisperModel)")
    let t0 = Date()
    let whisper = try await Ears.loadWhisper(model: whisperModel)
    // Small model for live captions; set NOD_WHISPER_FAST="" to use one model for everything.
    let fastModel = env["NOD_WHISPER_FAST"] ?? "small.en"
    let fast = fastModel.isEmpty || fastModel == whisperModel ? nil : try await Ears.loadWhisper(model: fastModel)
    let e = Ears(whisper: whisper, fast: fast, hubURL: hubURL, env: env)
    await e.warmUp()
    ears = e
    if let file = env["NOD_AUDIO_FILE"] {
        log("TEST MODE: feeding \(file) instead of the microphone")
        Task { try? await e.simulate(file: file) }
    } else {
        // Don't block startup on the macOS microphone prompt.
        Task {
            do { try await e.startMic(); log("microphone on") }
            catch { log("MIC ERROR: \(error.localizedDescription)") }
        }
    }
    Task { await e.run() }
    log(String(format: "ears ready in %.1fs, posting to %@", Date().timeIntervalSince(t0), hubURL.absoluteString))
}

// MARK: Routes

let server = HTTPServer(address: try .inet(ip4: "127.0.0.1", port: port))

await server.appendRoute("GET /health") { _ in
    json(["ok": true])
}

await server.appendRoute("POST /speak") { request in
    let text = ((await body(request))["text"] as? String ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
    guard !text.isEmpty else { return fail("missing text", status: .badRequest) }
    do {
        let t = try await voice.speak(text)
        log(String(format: "spoke %.1fs of audio, first audio %.2fs, total %.2fs: %@",
                   t.audioSeconds, t.firstAudio, t.total, text))
        return json(["ok": true])
    } catch {
        log("speak failed: \(error)")
        return fail("\(error)", status: .internalServerError)
    }
}

log("listening on http://127.0.0.1:\(port)")
try await server.run()
