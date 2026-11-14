# Handoff to the Mac side (sidecar owner and their coding agent)

Written 2026-11-14 by Kyle's side, updated the same evening after computer mode and the UI were built. Read SPEC.md and CLAUDE.md first; this file says what changed, what's left for the Mac, and one new idea that needs sidecar work. Demo is tomorrow.

## State of the repo

`git pull` gets you four commits on top of the scaffold. Talk mode works end to end on Kyle's Windows laptop with real Bonsai 2 9B and real webcam gestures; only the sidecar is mocked.

What changed that affects you:

- **Select gesture is now a smile**, not eyebrows (SPEC.md, Gestures). Eyebrows don't read behind glasses. This is hub-side only; the sidecar isn't involved in gestures.
- **The character is Theo** (`hub/profile.json`): a CS junior with cerebral palsy, chill and low-key. If the sidecar picks a TTSKit voice, the `voice` field there describes it: warm, slightly raspy young male voice, calm pace.
- **"More..." is now driven by Bonsai** (SPEC.md, Hub states). Hub-side only.
- **Speech endpoints are unchanged** (`/heard`, `/speak`). `/screen` and `/act` gained a few details for computer mode: see "Computer mode" below and SPEC.md.

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

Note on the glass look: a web page cannot blur other apps' windows behind it, so in overlay mode the tiles are a dark tinted glass with a sheen, not a live blur. If you want real blur, an `NSVisualEffectView` behind the web view would do it, but it's optional polish.

## Computer mode: what the hub does now, and what it needs from you

The hub side is built and rehearsed against a fake Mac (`hub/sidecar.py`, `FakeMac`). With `MOCK_SIDECAR=1` you can run the whole thing with no Swift at all, which is the fastest way to see what the hub expects from the real sidecar:

```bash
USE_CAMERA=0 MOCK_SIDECAR=1 uvicorn hub.main:app --port 8000
# in the browser dev box: the game's about to start, can you tell Andrew to come back
# N, then Space on each first tile, and → then Space to confirm the send
```

The exact contract is in SPEC.md ("Hub -> Sidecar" and "Hub states"). What changed there since the scaffold:

- `/screen` elements that can be typed into carry a `"value"` (current text, `""` if empty).
- Use role `textfield` for anything typeable; everything else is treated as pressable.
- A send control needs "send" in its label, so the hub asks for confirmation.
- `open_app` names come from `hub/profile.json` `apps` (Messages, Safari).
- `/act` returning `{"ok": false, "error": "..."}` is handled: the hub says it didn't work and offers new steps.

### The two demo chains (rehearsed, first tile is correct at each step)

Measured on Kyle's laptop against the fake Mac: texting 5 of 5 full runs, game 3 of 3, about 17 s each including speech.

**A. Text Andrew.** Friend says "the game's about to start, can you tell Andrew to come back".

| Gesture | Tile picked | Hub calls |
|---|---|---|
| nod, smile | "yeah one sec" (marked "then does it on the mac") | `/speak`, then `/screen` |
| smile | press · Andrew | `/act {"type":"press","id":…}`, `/screen` |
| smile | type · "come back" | `/act {"type":"type","id":…,"text":"come back"}`, `/screen` |
| smile | press · Send | (no call yet: hub asks `Send "come back"?`, speaks it via `/speak`) |
| tilt, smile | Yes, do it | `/act {"type":"press","id":…}` then back to listening |

If Messages is not in front, the first step is "Open Messages" (`/act {"type":"open_app","name":"Messages"}`).

**B. Put the game on.** Friend says "can you put the yankees game on". Steps: "yeah one sec", Open Safari (if not in front), press the link whose label mentions the Yankees game, press Play, then Done.

For these to work on the real Mac, `/screen` has to return elements shaped like the fake ones. For Messages, the conversation list should yield one pressable element per person labeled with just their name (`"Andrew"`), and an open chat should yield a `textfield` labeled `"Message Andrew"` with its `value`, plus a `"Send"` button. Run the fake and look at `FakeMac.screen()` for the exact shapes that are known to work.

### Things about real macOS apps that will need handling (please verify, these are from memory)

- **Messages may have no Send button.** On macOS you normally send with Return. If the accessibility tree has no send control, have the sidecar add a synthetic element `{"role":"button","label":"Send"}` to `/screen` whenever the message field is non-empty or present, and implement pressing it as posting a Return key to the field. The hub only presses it after the user confirms.
- **Conversation rows are probably not buttons.** They're likely rows/cells with static text inside. Report each as one pressable element labeled with the contact name, and implement press as AXPress, or selecting the row, or a click at its center, whichever works.
- **Setting a field's value by accessibility may not register** in some apps (the Send control stays disabled, or the text vanishes). If so, focus the field and type the text as keystrokes instead.
- **Prefer Apple's apps for the demo** (Messages, Safari). Electron apps like Discord often expose little to accessibility.
- **Accessibility permission**: the sidecar binary (or Terminal/Xcode while developing) needs it in System Settings, Privacy & Security, Accessibility. Without it every AX call fails silently.
- **Keep `/screen` small and plain.** At most about 40 elements, deduplicated, skipping toolbar noise. Bonsai 2 9B gets worse with long or cryptic lists. Fewer, well-labeled elements beat completeness.
- **Frontmost app**: with the non-activating overlay, `NSWorkspace.shared.frontmostApplication` stays the real app. If the UI is in a browser tab instead, you need to track the last non-browser frontmost app and target that.
- For the Safari chain, links on a real page will be many. Consider returning only links plus the address field, capped, or pre-opening a simple page for the demo.

### Steering the demo without code changes

Everything Bonsai knows about Theo is in `hub/profile.json`, re-read on every call, no restart needed:

- `apps`: the apps "Open …" can offer.
- `computer`: his habits ("Texts his friends in the Messages app"). This is what makes Bonsai choose the right app.
- `texts`: example messages he's sent before. These steer the wording of drafted messages strongly. To make a specific demo line come out a specific way, add an example close to it here. That is prompt tuning: Bonsai still writes every tile.
- `examples` and `style`: his talking voice.

Prompts are in `prompts/`: `task.txt` (is this a request to do something), `actions.txt` (next step), `message.txt` (message wording), `reply.txt`, `intent.txt`. The hub prints `[brain] goal: … app: …` for every computer step, which is the first thing to look at when a tile is wrong.

## Order of work

1. Sidecar speech in and out, talk mode working on the Mac with Wi-Fi off. The demo depends on this.
2. `/screen` and `/act` for Messages (chain A), ignoring Blink's window. Rehearse chain A until it's boring. Then Safari (chain B) if time allows.
3. Overlay window.

## If something on the hub side looks wrong

Kyle owns `hub/`, `ui/`, `prompts/`. Message him rather than patching, except the `face.py` threshold constants mentioned above. If you need an interface change, change SPEC.md in the same commit and tell him.
