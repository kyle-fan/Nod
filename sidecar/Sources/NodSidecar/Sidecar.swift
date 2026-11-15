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

/// Names and proper nouns from hub/profile.json (people, apps, capitalised words in "about",
/// plus an optional "vocabulary" list): words the speech recogniser should expect.
func profileVocabulary() -> [String] {
    let cwd = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
    let candidates = [env["NOD_PROFILE"].map { URL(fileURLWithPath: $0) },
                      cwd.appending(path: "../hub/profile.json"),
                      cwd.appending(path: "hub/profile.json")].compactMap { $0 }
    for url in candidates {
        guard let data = try? Data(contentsOf: url),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { continue }
        var words: [String] = (obj["vocabulary"] as? [String]) ?? []
        if let name = obj["name"] as? String { words.append(name) }
        words += Array(((obj["people"] as? [String: Any]) ?? [:]).keys)
        for line in (obj["about"] as? [String]) ?? [] {
            // capitalised words that are not the first word of the line: names, teams, games
            let parts = line.split(separator: " ").map(String.init)
            var run: [String] = []
            for (i, raw) in parts.enumerated() {
                let w = raw.trimmingCharacters(in: .punctuationCharacters)
                // a line's first word is capitalised anyway; count it only if it isn't an ordinary opener
                let opener = i == 0 && ["has", "have", "shares", "share", "does", "doesn't", "loves", "likes",
                                        "plays", "talks", "is", "was", "can", "can't", "the", "his", "her", "a", "an",
                                        "lives", "works", "studies", "always", "never", "most"].contains(w.lowercased())
                if !opener, let f = w.first, f.isUppercase, w.count > 2 { run.append(w) }
                else { if !run.isEmpty { words.append(run.joined(separator: " ")) }; run = [] }
            }
            if !run.isEmpty { words.append(run.joined(separator: " ")) }
        }
        var seen = Set<String>()
        return words.filter { seen.insert($0.lowercased()).inserted }
    }
    return []
}

/// People the character knows (hub/profile.json "people"). Read-only.
func profilePeople() -> [String] {
    let cwd = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
    let candidates = [env["NOD_PROFILE"].map { URL(fileURLWithPath: $0) },
                      cwd.appending(path: "../hub/profile.json"),
                      cwd.appending(path: "hub/profile.json")].compactMap { $0 }
    for url in candidates {
        if let data = try? Data(contentsOf: url),
           let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           let people = obj["people"] as? [String: Any] { return Array(people.keys) }
    }
    return []
}

nonisolated(unsafe) var earsRef: Ears?

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
        earsRef = e
        if env["NOD_VOCAB"] != "0" {
            let vocab = profileVocabulary()
            e.setVocabulary(vocab)
            log("speech hints: \(vocab.joined(separator: ", "))")
        }
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

    // Diagnostics: is the microphone delivering sound, and how loud is it?
    await server.appendRoute("GET /ears") { _ in
        guard let ears = earsRef else { return json(["ears": "off"]) }
        let status = ears.status
        ears.resetPeak()
        return json(status)
    }

    await server.appendRoute("GET /health") { _ in
        json(["ok": true])
    }

    await server.appendRoute("POST /speak") { request in
        let text = ((await body(request))["text"] as? String ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return fail("missing text", status: .badRequest) }
        do {
            let t = try await voice.speak(text)
            log(String(format: "\(await voice.isSilent ? "(silent) " : "")spoke %.1fs of audio, first audio %.2fs, total %.2fs: %@",
                       t.audioSeconds, t.firstAudio, t.total, text))
            return json(["ok": true])
        } catch {
            log("speak failed: \(error)")
            return fail("\(error)", status: .internalServerError)
        }
    }

    // Test mode: POST /quiet?on=1 makes /speak silent (same timing, no sound); on=0 restores it.
    await server.appendRoute("POST /quiet") { request in
        let on = request.query.first(where: { $0.name == "on" })?.value != "0"
        await voice.setSilent(on)
        return json(["ok": true, "silent": on])
    }

    // MARK: Hands (Accessibility API)

    let hands = Hands(env: env)
    Task { await hands.watchFrontmost() }
    // Known names, so misheard ones ("Kayden Ron") are corrected to the real contact.
    NameBook.shared.setProfileNames(profilePeople())
    Task {
        while !Task.isCancelled {
            let names = hands.messagesContactNames()
            if !names.isEmpty { NameBook.shared.setContacts(names) }
            try? await Task.sleep(nanoseconds: 20_000_000_000)
        }
    }

    // Turn the floating tile window on or off while running: POST /overlay?on=1 (or 0).
    await server.appendRoute("POST /overlay") { request in
        let on = request.query.first(where: { $0.name == "on" })?.value != "0"
        await MainActor.run { if on { showOverlay() } else { hideOverlay() } }
        return json(["ok": true, "overlay": on])
    }

    // Tell the hub when a text message arrives, so the next nod can answer it.
    // Off unless NOD_WATCH_MESSAGES=1: it reads the Messages window every few seconds.
    if env["NOD_WATCH_MESSAGES"] == "1" {
        let incomingURL = hubURL.deletingLastPathComponent().appending(path: "incoming")
        log("watching Messages for new texts")
        Task {
            var seen: Set<String>? = nil     // nil until the first look: what is already there is not news
            while !Task.isCancelled {
                let texts = hands.unansweredTexts()
                let keys = Set(texts.map { "\($0.from)|\($0.text)" })
                if let old = seen {
                    for t in texts where !old.contains("\(t.from)|\(t.text)") {
                        var request = URLRequest(url: incomingURL)
                        request.httpMethod = "POST"
                        request.timeoutInterval = 2
                        request.setValue("application/json", forHTTPHeaderField: "content-type")
                        request.httpBody = try? JSONSerialization.data(withJSONObject: ["from": t.from, "text": t.text])
                        _ = try? await URLSession.shared.data(for: request)
                        log("text from \(t.from) passed to the hub")
                    }
                    seen = old.union(keys)
                } else if Hands.trusted {
                    seen = keys
                }
                try? await Task.sleep(nanoseconds: 3_000_000_000)
            }
        }
    }

    // Debug: what a transcript would be corrected to, e.g. /names?text=tell%20Kayden%20Ron
    await server.appendRoute("GET /names") { request in
        let text = (request.query.first(where: { $0.name == "text" })?.value ?? "").replacingOccurrences(of: "+", with: " ")
        return json(["known": NameBook.shared.count, "corrected": NameBook.shared.correct(text)])
    }
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
        // Keep the overlay across the relaunch if it is showing now.
        if await MainActor.run(body: { overlayWindow != nil }) { setenv("NOD_OVERLAY", "1", 1) }
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

// MARK: Overlay

@MainActor var overlayWindow: Overlay?

/// Blink floats over whatever app is in use (SPEC "Overlay window").
@MainActor func showOverlay() {
    guard overlayWindow == nil else { return }
    let url = URL(string: env["NOD_OVERLAY_URL"] ?? "http://127.0.0.1:8000/?overlay=1")!
    let overlay = Overlay(url: url, heightFraction: CGFloat(Double(env["NOD_OVERLAY_HEIGHT"] ?? "") ?? 0.34))
    overlay.show()
    overlayWindow = overlay
}

@MainActor func hideOverlay() {
    overlayWindow?.close()
    overlayWindow = nil
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
        if env["NOD_OVERLAY"] == "1" { showOverlay() }
        app.run()
    }
}
