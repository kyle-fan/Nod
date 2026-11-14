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
  "app": "Discord",
  "elements": [
    { "id": 1, "role": "button", "label": "Andrew" },
    { "id": 2, "role": "textfield", "label": "Message @Andrew" },
    { "id": 3, "role": "button", "label": "Send" }
  ]
}
```
- Keep it short: at most ~40 elements, only things you can press or type into, with a non-empty label. IDs only need to be valid until the next `/screen` call.

`POST /act`
```json
{ "type": "press", "id": 3 }
{ "type": "type", "id": 2, "text": "Game's starting, come back!" }
{ "type": "open_app", "name": "Discord" }
```
- `press` = AXPress. `type` = focus the field, set its value (or type keystrokes), do NOT press Enter. Return `{"ok": true}` or `{"ok": false, "error": "..."}`.

## Gestures

| Gesture | Event | Meaning |
|---|---|---|
| Nod (down then up) | `nod` | "My turn": show reply tiles |
| Tilt head left / right | `left` / `right` | Move highlight |
| Raise eyebrows | `select` | Pick highlighted tile |
| Open mouth (hold) | `cancel` | Back. From listening: open home menu |

Keyboard fallback in the UI (for dev and as a demo safety net): `N` nod, `←` `→` move, `Space` select, `Esc` cancel.

## Hub states

```
LISTEN --nod--> THINKING --> CHOOSE --select reply--> SPEAKING --> LISTEN
LISTEN --cancel--> HOME (Talk | Use computer | Repeat last)
CHOOSE "More..." --> NARROW: Bonsai asks a short question + 3 options that fit the
                     conversation + "Something else" --> pick one --> 3 replies that do that
                     ("More..." again narrows deeper; "Something else" = 3 new options)
NARROW after 3 rounds --> safe tiles ("Hold on, let me think." | "Can you ask me as a yes or no?" | "Never mind." | Start over)
NARROW if Bonsai fails --> fixed KIND tiles (Yes | No | Ask a question | Something else)
COMPUTER: /screen -> Bonsai picks 3 next steps + "Done" -> select -> confirm if it sends -> /act -> repeat
```

## Rules

- Tiles: 3 suggestions + 1 escape tile ("More..." in talk mode, "Done" in computer mode).
- Replies under 12 words, in the character's voice, never invent facts about them.
- Anything that sends, posts, or deletes gets a spoken confirmation first ("Send this to Andrew?"), answered with Yes / No tiles.
- The hub pre-generates replies when the friend pauses (0.8 s after a final transcript) so the nod feels instant.
- No network calls except localhost. Demo runs with Wi-Fi off.
