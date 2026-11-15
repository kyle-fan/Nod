import AppKit
import FlyingFox
import Foundation
import TTSKit
import WhisperKit

// Nod sidecar: ears (WhisperKit), voice (TTSKit), hands (Accessibility API).
// Contract lives in ../SPEC.md. Localhost only, no cloud calls.

let env: [String: String] = {
    setvbuf(stdout, nil, _IOLBF, 0)
    return ProcessInfo.processInfo.environment
}()
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

/// Loads the models, then serves HTTP until the process is killed. Runs off the main thread.
func runSidecar() async throws {
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
    if env["NOD_EARS"] != "0" {
        log("loading Whisper \(whisperModel)")
        let t0 = Date()
        let whisper = try await Ears.loadWhisper(model: whisperModel)
        // Small model for live captions; set NOD_WHISPER_FAST="" to use one model for everything.
        let fastModel = env["NOD_WHISPER_FAST"] ?? "small.en"
        let fast = fastModel.isEmpty || fastModel == whisperModel ? nil : try await Ears.loadWhisper(model: fastModel)
        let e = Ears(whisper: whisper, fast: fast, hubURL: hubURL, env: env)
        await e.warmUp()
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

    // MARK: Hands (Accessibility API)

    let hands = Hands(env: env)
    Task { await hands.watchFrontmost() }
    if !Hands.trusted { log("WARNING: " + Hands.permissionError) }

    await server.appendRoute("GET /screen") { request in
        // Optional ?app=Name reads a specific running app instead of the frontmost one.
        let app = request.query.first(where: { $0.name == "app" })?.value
        return json(hands.screen(appName: app))
    }

    // Debug: raw accessibility tree, e.g. curl "localhost:8790/axdump?app=Messages"
    await server.appendRoute("GET /axdump") { request in
        let app = request.query.first(where: { $0.name == "app" })?.value
        return HTTPResponse(statusCode: .ok, headers: [.contentType: "text/plain; charset=utf-8"],
                            body: Data(hands.dump(appName: app).utf8))
    }

    await server.appendRoute("POST /act") { request in
        let action = await body(request)
        let result = await hands.act(action)
        log("act \(action) -> \(result)")
        return json(result)
    }

    // Dev only: relaunch the (rebuilt) binary from inside this process, so it keeps the
    // Microphone / Accessibility permission of the terminal that first started it.
    await server.appendRoute("POST /restart") { _ in
        let binary = CommandLine.arguments[0]
        let task = Process()
        task.executableURL = URL(fileURLWithPath: "/bin/sh")
        task.arguments = ["-c", "sleep 1; exec \"$0\"", binary]
        try? task.run()
        log("restarting")
        DispatchQueue.global().asyncAfter(deadline: .now() + 0.2) { exit(0) }
        return json(["ok": true])
    }

    log("listening on http://127.0.0.1:\(port)")

    try await server.run()
}

// MARK: Entry point

/// The main thread belongs to AppKit (needed for the overlay window and app notifications);
/// everything else runs in a background task.
@main
enum Sidecar {
    static func main() {
        Task.detached {
            do { try await runSidecar() } catch {
                log("fatal: \(error)")
                exit(1)
            }
        }
        let app = NSApplication.shared
        app.setActivationPolicy(.accessory)   // no Dock icon, never becomes the frontmost app
        var overlay: Overlay?
        if env["NOD_OVERLAY"] == "1" {
            // Blink floats over whatever app is in use (SPEC "Overlay window").
            let url = URL(string: env["NOD_OVERLAY_URL"] ?? "http://127.0.0.1:8000/?overlay=1")!
            overlay = Overlay(url: url, heightFraction: CGFloat(Double(env["NOD_OVERLAY_HEIGHT"] ?? "") ?? 0.34))
            overlay?.show()
        }
        app.run()
        _ = overlay
    }
}
