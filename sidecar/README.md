# Sidecar (partner, Swift, Mac only)

A small Swift command-line server on `http://127.0.0.1:8790` that gives the Python hub ears, a voice, and hands. The exact JSON is in `../SPEC.md`.

## Run it

```bash
cd sidecar
swift build -c release          # first build ~2 min
.build/release/NodSidecar       # serves http://127.0.0.1:8790, posts to the hub on :8000
```

`../start.sh` starts Bonsai, this sidecar and the hub together; `../stop.sh` stops them.

How listening works: an energy gate (noise floor = 10th percentile of the last 5 s, so it
adapts to the room) finds each sentence. While the friend talks, the small model re-transcribes
the sentence so far for live captions; when they pause, the large model writes the final.
Two safety nets cover loud rooms: a caption that stops changing for 1.5 s ends the sentence,
and sound with no words in it is dropped after 2 s.

First run needs the network once to download models into `~/Documents/huggingface/models`
(TTS 1.7B, Whisper large-v3 turbo, Whisper small.en). After that it loads only from that
folder and works with Wi-Fi off. The terminal you run it from needs **Microphone** and
**Accessibility** permission (System Settings > Privacy & Security).

Settings (environment variables, all optional):

| Variable | Default | Meaning |
|---|---|---|
| `NOD_PORT` | `8790` | Port to serve on |
| `NOD_HUB_URL` | `http://127.0.0.1:8000/heard` | Where transcripts are posted |
| `NOD_TTS_MODEL` | `1.7b` | `1.7b` follows the style line in `hub/profile.json`; `0.6b` ignores it |
| `NOD_TTS_SPEAKER` | `aiden` | TTSKit voice (`ryan`, `aiden`, `eric`, `dylan`, ...) |
| `NOD_TTS_STYLE` | `voice` from `hub/profile.json` | Style instruction for the 1.7B model |
| `NOD_WHISPER_MODEL` | `large-v3-v20240930_626MB` | Model for final sentences |
| `NOD_WHISPER_FAST` | `small.en` | Model for live partial captions (`""` = use one model) |
| `NOD_END_SILENCE` | `0.7` | Seconds of pause that end a sentence |
| `NOD_VAD_DB` | `9` | dB above room noise that counts as speech (raise in a loud room) |
| `NOD_EARS` | `1` | `0` turns the microphone off |
| `NOD_AUDIO_FILE` | unset | Test only: feed a wav file instead of the microphone |
| `NOD_DEBUG` | `0` | `1` logs transcription timings |
| `NOD_OVERLAY` | `0` | `1` opens the floating, click-through tile window (loads the hub page with `?overlay=1`) |
| `NOD_OVERLAY_HEIGHT` | `0.34` | Share of the screen height the overlay covers, from the bottom |
| `NOD_IGNORE_APPS` | `Terminal,iTerm2,Claude,Xcode,loginwindow` | Apps `/screen` never treats as the app to control |

Files: `Sidecar.swift` (entry point, server and routes), `Voice.swift` (TTSKit and the mute gate),
`Ears.swift` (mic, WhisperKit, `/heard`), `Hands.swift` (`/screen`, `/act`), `Overlay.swift` (floating tile window).

`/screen` and `/act` target the frontmost app that is not Blink itself (the overlay, the terminal, or a
browser tab titled "Blink"). `curl "localhost:8790/axdump?app=Messages"` prints an app's raw
accessibility tree, for working out how a new app exposes its controls.

Name correction (`Names.swift`): transcripts are checked against real names (the `people` in
`hub/profile.json` plus the chats in the Messages sidebar, refreshed every 20 s). A capitalised word
or pair that sounds like a known name but is spelled differently is replaced with the real one
("Kayden Ron" becomes "Caden Ruan"), so Bonsai looks for a contact that exists.
`curl -G localhost:8790/names --data-urlencode "text=tell Kayden Ron hi"` shows what a line becomes.

Messages is read in a purpose-built way (`messagesScreen` in `Hands.swift`) with the same two
views as the hub's `FakeMac`. List view: one button per sidebar chat labeled with just the name, and
no message box, so nothing can be typed to whoever happened to be open. Chat view (for 90 s after a
name is pressed, until Send or Back): `Back to conversations`, the message box as
`Message <chat name>` with its `value`, and a synthetic `Send` that presses Return. Sidebar rows are opened with a real mouse click, because AXPress
on them multi-selects instead. `POST /restart` (dev only) relaunches a rebuilt binary while keeping
the terminal's permissions.

## What it does

1. **Ears (WhisperKit):** stream the mic, and `POST http://127.0.0.1:8000/heard` with partial (`final: false`) and finished (`final: true`) sentences.
2. **Voice (TTSKit):** `POST /speak {text}` speaks with the character's voice and only returns after playback ends.
3. **Mute rule:** while speaking, plus 400 ms after, do not send anything to `/heard`.
4. **Screen (accessibility API):** `GET /screen` lists pressable / typeable elements of the frontmost app.
5. **Hands (accessibility API):** `POST /act` does `press`, `type`, or `open_app`.

## Build order (stop at each check)

1. Run Argmax's CLI demos once (`argmax-cli` / `whisperkit-cli` transcribe and `tts --play`). **Check:** both work offline.
2. Swift package with `ArgmaxOSS` (or the Pro SDK, using the key from the organizer) plus a tiny HTTP server. Hummingbird or Swifter are fine; pick whichever builds first.
3. `/speak` with TTSKit. Try voice cloning from a short sample; if it is not exposed, use the 1.7B model with a style instruction from `hub/profile.json` ("voice"). **Check:** `curl -X POST localhost:8790/speak -d '{"text":"hello"}' -H 'content-type: application/json'` talks.
4. Mic streaming with WhisperKit to `/heard`, with the mute rule. **Check:** the hub UI shows what you say. This finishes Talk mode (Gate 1).
5. `/screen` and `/act` with `AXUIElementCreateApplication(frontmostApp.pid)`, walking children for roles AXButton, AXTextField, AXTextArea, AXLink, AXMenuItem. Label = AXTitle, else AXDescription, else AXPlaceholderValue. Keep an id -> AXUIElement map until the next `/screen`. `press` = `AXUIElementPerformAction(el, kAXPressAction)`. `type` = set focused, then set `kAXValueAttribute` (fallback: CGEvent keystrokes). `open_app` = `NSWorkspace.shared.launchApplication`.
   The terminal running the sidecar needs Accessibility permission (System Settings > Privacy & Security > Accessibility).

## Prompt to start a coding session

> Read ../SPEC.md and sidecar/README.md. Build the Swift sidecar in this folder, one step at a time, in the build order listed. After each step, give me the exact command to test it and stop. Use Argmax's open-source Swift SDK for speech. No cloud calls.

## Tips

- Electron apps like Discord and Slack sometimes expose a thin accessibility tree. If Discord's is empty, test Messages first; it is native and exposes everything.
- Print the `/screen` JSON for each app you plan to demo before the hub uses it.
