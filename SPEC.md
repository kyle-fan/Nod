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

## Overlay window (page built; native window proposed, sidecar side)

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

Holding a tilt keeps moving the highlight, one tile every 0.9 s.

## Hub states

```
LISTEN --nod--> THINKING --> CHOOSE --select reply--> SPEAKING --> LISTEN
LISTEN --cancel--> HOME (Talk | Use computer | Spell | Repeat last)
CHOOSE "More..." --> NARROW: Bonsai asks a short question + 3 options that fit the
                     conversation + "Something else" --> pick one --> 3 replies that do that
                     ("More..." again narrows deeper; "Something else" = 3 new options)
NARROW after 3 rounds --> safe tiles ("Hold on, let me think." | "Can you ask me as a yes or no?" | "Never mind." | Spell it)
NARROW if Bonsai fails --> fixed KIND tiles (Yes | No | Ask a question | Spell it)
SPELL (from "Spell it", or the home menu "Spell") --> see "Spell mode" below
COMPUTER: /screen -> Bonsai picks 3 next steps + "Done" -> select -> /act -> /screen -> repeat
          a step that sends/posts/deletes -> Yes/No first ("No" is highlighted) -> /act -> LISTEN
          /act fails -> "That didn't work. Try another?" + new steps
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

## Spell mode (last-resort keyboard)

Context-aware abbreviation expansion, after SpeakFaster (Cai et al., NAACL 2022; Nature Communications 2024).

- Each letter is the **first letter of a word**: `iwtgh` -> "I want to go home".
- **Space** ends a word he spelled out in full: `thai sg` -> "Thai sounds good". Spelled words come first, then initials.
- Bonsai expands the letters using the conversation and profile (`prompts/spell.txt`). The JSON schema carries a regex built from the letters, so every guess fits them.
- Guesses load in the background after each letter, and typing never waits for them.
- Layout: top row = up to 3 guesses + "say as typed" (once a word is spelled out). Bottom row = `A-F` `G-L` `M-R` `S-Z` `Space` `Delete`. Picking a group shows its letters + Back.
- After each letter the highlight lands on `M-R`, so any group is at most 2 tilts away. **Nod** jumps to the first guess (smile then says it); nod again goes back to the keys.
- Cancel (open mouth) inside a letter group goes back to the keyboard row. From the keyboard row it goes to LISTEN, and the letters are kept until he says something.
- Bonsai down: no guesses. He spells whole words and picks "say as typed".

## Rules

- Tiles: 3 suggestions + 1 escape tile ("More..." in talk mode, "Done" in computer mode).
- Replies under 12 words, in the character's voice, never invent facts about them.
- Anything that sends, posts, or deletes gets a spoken confirmation first that includes the text (`Send "come back"?`), answered with Yes / No tiles. "No" starts highlighted, so one stray smile can never send something.
- The hub pre-generates replies when the friend pauses (0.8 s after a final transcript) so the nod feels instant.
- No network calls except localhost. Demo runs with Wi-Fi off.
