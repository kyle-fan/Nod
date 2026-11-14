# Handoff to the Mac side (sidecar owner and their coding agent)

Written 2026-11-14 by Kyle's side. Read SPEC.md and CLAUDE.md first; this file says what changed, what's left for the Mac, and one new idea that needs sidecar work. Demo is tomorrow.

## State of the repo

`git pull` gets you four commits on top of the scaffold. Talk mode works end to end on Kyle's Windows laptop with real Bonsai 2 9B and real webcam gestures; only the sidecar is mocked.

What changed that affects you:

- **Select gesture is now a smile**, not eyebrows (SPEC.md, Gestures). Eyebrows don't read behind glasses. This is hub-side only; the sidecar isn't involved in gestures.
- **The character is Theo** (`hub/profile.json`): a CS junior with cerebral palsy, chill and low-key. If the sidecar picks a TTSKit voice, the `voice` field there describes it: warm, slightly raspy young male voice, calm pace.
- **"More..." is now driven by Bonsai** (SPEC.md, Hub states). Hub-side only.
- **No sidecar endpoint or JSON shape changed.** `/heard`, `/speak`, `/screen`, `/act` are exactly as the spec had them.

## What the Mac side needs to do

### 1. Bonsai on the Mac

Use the llama.cpp server from PrismML's Bonsai-demo, not the MLX server. The hub sends `chat_template_kwargs` and a `response_format` JSON schema, which are llama-server features, and it expects port 8080.

```bash
git clone https://github.com/PrismML-Eng/Bonsai-demo.git && cd Bonsai-demo
export BONSAI_MODEL=9B          # setup defaults to 27B
./setup.sh
BONSAI_MODEL=9B BONSAI_THINKING=0 BONSAI_CTX=8192 ./scripts/start_llama_server.sh -np 1
```

Check: `curl http://127.0.0.1:8080/health` returns ok. On Kyle's laptop a set of 3 replies takes about 1 to 2 seconds. Do the downloads while Wi-Fi is on.

### 2. Sidecar, per SPEC.md

The three things the hub depends on most:

- `POST /speak` returns `{"ok": true}` only after playback finishes. The hub waits on it before listening again.
- The mute rule: send nothing to `/heard` while TTSKit is speaking, plus 400 ms after. Otherwise Theo's own voice comes back as the friend.
- One `"final": true` per finished sentence to `POST http://127.0.0.1:8000/heard`. The hub starts preparing replies 0.8 s after each final, which is what makes the nod feel instant.

### 3. Run the hub on the Mac

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r hub/requirements.txt
python -m hub.face --measure     # first run downloads the face model: needs Wi-Fi once
USE_CAMERA=1 MOCK_SIDECAR=0 uvicorn hub.main:app --port 8000
```

- Give the terminal camera permission (System Settings, Privacy & Security, Camera).
- **Gesture thresholds are per camera and per person.** The values at the top of `hub/face.py` are tuned for Kyle's laptop webcam. Run `--measure` on the Mac with whoever will demo, wearing what they'll wear, and put the suggested values at the top of `hub/face.py`. Kyle is fine with you editing those constants (and only those) in his folder. `FLIP_PITCH` and `FLIP_ROLL` may come out different on the Mac camera.
- The detector takes its neutral pose from the first 1.5 s after the hub starts. Sit neutral when you launch it. To redo it later, press `C` in the browser tab or run `curl -X POST http://127.0.0.1:8000/recalibrate` (the overlay window has no keyboard focus, so use curl there).

### 4. Full run with Wi-Fi off

Friend speaks, nod, tilt to a reply, smile to pick, it speaks, and the hub goes back to listening without hearing itself.

## New idea: Blink as an overlay (needs sidecar work)

Kyle wants Blink to feel like an assistant that floats over whatever you're doing: a small pixel-star character with frosted-glass option tiles, sitting over the screen, instead of a browser tab. The page side is done: open `http://127.0.0.1:8000/?overlay=1` in a browser to see exactly what your window will show. The floating part can only be done natively, so it's yours. It's written up in SPEC.md under "Overlay window (proposed)". In short:

1. **A transparent, always-on-top, click-through window** that loads `http://127.0.0.1:8000/?overlay=1` in a WKWebView. Borderless `NSPanel`, non-activating so it never takes focus, floating level, visible on all Spaces, transparent background (`drawsBackground` off on the web view), `ignoresMouseEvents = true`. Full screen width, anchored to the bottom, roughly the bottom third of the screen.
2. **`/screen` and `/act` must ignore Blink's own window** and target the frontmost real app. This matters even without the overlay: if the tiles live in a browser tab, the browser is the frontmost app and computer mode would read its own UI.

With `?overlay=1` the page has a transparent background and shows only the star, the status line, and the tiles on dark glass, pinned to the bottom. Without it, the page stays a normal full tab, so the browser version keeps working as a fallback and as the dev setup.

Gestures don't need window focus (the hub reads the camera directly), so a click-through window loses nothing. The keyboard fallback only works in the browser tab.

If the overlay turns out to be more than an hour or two, skip it: demo in a browser window docked to the bottom of the screen, and still do item 2.

## Order of work

1. Sidecar speech in and out, talk mode working on the Mac with Wi-Fi off. The demo depends on this.
2. `/screen` and `/act` ignoring Blink's window, then try computer mode (hub side is scaffolded, untested against a real screen).
3. Overlay window.

## If something on the hub side looks wrong

Kyle owns `hub/`, `ui/`, `prompts/`. Message him rather than patching, except the `face.py` threshold constants mentioned above. If you need an interface change, change SPEC.md in the same commit and tell him.
