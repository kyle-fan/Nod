# Blink: spec (source of truth)

Read this before writing code. If you change an interface, update this file first and tell your partner.

## What it is

A person who can't speak or use their hands holds conversations and uses their computer with head gestures. Everything runs on one Mac with Wi-Fi off.

One loop, two modes:

```
context  ->  Bonsai predicts options  ->  tiles on screen  ->  head gesture picks one  ->  app acts
```

- **Talk mode:** context = what the friend said. Acting = speak the reply out loud.
- **Computer mode:** context = conversation so far + what's on screen. Acting = click or type in the frontmost app.

## Pieces and owners

| Piece | Folder | Owner | Runs on |
|---|---|---|---|
| Hub: state machine, Bonsai calls, websocket to UI | `hub/` | Kyle | Python, any OS for dev, Mac for demo |
| Face gestures (MediaPipe) | `hub/face.py` | Kyle | Python |
| Tile screen | `ui/index.html` | Kyle | Browser |
| Prompts | `prompts/` | Kyle | text files |
| Sidecar: WhisperKit, TTSKit, macOS accessibility | `sidecar/` | Partner | Swift, Mac only |
| Bonsai server | n/a | Partner sets up on Mac | llama-server, port 8080 |

## Ports

- Hub: `http://127.0.0.1:8000` (UI at `/`, websocket at `/ws`)
- Sidecar: `http://127.0.0.1:8790`
- Bonsai (OpenAI-compatible): `http://127.0.0.1:8080/v1/chat/completions`

## Sidecar -> Hub

`POST http://127.0.0.1:8000/heard`
```json
{ "text": "hey want to watch the yankees tonight", "final": true }
```
- Send partial results with `"final": false` (UI shows them live) and one `"final": true` per finished sentence.
- **Mute rule:** the sidecar must NOT send anything while TTSKit is speaking, plus 400 ms after it finishes. Otherwise the app hears its own voice as the friend.

`POST http://127.0.0.1:8000/incoming`  (optional; sidecar sends it when started with `NOD_WATCH_MESSAGES=1`)
```json
{ "from": "Andrew", "text": "where are you" }
```
- A text message arrived. The hub shows it, and the next nod offers 3 answers to it instead of talk replies (see Hub states). The same message reported twice counts once.

## Hub -> Sidecar

`POST /speak` `{ "text": "Thai, but no meat for me" }`
- Speak with TTSKit using the character's voice. Return `{"ok": true}` only after playback finishes (the hub waits on it).

`GET /screen`
- Returns the actionable elements of the frontmost app, from the macOS accessibility tree (AXUIElement).
```json
{
  "app": "Messages",
  "elements": [
    { "id": 1, "role": "button", "label": "Back to conversations" },
    { "id": 2, "role": "textfield", "label": "Message Andrew", "value": "" },
    { "id": 3, "role": "button", "label": "Send" }
  ]
}
```
- Optional: `GET /screen?app=Messages` reads that running app instead of the frontmost one (useful when the tile browser is in front).
- Keep it short: at most ~40 elements, only things you can press or type into, with a non-empty label. IDs only need to be valid until the next `/screen` call.
- `role`: use `textfield` for anything you can type into (the hub also accepts `textarea`, `searchfield`, `combobox`). Every other role (`button`, `link`, `row`, ...) is treated as pressable.
- `value` (typeable elements only): the text currently in the field, `""` if empty. The hub uses it to know a message has been typed (so the next step is Send) and to say what is about to be sent in the confirmation.
- Labels are what the user reads on the tile and what Bonsai reasons from, so make them plain: `"Andrew"`, `"Message Andrew"`, `"Send"`. A send control must have "send" (or "post"/"submit") in its label: that is how the hub knows to ask for confirmation.
- `app` is the app's plain name (`"Messages"`, `"Safari"`), matching the names in `hub/profile.json` `apps`.

`POST /act`
```json
{ "type": "press", "id": 3 }
{ "type": "type", "id": 2, "text": "game's starting, come back" }
{ "type": "open_app", "name": "Messages" }
```
- `press` = AXPress (fall back to a click at the element's center if AXPress isn't supported). `type` = focus the field and set its value (or type keystrokes), replacing what was there. Do NOT press Enter. Return `{"ok": true}` or `{"ok": false, "error": "..."}`.
- `open_app` names come from `hub/profile.json` `apps` (currently Messages and Safari). Bring the app to the front; `/screen` should describe it on the next call.
- The hub calls `/screen` again about 0.4 s after every successful `/act`, so the app should have updated by then.

Sidecar extras (none are needed by the hub; all are for running and checking it):

| Endpoint | What it does |
|---|---|
| `GET /health` | `{"ok": true}` once the models are loaded |
| `GET /status` | accessibility permission, microphone state, overlay, silent mode (used by `./doctor.sh`) |
| `GET /ears` | is sound arriving, how loud, noise floor |
| `POST /quiet?on=1` | rehearsal: speech is generated and timed but not played (`on=0` to undo; `NOD_SILENT=1` at start) |
| `POST /overlay?on=1` | show or hide the floating tile window while running |
| `GET /screen?app=Messages` | read a named running app instead of the frontmost one |
| `GET /axdump?app=Messages` | raw accessibility tree of an app, for fitting `/screen` to a new app |
| `GET /names?text=...` | what a transcript becomes after name correction |
| `POST /restart` | relaunch a rebuilt sidecar, keeping the terminal's permissions (dev only) |

How Messages is read: two views with the same shapes as `FakeMac` in `hub/sidecar.py`. List view is one button per chat labeled with the name, and no message box. For 90 s after a name is pressed it is the chat view: `Back to conversations`, the box `Message <name>` with its `value`, and `Send` (which presses Return, since Messages has no Send control). If the other person wrote last, the box label continues `: your answer to <name>, who asked you '<their text>'`, and the hub passes that label to the message prompt.

## Overlay window (built: `NOD_OVERLAY=1`)

Blink should float over whatever app is in use instead of living in a browser tab.

- The sidecar opens a transparent, always-on-top, non-activating, click-through window (borderless `NSPanel` + WKWebView) that loads `http://127.0.0.1:8000/?overlay=1`. Full width, anchored to the bottom, about the bottom third of the screen.
- With `?overlay=1` the page has a transparent background and shows only the character, status line, and tiles (no dev bar, no conversation panel). Without it, the page is the normal full tab.
- `/screen` and `/act` always target the frontmost app that is not Blink (not the overlay, and not a browser tab showing the hub UI).
- Fallback if the overlay isn't built: a browser window docked at the bottom of the screen.

## Gestures

| Gesture | Event | Meaning |
|---|---|---|
| Nod (down then up) | `nod` | "My turn": show reply tiles |
| Tilt head left / right | `left` / `right` | Move highlight |
| Smile (hold 0.25 s) | `select` | Pick highlighted tile. Was eyebrows: they don't read behind glasses. Switch back with `SELECT_SIGNAL` in `hub/face.py` |
| Open mouth (hold) | `cancel` | Back. From listening: open home menu |

Keyboard fallback in the UI (for dev and as a demo safety net): `N` nod, `←` `→` move, `Space` select, `Esc` cancel. `C` recalibrates the camera's neutral pose (sit still for 1.5 s); `POST /recalibrate` on the hub does the same without a focused browser.

Holding a tilt keeps moving the highlight, one tile every 1.3 s.

The rules that turn head pose into gestures are in `hub/gestures.py` (no camera code, tested in `tests/test_gestures.py`). The neutral pose follows slow drift, so slumping doesn't break nods; a nod is a dip and return measured against where the head just was; the dip that comes with a head tilt is not a nod. Thresholds stay at the top of `hub/face.py`.

Without gestures: number keys `1`-`5` or a click pick a tile directly (websocket `{"type": "pick", "index": 0}`).

## Hub states

```
LISTEN --nod--> THINKING --> CHOOSE --select reply--> SPEAKING --> LISTEN
LISTEN --cancel--> HOME (Talk | Quick phrases | Text someone | More | Back)
                   More = Use computer | Repeat last | Stop listening
QUICK PHRASES: fixed lines (profile "phrases"), 3 at a time, spoken at once. No model needed.
STOP LISTENING: everything heard is dropped until the next nod.
TEXT SOMEONE: pick a person from the profile -> 3 messages -> pick -> (auto) open Messages,
              open the chat, type -> Yes/No -> send
TEXT ARRIVES (POST /incoming): "Andrew texted you. Nod to answer" --nod--> 3 answers
              + "Other answers" + "Not now" --pick--> same auto steps -> Yes/No -> send.
              If a step isn't where expected, it hands over to the step-by-step tiles.
CHOOSE "More..." --> NARROW: Bonsai asks a short question + 3 options that fit the
                     conversation + "Something else" --> pick one --> 3 replies that do that
                     ("More..." again narrows deeper; "Something else" = 3 new options)
NARROW after 3 rounds --> safe tiles ("Hold on, let me think." | "Can you ask me as a yes or no?" | "Never mind." | Start over)
NARROW if Bonsai fails --> fixed KIND tiles (Yes | No | Ask a question | Something else)
COMPUTER: /screen -> Bonsai picks 3 next steps + "Done" -> select -> /act -> /screen -> repeat
          a step that sends/posts/deletes -> Yes/No first ("No" is highlighted) -> /act -> LISTEN
          /act fails -> "That didn't work. Try another?" + new steps
          task complete -> "That looks done" with Done as the first, highlighted tile
          a typed message can be reworded: "Write a different message" -> 3 more wordings
Tiles left untouched for 90 s are put away (TILES_TIMEOUT_S).
A sidecar or Bonsai failure never leaves the hub stuck: it says what failed and returns to LISTEN.
```

Two ways into computer mode:
- **Say, then do.** When the friend asks for something doable on the Mac ("tell Andrew to come back", "put the game on"), the first reply tile agrees ("yeah one sec") and is marked "then does it on the mac". Picking it speaks the reply, then goes straight to COMPUTER. Bonsai decides this per friend line (`prompts/task.txt`).
- **Home menu.** Open mouth from LISTEN, pick "Use computer".

How a computer step is chosen (`prompts/actions.txt`, `prompts/message.txt`):
- Bonsai first states the goal and which app it needs, from the conversation. If that app isn't in front, "Open <app>" is the first tile.
- Bonsai can only output steps that exist: press ids of pressable elements, type into ids of text fields, open an app from the profile.
- When the best step is typing a message, the 3 tiles are 3 wordings of that message.
- "Send" is not offered while every text field is empty.
- Tile text comes from the real element ("press · Andrew", "type · “come back”"), so a tile says exactly what will happen.
- Bonsai answers with short step codes ("press 3", "type 2", "open Safari") and the goal is decided once per task, which keeps a step to 1.5-2.5 s.
- Fixed rules sit around Bonsai's answer: the person the friend named out loud is the recipient; once a message is typed, Send is the first tile; a control already pressed in this task is not offered again; only message boxes get typed into.
- The first step is worked out while the spoken reply ("yeah one sec") is still playing.

Prompt files (`prompts/*.txt`) have two parts split by a line of `=====`. Above it is what never changes between calls (who he is, the rules): it is sent as the system message and llama-server keeps it cached. Below it is what changed (conversation, screen). Keep anything that varies below the line.

State sent to the UI over `/ws` now also has: `camera` (`on`, `face`, `calibrating`, `error`), `task` (`goal`, `done`) during computer mode, `incoming` (`from`, `text`), `paused`, and `stats.sidecar_ok`. History entries can be `{"who": "text", "from": ...}` and `{"who": "me_text", "to": ...}` as well as `friend` and `me`.

## Rules

- Tiles: 3 suggestions + 1 escape tile ("More..." in talk mode, "Done" in computer mode).
- Replies under 12 words, in the character's voice, never invent facts about them.
- Anything that sends, posts, or deletes gets a spoken confirmation first that includes the text and who it goes to (`Send "come back" to Andrew?`), answered with Yes / No tiles. "No" starts highlighted, so one stray smile can never send something. He can answer while the question is still being spoken.
- The replies he picks are saved in `hub/learned.json` (this Mac only, git-ignored) and the last few are shown to Bonsai as examples of his real voice. `LEARN=0` turns it off.
- The hub pre-generates replies when the friend pauses (0.8 s after a final transcript) so the nod feels instant.
- No network calls except localhost. Demo runs with Wi-Fi off.
