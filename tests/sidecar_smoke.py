"""Runs the real Swift sidecar with a recording instead of the microphone and with speech
silenced, next to a stand-in hub, and checks the contract in SPEC.md. Needs the models on
disk (run the sidecar once with Wi-Fi on) and takes about a minute. Makes no sound.

Run: .venv/bin/python tests/sidecar_smoke.py      (or ./test.sh --all)
"""
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BIN = ROOT / "sidecar/.build/release/NodSidecar"
PORT, HUB_PORT = 8793, 8794
HEARD = []          # (seconds since start, final, text)
T0 = time.time()


class Hub(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        HEARD.append((time.time() - T0, body["final"], body["text"]))
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok":true}')

    def log_message(self, *a):
        pass


def call(path, body=None, method=None):
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", method=method or ("POST" if body is not None else "GET"),
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def main():
    if not BIN.exists():
        print("skip: sidecar is not built (cd sidecar && swift build -c release)")
        return 0
    threading.Thread(target=HTTPServer(("127.0.0.1", HUB_PORT), Hub).serve_forever, daemon=True).start()
    env = dict(os.environ, NOD_PORT=str(PORT), NOD_SILENT="1", NOD_HUB_URL=f"http://127.0.0.1:{HUB_PORT}/heard",
               NOD_AUDIO_FILE=str(ROOT / "tests/fixtures/friend.wav"), NOD_OVERLAY="0", NOD_WATCH_MESSAGES="0")
    log = open("/tmp/nod_sidecar_smoke.log", "w")
    proc = subprocess.Popen([str(BIN)], cwd=ROOT / "sidecar", env=env, stdout=log, stderr=log)
    failed = []

    def check(name, cond, detail=""):
        print(("ok   " if cond else "FAIL ") + name + ("" if cond else f"  {detail}"))
        if not cond:
            failed.append(name)

    try:
        for _ in range(240):
            try:
                if call("/health")[1].get("ok"):
                    break
            except Exception:
                time.sleep(1)
        else:
            print("FAIL sidecar did not start; see /tmp/nod_sidecar_smoke.log")
            return 1
        check("health", True)

        time.sleep(14)   # the recording: two sentences with a pause between them
        finals = [t for _, f, t in HEARD if f]
        partials = [t for _, f, t in HEARD if not f]
        check("live captions arrive before the final", len(partials) >= 2, partials)
        check("one final per sentence", len(finals) == 2, finals)
        check("first sentence heard right", finals[:1] == ["Hey, want to watch the Yankees tonight?"], finals)
        check("second sentence is whole", len(finals) > 1 and "pasta" in finals[1].lower(), finals)

        n = len(HEARD)
        t = time.time()
        status, body = call("/speak", {"text": "Thai, but no meat for me."})
        took = time.time() - t
        check("speak returns ok only after the speech would have ended", status == 200 and body == {"ok": True} and took > 1.0, (status, body, took))
        check("nothing was sent to the hub while speaking", len(HEARD) == n, HEARD[n:])
        check("speak without text is refused", call("/speak", {})[1].get("ok") is False)

        status, body = call("/act", {"type": "press", "id": 99})
        check("a bad id is an error, not a crash", status == 200 and body.get("ok") is False and "error" in body, body)
        check("an unknown action type is an error", call("/act", {"type": "dance"})[1].get("ok") is False)
        check("open_app needs a name", call("/act", {"type": "open_app"})[1].get("ok") is False)

        screen = call("/screen")[1]
        check("screen answers with app and elements", "elements" in screen and "app" in screen, screen)
        st = call("/status")[1]
        check("status reports silent mode and the microphone", st.get("silent") is True and "ears" in st, st)
        if not st.get("accessibility"):
            print("     (no Accessibility permission here, so reading real apps was not exercised)")
        check("quiet can be switched off and on", call("/quiet?on=0", {})[1].get("silent") is False
              and call("/quiet?on=1", {})[1].get("silent") is True)
        names = call("/names?text=can+you+tell+Andru+to+come+back")[1]
        check("a misheard name is corrected", names.get("corrected") == "can you tell Andrew to come back", names)
        names = call("/names?text=I+was+alone+at+the+game")[1]
        check("ordinary words are left alone", names.get("corrected") == "I was alone at the game", names)
        check("still alive at the end", call("/health")[1].get("ok") is True)
    finally:
        proc.terminate()
        proc.wait(timeout=10)
    print("SIDECAR SMOKE TEST " + ("FAILED" if failed else "PASSED"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
