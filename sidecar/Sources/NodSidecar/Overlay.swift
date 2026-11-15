import AppKit
import WebKit

/// Blink as an overlay (SPEC "Overlay window"): a transparent, always-on-top, click-through,
/// non-activating panel over the bottom of the screen showing the hub page with ?overlay=1.
/// It never takes focus, so the app the user is working in stays frontmost.
@MainActor
final class Overlay: NSObject, WKNavigationDelegate {
    private var panel: NSPanel?
    private var web: WKWebView?
    private let url: URL
    private let heightFraction: CGFloat
    private var loaded = false

    init(url: URL, heightFraction: CGFloat) {
        self.url = url
        self.heightFraction = heightFraction
    }

    func show() {
        guard let screen = NSScreen.main else { return }
        // Sit on top of the Dock, not behind it: visibleFrame excludes the Dock and menu bar.
        let full = screen.frame
        let visible = screen.visibleFrame
        let frame = NSRect(x: full.minX, y: visible.minY, width: full.width,
                           height: min(full.height * heightFraction, visible.height))

        let panel = NSPanel(contentRect: frame, styleMask: [.borderless, .nonactivatingPanel],
                            backing: .buffered, defer: false)
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hasShadow = false
        panel.ignoresMouseEvents = true          // click-through
        panel.hidesOnDeactivate = false
        panel.isFloatingPanel = true
        panel.level = .statusBar                 // above normal windows and the Dock
        panel.becomesKeyOnlyIfNeeded = true
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary, .ignoresCycle]

        let config = WKWebViewConfiguration()
        let web = WKWebView(frame: NSRect(origin: .zero, size: frame.size), configuration: config)
        web.setValue(false, forKey: "drawsBackground")   // transparent page background
        web.navigationDelegate = self
        web.autoresizingMask = [.width, .height]
        panel.contentView = web
        panel.orderFrontRegardless()

        self.panel = panel
        self.web = web
        load()
    }

    func close() {
        panel?.orderOut(nil)
        panel = nil
        web = nil
    }

    private func load() {
        web?.load(URLRequest(url: url, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: 5))
    }

    /// The hub may start after the sidecar, or be restarted: keep retrying until the page is up.
    private func retrySoon() {
        loaded = false
        Task { @MainActor in
            try? await Task.sleep(nanoseconds: 1_500_000_000)
            self.load()
        }
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        if !loaded { print("[sidecar] overlay showing \(url.absoluteString)") }
        loaded = true
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) { retrySoon() }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        retrySoon()
    }

    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) { retrySoon() }
}
