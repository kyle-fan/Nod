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
| `NOD_SILENT` | `0` | `1` generates speech and waits as long as it would play, but makes no sound (rehearsal, tests). Also `POST /quiet?on=1` |
| `NOD_WATCH_MESSAGES` | `0` | `1` tells the hub (`POST /incoming`) when a text message arrives; `people` only for the people in `hub/profile.json` |
| `NOD_VOCAB` | `1` | `0` stops giving Whisper the names and proper nouns from `hub/profile.json` as hints |
| `NOD_MID_SENTENCE_EXTRA` | `0.6` | Extra seconds to wait when the words so far don't end a sentence |
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

1. **Ears (WhisperKit).** Listens to the microphone and posts what the friend says to `POST http://127.0.0.1:8000/heard`: live partials (`final: false`) from a small model, one final per sentence from a large one. Names and teams from the profile are given to Whisper as hints, and misheard names are corrected to real contacts.
2. **Voice (TTSKit).** `POST /speak {text}` speaks in the character's voice and returns only after playback ends. Speech starts about half a second after the request.
3. **Mute rule.** While speaking, plus 400 ms after, nothing is sent to `/heard`, so the Mac never hears itself.
4. **Screen (Accessibility API).** `GET /screen` lists what can be pressed or typed into in the app the user is working in, never Blink's own window. Messages has a purpose-built reading; web pages list their own links before browser toolbar buttons.
5. **Hands (Accessibility API).** `POST /act` does `press`, `type` (never presses Enter) and `open_app`.
6. **Overlay.** With `NOD_OVERLAY=1`, a transparent, click-through, always-on-top window shows the hub's tiles over whatever app is in front.
7. **Incoming texts.** With `NOD_WATCH_MESSAGES=1`, a new text in Messages is reported to `POST http://127.0.0.1:8000/incoming`.

The exact JSON is in `../SPEC.md`. The sidecar never makes a network call except to the hub on localhost.

## Checking it

```bash
../test.sh --all        # runs this sidecar on a recording with speech silenced, next to a stand-in hub
../doctor.sh            # with everything running: permissions, microphone, Messages, overlay
curl -s localhost:8790/status
curl -s localhost:8790/ears        # is sound arriving, and how loud
```

What has been checked on the real Mac: speech in and out, the mute rule with real speakers and microphone, reading Messages, opening a chat, typing and sending. What has only been checked on recordings or not at all is listed in `../OVERNIGHT.md`.

## Fitting a new app

- `curl "localhost:8790/axdump?app=Notes"` prints the app's raw accessibility tree: roles, labels, identifiers, actions. Start there.
- Native Apple apps expose everything. Electron apps (Discord, Slack) often expose very little; `AXManualAccessibility` is already requested, but don't count on it.
- If `AXPress` does the wrong thing (in Messages it multi-selects a chat row), mark the element for a real click instead: see `clickIDs` in `Hands.swift`.
- Keep `/screen` short and plainly labeled. Bonsai chooses better from 12 clear things than from 40 cryptic ones; the hub also shortlists long screens.
