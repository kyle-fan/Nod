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
        // Live caption: the overlay page hides the conversation, so the user can't tell whether
        // they were heard. This adds a small line above the tiles showing what is being heard
        // and the last thing said, read from the hub's own websocket. Nothing in ui/ changes.
        let caption = WKUserScript(source: Overlay.captionScript, injectionTime: .atDocumentEnd, forMainFrameOnly: true)
        config.userContentController.addUserScript(caption)
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

    static let captionScript = """
    (function () {
      const box = document.createElement('div');
      box.style.cssText = 'position:fixed;left:28px;top:10px;max-width:72%;padding:10px 18px;border-radius:16px;' +
        'background:rgba(15,18,26,.88);color:#fff;font:600 22px -apple-system,system-ui,sans-serif;' +
        'box-shadow:0 4px 18px rgba(0,0,0,.45);opacity:0;transition:opacity .35s;z-index:9999;pointer-events:none;' +
        'white-space:nowrap;overflow:hidden;text-overflow:ellipsis';
      document.body.appendChild(box);
      let hideTimer = null, lastKey = '';
      function show(tag, text, color, keep) {
        box.innerHTML = '<span style="color:' + color + ';font-weight:700;margin-right:10px">' + tag + '</span>';
        box.appendChild(document.createTextNode(text));
        box.style.opacity = 1;
        clearTimeout(hideTimer);
        if (!keep) hideTimer = setTimeout(() => { box.style.opacity = 0; }, 9000);
      }
      function connect() {
        const ws = new WebSocket('ws://' + location.host + '/ws');
        ws.onmessage = (e) => {
          const s = JSON.parse(e.data);
          const last = (s.history || [])[s.history.length - 1];
          const key = (s.partial || '') + '|' + (s.history || []).length + '|' + (last ? last.text : '');
          if (key === lastKey) return;
          lastKey = key;
          if (s.partial) show('hearing', s.partial, '#8fd3ff', true);
          else if (last && last.who === 'friend') show('heard', last.text, '#7dffb0', false);
          else if (last) show('you said', last.text, '#ffd84a', false);
          else box.style.opacity = 0;
        };
        ws.onclose = () => setTimeout(connect, 1500);
      }
      connect();
    })();
    """

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
