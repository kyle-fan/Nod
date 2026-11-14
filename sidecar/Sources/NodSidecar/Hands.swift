import AppKit
import ApplicationServices
import Foundation

/// Screen reading and acting through the macOS Accessibility API (AXUIElement).
/// GET /screen lists pressable / typeable elements; POST /act presses, types or opens an app.
final class Hands: @unchecked Sendable {
    private let lock = NSLock()
    private var elements: [Int: AXUIElement] = [:]   // valid until the next /screen
    private var roles: [Int: String] = [:]

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
        case "AXTextField", "AXTextArea", "AXComboBox", "AXSearchField": return "textfield"
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
        if text == nil, role == "textfield" { text = string(el, kAXValueAttribute) }
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
        guard let app = (appName.flatMap { runningApp(named: $0) }) ?? frontmostApp() else {
            return ["app": "", "elements": []]
        }
        let appEl = AXUIElementCreateApplication(app.processIdentifier)
        AXUIElementSetMessagingTimeout(appEl, 1.0)
        // Electron / Chromium apps only build their accessibility tree when asked.
        AXUIElementSetAttributeValue(appEl, "AXManualAccessibility" as CFString, kCFBooleanTrue)
        AXUIElementSetAttributeValue(appEl, "AXEnhancedUserInterface" as CFString, kCFBooleanTrue)

        // Windows only (focused first). The menu bar would flood the list with menu items.
        var roots: [AXUIElement] = []
        if let focused = attr(appEl, kAXFocusedWindowAttribute) { roots.append(focused as! AXUIElement) }
        for w in (attr(appEl, kAXWindowsAttribute) as? [AXUIElement]) ?? [] where !roots.contains(where: { CFEqual($0, w) }) {
            roots.append(w)
        }

        struct Found { let el: AXUIElement; let role: String; let label: String }
        var found: [Found] = []
        var seen = Set<String>()
        var visited = 0
        let deadline = Date().addingTimeInterval(timeBudget)

        func walk(_ el: AXUIElement, depth: Int) {
            guard depth <= maxDepth, visited < maxVisited, found.count < maxElements * 3, Date() < deadline else { return }
            visited += 1
            let axRole = string(el, kAXRoleAttribute) ?? ""
            var matched = false
            if let role = mapRole(el, axRole: axRole) {
                let enabled = (attr(el, kAXEnabledAttribute) as? Bool) ?? true
                if enabled, let text = label(el, role: role) {
                    let key = role + "|" + text
                    if !seen.contains(key) {
                        seen.insert(key)
                        found.append(Found(el: el, role: role, label: text))
                    }
                    matched = true
                }
            }
            // Don't descend into a matched leaf control (a button's inner label/image).
            if matched, axRole != "AXGroup", axRole != "AXCell", axRole != "AXRow" { return }
            for child in children(el) { walk(child, depth: depth + 1) }
        }
        for root in roots { walk(root, depth: 0) }

        // Cap at 40 but never drop a text field: keep fields, fill the rest in screen order.
        var keep = Set(found.indices.filter { found[$0].role == "textfield" }.prefix(maxElements))
        for i in found.indices where keep.count < maxElements { keep.insert(i) }

        var out: [[String: Any]] = []
        var map: [Int: AXUIElement] = [:]
        var roleMap: [Int: String] = [:]
        var id = 0
        for i in found.indices.sorted() where keep.contains(i) {
            id += 1
            map[id] = found[i].el
            roleMap[id] = found[i].role
            out.append(["id": id, "role": found[i].role, "label": found[i].label])
        }
        lock.withLock { elements = map; roles = roleMap }
        return ["app": app.localizedName ?? "", "elements": out]
    }

    // MARK: /act

    func act(_ action: [String: Any]) async -> [String: Any] {
        let type = action["type"] as? String ?? ""
        if type == "open_app" {
            guard let name = action["name"] as? String, !name.isEmpty else { return err("missing name") }
            return await openApp(name)
        }
        guard type == "press" || type == "type" else { return err("unknown type '\(type)'") }
        guard Hands.trusted else { return err(Hands.permissionError) }
        let id = (action["id"] as? Int) ?? Int(action["id"] as? String ?? "") ?? -1
        guard let el = lock.withLock({ elements[id] }) else {
            return err("unknown id \(id): call /screen first, ids only last until the next /screen")
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
        keystrokes(clean)
        return ["ok": true]
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
            _ = try await NSWorkspace.shared.openApplication(at: url, configuration: config)
            try? await Task.sleep(nanoseconds: 500_000_000)
            return ["ok": true]
        } catch {
            return err("could not open \(name): \(error.localizedDescription)")
        }
    }
}
