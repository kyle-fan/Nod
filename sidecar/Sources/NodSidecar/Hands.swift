import AppKit
import ApplicationServices
import Foundation

/// Screen reading and acting through the macOS Accessibility API (AXUIElement).
/// GET /screen lists pressable / typeable elements; POST /act presses, types or opens an app.
final class Hands: @unchecked Sendable {
    private let lock = NSLock()
    private var elements: [Int: AXUIElement] = [:]   // valid until the next /screen
    private var roles: [Int: String] = [:]
    private var sendFieldID: [Int: AXUIElement] = [:]   // synthetic "Send" ids -> the message field
    private var clickIDs: Set<Int> = []                  // ids that need a real mouse click, not AXPress
    private var backID: Int?                             // synthetic "Back to conversations"
    private var chatViewUntil = Date.distantPast         // Messages: show the open chat, not the list
    private var rowNames: [Int: String] = [:]            // Messages list view: id -> the name on the row
    private var pressedChat: String?                     // the row that was just pressed (the open chat)
    private var lastTarget: NSRunningApplication?        // last real app in front (never Blink itself)
    private let ignoredApps: Set<String>
    private static let browsers: Set<String> = [
        "com.apple.Safari", "com.google.Chrome", "company.thebrowser.Browser", "org.mozilla.firefox",
        "com.microsoft.edgemac", "com.brave.Browser",
    ]

    init(env: [String: String]) {
        // Apps that are part of running Nod, never the thing the user wants to control.
        let names = env["NOD_IGNORE_APPS"] ?? "Terminal,iTerm2,Claude,Xcode,loginwindow"
        ignoredApps = Set(names.split(separator: ",").map { $0.trimmingCharacters(in: .whitespaces).lowercased() })
    }

    /// Keep track of the app in front so /screen can skip Blink's own window (SPEC "Overlay window").
    func watchFrontmost() async {
        while !Task.isCancelled {
            if Hands.trusted { _ = targetApp() }
            try? await Task.sleep(nanoseconds: 700_000_000)
        }
    }

    /// True for windows that are Nod itself: this process (the overlay), the terminal that
    /// launched it, or a browser tab showing the hub page (its title is "Blink").
    private func isBlink(_ app: NSRunningApplication) -> Bool {
        if app.processIdentifier == ProcessInfo.processInfo.processIdentifier { return true }
        if let name = app.localizedName?.lowercased(), ignoredApps.contains(name) { return true }
        if let bundle = app.bundleIdentifier, Hands.browsers.contains(bundle) {
            let appEl = AXUIElementCreateApplication(app.processIdentifier)
            AXUIElementSetMessagingTimeout(appEl, 0.5)
            // Any window of this browser showing the hub page makes the whole browser "Blink":
            // the tiles live there, so it is never the app the user wants to control.
            var windows = (attr(appEl, kAXWindowsAttribute) as? [AXUIElement]) ?? []
            if let focused = attr(appEl, kAXFocusedWindowAttribute) { windows.append(focused as! AXUIElement) }
            for window in windows {
                if let title = string(window, kAXTitleAttribute), title.lowercased().hasPrefix("openmouth") || title.lowercased().hasPrefix("blink") { return true }
            }
        }
        return false
    }

    /// The frontmost app that is not Blink. Falls back to whatever is in front.
    private func targetApp() -> NSRunningApplication? {
        let front = frontmostApp()
        if let front, !isBlink(front) {
            lock.withLock { lastTarget = front }
            return front
        }
        if let last = lock.withLock({ lastTarget }), !last.isTerminated { return last }
        // Nothing remembered yet (we just started): take the topmost window that isn't Blink.
        let windows = (CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID)
            as? [[String: Any]]) ?? []
        for w in windows where (w[kCGWindowLayer as String] as? Int) == 0 {
            guard let pid = w[kCGWindowOwnerPID as String] as? pid_t,
                  let app = NSRunningApplication(processIdentifier: pid),
                  app.activationPolicy == .regular, !isBlink(app) else { continue }
            lock.withLock { lastTarget = app }
            return app
        }
        return front
    }

    private let maxElements = 40
    private let maxDepth = 25
    private let maxVisited = 4000
    private let timeBudget: TimeInterval = 2.5

    static var trusted: Bool { AXIsProcessTrusted() }

    static let permissionError =
        "Accessibility permission missing. Allow your terminal in System Settings > Privacy & Security > Accessibility, then restart the sidecar."

    // MARK: AX helpers

    private func attr(_ el: AXUIElement, _ name: String) -> AnyObject? {
        var value: AnyObject?
        return AXUIElementCopyAttributeValue(el, name as CFString, &value) == .success ? value : nil
    }

    private func string(_ el: AXUIElement, _ name: String) -> String? {
        guard let s = attr(el, name) as? String else { return nil }
        let t = s.replacingOccurrences(of: "\\s+", with: " ", options: .regularExpression)
            .trimmingCharacters(in: .whitespacesAndNewlines)
        return t.isEmpty ? nil : t
    }

    private func children(_ el: AXUIElement) -> [AXUIElement] {
        (attr(el, kAXChildrenAttribute) as? [AXUIElement]) ?? []
    }

    private func actions(_ el: AXUIElement) -> [String] {
        var names: CFArray?
        guard AXUIElementCopyActionNames(el, &names) == .success, let list = names as? [String] else { return [] }
        return list
    }

    /// The app the user is looking at. Uses the system-wide AX element (always current),
    /// falling back to NSWorkspace.
    private func frontmostApp() -> NSRunningApplication? {
        let system = AXUIElementCreateSystemWide()
        if let focused = attr(system, kAXFocusedApplicationAttribute) {
            var pid: pid_t = 0
            if AXUIElementGetPid(focused as! AXUIElement, &pid) == .success,
               let app = NSRunningApplication(processIdentifier: pid) { return app }
        }
        return NSWorkspace.shared.frontmostApplication
    }

    private func runningApp(named name: String) -> NSRunningApplication? {
        let wanted = name.lowercased()
        return NSWorkspace.shared.runningApplications.first {
            $0.activationPolicy == .regular && $0.localizedName?.lowercased() == wanted
        }
    }

    // MARK: /screen

    private func mapRole(_ el: AXUIElement, axRole: String) -> String? {
        switch axRole {
        case "AXButton", "AXPopUpButton", "AXMenuButton", "AXDisclosureTriangle": return "button"
        case "AXTextField", "AXTextArea", "AXComboBox", "AXSearchField":
            // Read-only text (chat bubbles, labels) is not something you can type into.
            var settable: DarwinBoolean = false
            AXUIElementIsAttributeSettable(el, kAXValueAttribute as CFString, &settable)
            return settable.boolValue ? "textfield" : nil
        case "AXLink": return "link"
        case "AXMenuItem", "AXMenuBarItem": return "menuitem"
        case "AXCheckBox", "AXSwitch", "AXToggle": return "checkbox"
        case "AXRadioButton":
            return string(el, kAXSubroleAttribute) == "AXTabButton" ? "tab" : "checkbox"
        case "AXTab": return "tab"
        case "AXCell", "AXRow", "AXGroup", "AXStaticText", "AXImage", "AXGenericElement":
            // Many modern apps (Messages' conversation list, web views) expose clickable rows
            // as generic elements; count them only if they really accept a press.
            return actions(el).contains(kAXPressAction as String) ? "button" : nil
        default: return nil
        }
    }

    private func label(_ el: AXUIElement, role: String) -> String? {
        var text = string(el, kAXTitleAttribute)
            ?? string(el, kAXDescriptionAttribute)
            ?? string(el, kAXPlaceholderValueAttribute)
        if text == nil, role == "textfield" { text = "Text field" }
        if text == nil, let titleEl = attr(el, kAXTitleUIElementAttribute) {
            text = string(titleEl as! AXUIElement, kAXValueAttribute) ?? string(titleEl as! AXUIElement, kAXTitleAttribute)
        }
        if text == nil, role == "button" {
            // A pressable row: use the first text found inside it (e.g. the contact name).
            text = firstText(in: el, depth: 0)
        }
        guard var t = text else { return nil }
        if t.count > 80 { t = String(t.prefix(77)) + "..." }
        return t
    }

    private func firstText(in el: AXUIElement, depth: Int) -> String? {
        guard depth < 4 else { return nil }
        for child in children(el).prefix(8) {
            if let v = string(child, kAXValueAttribute) ?? string(child, kAXTitleAttribute)
                ?? string(child, kAXDescriptionAttribute) { return v }
            if let v = firstText(in: child, depth: depth + 1) { return v }
        }
        return nil
    }

    func screen(appName: String? = nil) -> [String: Any] {
        guard Hands.trusted else { return ["app": "", "elements": [], "error": Hands.permissionError] }
        guard let app = (appName.flatMap { runningApp(named: $0) }) ?? targetApp() else {
            return ["app": "", "elements": []]
        }
        let appEl = AXUIElementCreateApplication(app.processIdentifier)
        AXUIElementSetMessagingTimeout(appEl, 1.0)
        // Electron / Chromium apps only build their accessibility tree when asked.
        AXUIElementSetAttributeValue(appEl, "AXManualAccessibility" as CFString, kCFBooleanTrue)
        AXUIElementSetAttributeValue(appEl, "AXEnhancedUserInterface" as CFString, kCFBooleanTrue)

        // Messages gets a purpose-built reading: contact names, the message box and Send.
        if app.bundleIdentifier == "com.apple.MobileSMS", let result = messagesScreen(app: app, appEl: appEl) {
            return result
        }

        // Windows only (focused first). The menu bar would flood the list with menu items.
        var roots: [AXUIElement] = []
        if let focused = attr(appEl, kAXFocusedWindowAttribute) { roots.append(focused as! AXUIElement) }
        for w in (attr(appEl, kAXWindowsAttribute) as? [AXUIElement]) ?? [] where !roots.contains(where: { CFEqual($0, w) }) {
            roots.append(w)
        }

        struct Found { let el: AXUIElement; let role: String; let label: String; let web: Bool }
        var found: [Found] = []
        var seen = Set<String>()
        var visited = 0
        let deadline = Date().addingTimeInterval(timeBudget)

        func walk(_ el: AXUIElement, depth: Int, inWeb: Bool = false) {
            guard depth <= maxDepth, visited < maxVisited, found.count < maxElements * 3, Date() < deadline else { return }
            visited += 1
            let axRole = string(el, kAXRoleAttribute) ?? ""
            let inWeb = inWeb || axRole == "AXWebArea"   // inside a web page (Safari, Chrome, web views)
            var matched = false
            if let role = mapRole(el, axRole: axRole) {
                let enabled = (attr(el, kAXEnabledAttribute) as? Bool) ?? true
                if enabled, let text = label(el, role: role) {
                    let key = role + "|" + text
                    if !seen.contains(key) {
                        seen.insert(key)
                        found.append(Found(el: el, role: role, label: text, web: inWeb))
                    }
                    matched = true
                }
            }
            // Don't descend into a matched leaf control (a button's inner label/image).
            if matched, axRole != "AXGroup", axRole != "AXCell", axRole != "AXRow" { return }
            for child in children(el) { walk(child, depth: depth + 1, inWeb: inWeb) }
        }
        for root in roots { walk(root, depth: 0) }

        var keep = Set<Int>()
        let onPage = found.indices.filter { found[$0].web }
        if !onPage.isEmpty {
            // A web page: what matters is the page's own links and buttons. The browser's
            // toolbar (extensions, zoom, bookmarks) only crowds the list, so it gets a few slots.
            for i in found.indices where found[i].role == "textfield" && keep.count < 4 { keep.insert(i) }
            for i in onPage where keep.count < 30 { keep.insert(i) }
            for i in found.indices where !found[i].web && keep.count < 34 { keep.insert(i) }
        } else {
            // Cap at 40 but never drop a text field: keep fields, fill the rest in screen order.
            keep = Set(found.indices.filter { found[$0].role == "textfield" }.prefix(maxElements))
            for i in found.indices where keep.count < maxElements { keep.insert(i) }
        }

        var out: [[String: Any]] = []
        var map: [Int: AXUIElement] = [:]
        var roleMap: [Int: String] = [:]
        var sendMap: [Int: AXUIElement] = [:]
        var id = 0
        var hasSend = false
        var messageField: AXUIElement?
        let isMessages = app.bundleIdentifier == "com.apple.MobileSMS"
        for i in found.indices.sorted() where keep.contains(i) {
            id += 1
            map[id] = found[i].el
            roleMap[id] = found[i].role
            var item: [String: Any] = ["id": id, "role": found[i].role, "label": found[i].label]
            if found[i].role == "textfield" {
                // The hub reads "value" to know a message has been typed.
                item["value"] = (attr(found[i].el, kAXValueAttribute) as? String) ?? ""
                let lower = found[i].label.lowercased()
                if isMessages, lower.contains("message") { messageField = found[i].el }
            } else if found[i].label.lowercased().contains("send") {
                hasSend = true
            }
            out.append(item)
        }
        // Messages sends with Return and shows no Send control. Offer one so the hub can
        // ask "Send ...?" first; pressing it posts Return to the message field.
        if isMessages, !hasSend, let field = messageField {
            id += 1
            sendMap[id] = field
            out.append(["id": id, "role": "button", "label": "Send"])
        }
        lock.withLock { elements = map; roles = roleMap; sendFieldID = sendMap; clickIDs = []; backID = nil }
        return ["app": app.localizedName ?? "", "elements": out]
    }

    /// Depth-first search for an element by its AXIdentifier.
    private func find(identifier: String, in root: AXUIElement, budget: inout Int, depth: Int = 0) -> AXUIElement? {
        guard budget > 0, depth < 14 else { return nil }
        budget -= 1
        if string(root, "AXIdentifier") == identifier { return root }
        for child in children(root) {
            if let hit = find(identifier: identifier, in: child, budget: &budget, depth: depth + 1) { return hit }
        }
        return nil
    }

    /// Messages (macOS 26): the sidebar rows are pressable texts described as
    /// "Name, last message, time", the message box is `messageBodyField`, and there is no
    /// Send control (Return sends). The hub wants plain labels: "Andrew", "Message Andrew", "Send".
    private func messagesScreen(app: NSRunningApplication, appEl: AXUIElement) -> [String: Any]? {
        guard let window = (attr(appEl, kAXFocusedWindowAttribute) ?? attr(appEl, kAXMainWindowAttribute)
            ?? (attr(appEl, kAXWindowsAttribute) as? [AXUIElement])?.first as AnyObject?) else { return nil }
        let win = window as! AXUIElement
        // Just after Messages is opened its window is still being built: give it a moment.
        var found: AXUIElement?
        for attempt in 0..<6 {
            var budget = 800
            found = find(identifier: "ConversationList", in: win, budget: &budget)
            if found != nil { break }
            if attempt < 5 { usleep(300_000) }
        }
        guard let list = found else { return nil }
        var budget2 = 1500
        var field = find(identifier: "messageBodyField", in: win, budget: &budget2)
        // Right after a chat is opened Messages takes a moment to show its message box.
        var waits = 0
        while field == nil, waits < 6, lock.withLock({ Date() < chatViewUntil }) {
            usleep(300_000)
            waits += 1
            var b = 1500
            field = find(identifier: "messageBodyField", in: win, budget: &b)
        }

        // The chat that is open is the row that was just pressed. The window title can lag behind
        // or show the contact's old name after a rename, so it is only the fallback.
        let title = string(win, kAXTitleAttribute)
        let chatName = lock.withLock({ pressedChat }) ?? title
        if let title, let chatName, title != chatName { print("[sidecar] Messages window title is '\(title)', using pressed row '\(chatName)'") }
        var out: [[String: Any]] = []
        var map: [Int: AXUIElement] = [:]
        var roleMap: [Int: String] = [:]
        var sendMap: [Int: AXUIElement] = [:]
        var clicks = Set<Int>()
        var back: Int?
        var id = 0
        var names: [Int: String] = [:]

        // Two views, the same shapes as the hub's rehearsal Mac (hub/sidecar.py FakeMac):
        //  - list view: one button per chat. No message box, so nothing can be typed to whoever
        //    happened to be open; the first step is always "press <name>".
        //  - chat view (for 90 s after a name is pressed): Back, "Message <name>", Send.
        let inChat = field != nil && lock.withLock({ Date() < chatViewUntil })
        if inChat, let field {
            back = 1
            out.append(["id": 1, "role": "button", "label": "Back to conversations"])
            map[2] = field
            roleMap[2] = "textfield"
            // If the other person wrote last, say so in the box's label: the hub passes this label
            // to Bonsai when it drafts the message, so the draft can answer what they asked.
            var boxLabel = "Message \(chatName ?? "")".trimmingCharacters(in: .whitespaces)
            if let incoming = lastReceivedText(in: win), !incoming.lowercased().contains("search") {
                boxLabel += ": your answer to \(chatName ?? "them"), who asked you '\(incoming)'"
            }
            out.append(["id": 2, "role": "textfield", "label": boxLabel,
                        "value": (attr(field, kAXValueAttribute) as? String) ?? ""])
            sendMap[3] = field
            out.append(["id": 3, "role": "button", "label": "Send"])
        } else {
            var seen = Set<String>()
            // Up to 30 chats. The hub shows Bonsai the first dozen plus whoever the friend named,
            // so someone further down the list can still be found.
            for row in children(list).prefix(80) where out.count < 30 {
                guard let desc = string(row, kAXDescriptionAttribute) ?? string(row, kAXTitleAttribute) else { continue }
                // "Andrew, come back, 10:50 PM" / "Andrew, Pinned" -> "Andrew"
                let name = desc.components(separatedBy: ", ").first?.trimmingCharacters(in: .whitespaces) ?? ""
                guard !name.isEmpty, !seen.contains(name) else { continue }
                seen.insert(name)
                id += 1
                map[id] = row
                // AXPress on a sidebar row adds it to a multi-selection ("2 Conversations Selected")
                // instead of opening it, so rows are opened with a real click.
                clicks.insert(id)
                roleMap[id] = "button"
                names[id] = name
                out.append(["id": id, "role": "button", "label": name])
            }
        }
        lock.withLock { rowNames = names }
        lock.withLock { elements = map; roles = roleMap; sendFieldID = sendMap; clickIDs = clicks; backID = back }
        return ["app": "Messages", "elements": out]
    }

    /// The newest bubble in the open chat, if the other person sent it (so it is unanswered).
    /// Bubbles are groups described "Your iMessage, <text>, <time>" or "<Sender>, <text>, <time>".
    private func lastReceivedText(in win: AXUIElement) -> String? {
        var budget = 1200
        guard let transcript = find(identifier: "TranscriptCollectionView", in: win, budget: &budget) else { return nil }
        for group in children(transcript).reversed().prefix(12) {
            var b = 40
            guard let bubble = find(identifier: "CKBalloonTextView", in: group, budget: &b),
                  var text = string(bubble, kAXValueAttribute) else { continue }
            let desc = string(group, kAXDescriptionAttribute) ?? ""
            if desc.hasPrefix("Your ") { return nil }   // we wrote last: nothing to answer
            text = text.replacingOccurrences(of: "\"", with: "'")
            if text.count > 90 { text = String(text.prefix(87)) + "..." }
            return text
        }
        return nil
    }

    /// Names of the chats in the Messages sidebar (if Messages is running), for name correction.
    func messagesContactNames() -> [String] {
        guard Hands.trusted, let app = NSWorkspace.shared.runningApplications.first(where: {
            $0.bundleIdentifier == "com.apple.MobileSMS" }) else { return [] }
        let appEl = AXUIElementCreateApplication(app.processIdentifier)
        AXUIElementSetMessagingTimeout(appEl, 1.0)
        guard let win = (attr(appEl, kAXMainWindowAttribute) as! AXUIElement?)
            ?? (attr(appEl, kAXWindowsAttribute) as? [AXUIElement])?.first else { return [] }
        var budget = 800
        guard let list = find(identifier: "ConversationList", in: win, budget: &budget) else { return [] }
        return children(list).prefix(60).compactMap { row in
            (string(row, kAXDescriptionAttribute) ?? string(row, kAXTitleAttribute))?
                .components(separatedBy: ", ").first?.trimmingCharacters(in: .whitespaces)
        }
    }

    /// Texts that have come in and not been read or answered, for the hub's "X texted you".
    /// Two sources: sidebar rows marked Unread ("Name, Unread, preview, time"), and the newest
    /// bubble of the open chat when the other person wrote it.
    func unansweredTexts() -> [(from: String, text: String)] {
        guard Hands.trusted, let app = NSWorkspace.shared.runningApplications.first(where: {
            $0.bundleIdentifier == "com.apple.MobileSMS" }) else { return [] }
        let appEl = AXUIElementCreateApplication(app.processIdentifier)
        AXUIElementSetMessagingTimeout(appEl, 1.0)
        guard let win = (attr(appEl, kAXMainWindowAttribute) as! AXUIElement?)
            ?? (attr(appEl, kAXWindowsAttribute) as? [AXUIElement])?.first else { return [] }
        var out: [(from: String, text: String)] = []
        var budget = 800
        if let list = find(identifier: "ConversationList", in: win, budget: &budget) {
            for row in children(list).prefix(40) {
                guard let desc = string(row, kAXDescriptionAttribute) else { continue }
                let parts = desc.components(separatedBy: ", ")
                guard parts.count >= 4, let flag = parts.firstIndex(of: "Unread"), flag >= 1,
                      flag + 1 < parts.count - 1 else { continue }
                let preview = parts[(flag + 1)..<(parts.count - 1)].joined(separator: ", ")
                if !preview.isEmpty { out.append((parts[0].trimmingCharacters(in: .whitespaces), preview)) }
            }
        }
        if let name = string(win, kAXTitleAttribute), let text = lastReceivedText(in: win),
           !out.contains(where: { $0.from == name }) {
            out.append((name, text))
        }
        return out
    }

    // MARK: /act

    func act(_ action: [String: Any]) async -> [String: Any] {
        let type = action["type"] as? String ?? ""
        if type == "open_app" {
            guard let name = action["name"] as? String, !name.isEmpty else { return err("missing name") }
            return await openApp(name)
        }
        if type == "open_url" {
            // A web search or page the user chose from a tile: opened in Safari, like a click would.
            guard let text = action["url"] as? String, let url = URL(string: text),
                  let scheme = url.scheme?.lowercased(), scheme == "https" || scheme == "http" else {
                return err("open_url needs an http(s) url")
            }
            let safari = URL(fileURLWithPath: "/Applications/Safari.app")
            let config = NSWorkspace.OpenConfiguration()
            config.activates = true
            do {
                let app = try await NSWorkspace.shared.open([url], withApplicationAt: safari, configuration: config)
                lock.withLock { lastTarget = app }
            } catch {
                return err("could not open Safari: \(error.localizedDescription)")
            }
            try? await Task.sleep(nanoseconds: 2_500_000_000)   // let the page load before it is read
            return ["ok": true]
        }
        guard type == "press" || type == "type" else { return err("unknown type '\(type)'") }
        guard Hands.trusted else { return err(Hands.permissionError) }
        let id = (action["id"] as? Int) ?? Int(action["id"] as? String ?? "") ?? -1
        if type == "press", lock.withLock({ backID == id }) {
            lock.withLock { chatViewUntil = .distantPast; pressedChat = nil }
            return ["ok": true]
        }
        if type == "press", let field = lock.withLock({ sendFieldID[id] }) {
            let result = await pressReturn(in: field)
            if result["ok"] as? Bool == true { lock.withLock { chatViewUntil = .distantPast } }
            return result
        }
        guard let el = lock.withLock({ elements[id] }) else {
            return err("unknown id \(id): call /screen first, ids only last until the next /screen")
        }
        if type == "press", lock.withLock({ clickIDs.contains(id) }) {
            let result = await clickElement(el)
            if result["ok"] as? Bool == true { lock.withLock { chatViewUntil = Date().addingTimeInterval(90); pressedChat = rowNames[id] } }
            return result
        }
        if type == "press" { return press(el) }
        guard let text = action["text"] as? String else { return err("missing text") }
        return await typeText(text, into: el)
    }

    private func err(_ message: String) -> [String: Any] { ["ok": false, "error": message] }

    private func press(_ el: AXUIElement) -> [String: Any] {
        let result = AXUIElementPerformAction(el, kAXPressAction as CFString)
        if result == .success { return ["ok": true] }
        if result == .invalidUIElement { return err("element is gone: call /screen again") }
        // Some web / Electron controls don't implement AXPress: click its centre instead.
        if let point = center(of: el) {
            click(at: point)
            return ["ok": true]
        }
        return err("press failed (AXError \(result.rawValue))")
    }

    private func typeText(_ text: String, into el: AXUIElement) async -> [String: Any] {
        // Never presses Enter: sending is a separate, confirmed press on Send.
        let clean = text.replacingOccurrences(of: "\r", with: " ").replacingOccurrences(of: "\n", with: " ")
        let focusResult = AXUIElementSetAttributeValue(el, kAXFocusedAttribute as CFString, kCFBooleanTrue)
        if focusResult == .invalidUIElement { return err("element is gone: call /screen again") }

        var settable: DarwinBoolean = false
        AXUIElementIsAttributeSettable(el, kAXValueAttribute as CFString, &settable)
        if settable.boolValue,
           AXUIElementSetAttributeValue(el, kAXValueAttribute as CFString, clean as CFString) == .success {
            try? await Task.sleep(nanoseconds: 150_000_000)
            if (attr(el, kAXValueAttribute) as? String)?.contains(clean) == true { return ["ok": true] }
        }

        // The value didn't stick (common in web and Electron apps): focus by click, then type keys.
        // Keystrokes go to the frontmost app, so bring the target app forward first.
        var pid: pid_t = 0
        if AXUIElementGetPid(el, &pid) == .success, let app = NSRunningApplication(processIdentifier: pid), !app.isActive {
            app.activate()
            try? await Task.sleep(nanoseconds: 300_000_000)
        }
        if focusResult != .success, let point = center(of: el) {
            click(at: point)
            try? await Task.sleep(nanoseconds: 200_000_000)
        }
        selectAll()
        keystrokes(clean)
        return ["ok": true]
    }

    /// Bring the element's app forward and click the element's centre.
    private func clickElement(_ el: AXUIElement) async -> [String: Any] {
        var pid: pid_t = 0
        guard AXUIElementGetPid(el, &pid) == .success, let app = NSRunningApplication(processIdentifier: pid) else {
            return err("element is gone: call /screen again")
        }
        if !app.isActive {
            app.activate()
            try? await Task.sleep(nanoseconds: 350_000_000)
        }
        AXUIElementPerformAction(el, "AXScrollToVisible" as CFString)
        guard let point = center(of: el) else { return err("element is gone: call /screen again") }
        click(at: point)
        return ["ok": true]
    }

    /// The confirmed "Send": focus the message field and press Return in its app.
    private func pressReturn(in field: AXUIElement) async -> [String: Any] {
        guard ((attr(field, kAXValueAttribute) as? String) ?? "").isEmpty == false else {
            return err("nothing to send")
        }
        var pid: pid_t = 0
        guard AXUIElementGetPid(field, &pid) == .success, let app = NSRunningApplication(processIdentifier: pid) else {
            return err("element is gone: call /screen again")
        }
        if !app.isActive {
            app.activate()
            try? await Task.sleep(nanoseconds: 300_000_000)
        }
        AXUIElementSetAttributeValue(field, kAXFocusedAttribute as CFString, kCFBooleanTrue)
        try? await Task.sleep(nanoseconds: 100_000_000)
        let source = CGEventSource(stateID: .hidSystemState)
        for down in [true, false] {
            CGEvent(keyboardEventSource: source, virtualKey: 36, keyDown: down)?.postToPid(pid)
            usleep(30_000)
        }
        return ["ok": true]
    }

    /// Select everything in the focused field so typed text replaces it.
    private func selectAll() {
        let source = CGEventSource(stateID: .hidSystemState)
        for down in [true, false] {
            let event = CGEvent(keyboardEventSource: source, virtualKey: 0, keyDown: down) // "A"
            event?.flags = .maskCommand
            event?.post(tap: .cghidEventTap)
            usleep(30_000)
        }
    }

    private func center(of el: AXUIElement) -> CGPoint? {
        guard let p = attr(el, kAXPositionAttribute), let s = attr(el, kAXSizeAttribute) else { return nil }
        var point = CGPoint.zero, size = CGSize.zero
        guard AXValueGetValue(p as! AXValue, .cgPoint, &point), AXValueGetValue(s as! AXValue, .cgSize, &size),
              size.width > 0, size.height > 0 else { return nil }
        return CGPoint(x: point.x + size.width / 2, y: point.y + size.height / 2)
    }

    private func click(at point: CGPoint) {
        let source = CGEventSource(stateID: .hidSystemState)
        for type in [CGEventType.leftMouseDown, .leftMouseUp] {
            CGEvent(mouseEventSource: source, mouseType: type, mouseCursorPosition: point, mouseButton: .left)?
                .post(tap: .cghidEventTap)
            usleep(40_000)
        }
    }

    /// Type text as unicode key events (layout independent). No Return key is ever sent.
    private func keystrokes(_ text: String) {
        let source = CGEventSource(stateID: .hidSystemState)
        for ch in text {
            let units = Array(String(ch).utf16)
            for down in [true, false] {
                guard let event = CGEvent(keyboardEventSource: source, virtualKey: 0, keyDown: down) else { continue }
                event.keyboardSetUnicodeString(stringLength: units.count, unicodeString: units)
                event.post(tap: .cghidEventTap)
            }
            usleep(8_000)
        }
    }

    private func openApp(_ name: String) async -> [String: Any] {
        if let running = runningApp(named: name) {
            lock.withLock { lastTarget = running }
            running.activate(options: [.activateAllWindows])
            try? await Task.sleep(nanoseconds: 500_000_000)
            return ["ok": true]
        }
        let dirs = ["/Applications", "/System/Applications", "/System/Applications/Utilities",
                    "/Applications/Utilities", NSHomeDirectory() + "/Applications"]
        var url: URL?
        for dir in dirs {
            let items = (try? FileManager.default.contentsOfDirectory(atPath: dir)) ?? []
            if let match = items.first(where: { $0.lowercased() == name.lowercased() + ".app" }) {
                url = URL(fileURLWithPath: dir).appending(path: match)
                break
            }
        }
        guard let url else { return err("app '\(name)' not found") }
        let config = NSWorkspace.OpenConfiguration()
        config.activates = true
        do {
            let opened = try await NSWorkspace.shared.openApplication(at: url, configuration: config)
            lock.withLock { lastTarget = opened }
            try? await Task.sleep(nanoseconds: 500_000_000)
            return ["ok": true]
        } catch {
            return err("could not open \(name): \(error.localizedDescription)")
        }
    }

    // MARK: Debug

    /// Raw accessibility tree of an app, for working out how a new app exposes its controls.
    func dump(appName: String?, maxDepth: Int = 14) -> String {
        guard Hands.trusted else { return Hands.permissionError }
        guard let app = (appName.flatMap { runningApp(named: $0) }) ?? targetApp() else { return "no app" }
        let appEl = AXUIElementCreateApplication(app.processIdentifier)
        AXUIElementSetMessagingTimeout(appEl, 1.0)
        var lines = ["app: \(app.localizedName ?? "?") (\(app.bundleIdentifier ?? "?"))"]
        var count = 0
        func short(_ s: String?) -> String? { s.map { $0.count > 60 ? String($0.prefix(57)) + "..." : $0 } }
        func walk(_ el: AXUIElement, _ depth: Int) {
            guard depth <= maxDepth, count < 1500 else { return }
            count += 1
            var parts = [String(repeating: "  ", count: depth) + (string(el, kAXRoleAttribute) ?? "?")]
            if let v = string(el, kAXSubroleAttribute) { parts.append("sub=\(v)") }
            if let v = short(string(el, kAXTitleAttribute)) { parts.append("title=\"\(v)\"") }
            if let v = short(string(el, kAXDescriptionAttribute)) { parts.append("desc=\"\(v)\"") }
            if let v = short(string(el, kAXPlaceholderValueAttribute)) { parts.append("placeholder=\"\(v)\"") }
            if let v = short(string(el, kAXValueAttribute)) { parts.append("value=\"\(v)\"") }
            if let v = short(string(el, "AXIdentifier")) { parts.append("id=\(v)") }
            let acts = actions(el).filter { $0.hasPrefix("AX") }
            if !acts.isEmpty { parts.append("actions=\(acts.joined(separator: ","))") }
            lines.append(parts.joined(separator: " "))
            for child in children(el) { walk(child, depth + 1) }
        }
        for w in (attr(appEl, kAXWindowsAttribute) as? [AXUIElement]) ?? [] { walk(w, 0) }
        return lines.joined(separator: "\n")
    }
}
