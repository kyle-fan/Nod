# Demo script

Two versions: a 2-minute video and a 4 to 5 minute live demo. Both use the same beats.

## Before you start

```bash
NOD_OVERLAY=1 USE_CAMERA=1 NOD_WATCH_MESSAGES=1 ./start.sh     # from Terminal
./doctor.sh                                                     # everything should say OK
```

- Sit neutral while it starts. If gestures drift later: `curl -X POST localhost:8000/recalibrate`.
- Sound on the MacBook's own microphone and speakers, not headphones. Volume up.
- Messages open, with a chat for the person you will text near the top. Rename a safe contact to **Andrew** so the line reads well.
- Do Not Disturb on. Mac on power.
- Turn Wi-Fi off last, on camera.
- Rehearse the texting chain until it is boring. `./test.sh` before you record.

Keep the Chrome tab (http://127.0.0.1:8000) open on a second desktop: the keyboard works there if a gesture misfires (`N`, arrows, `Space`, `1`-`5`).

## The beats

| # | What happens | What to say (voice-over or presenter) | Time |
|---|---|---|---|
| 1 | Turn Wi-Fi off in the menu bar | "Everything you're about to see runs on this laptop. No cloud." | 10 s |
| 2 | Friend: "Hey, want to watch the Yankees tonight?" Captions appear. Nod, tilt, smile. The Mac speaks. | "Theo can't speak clearly or use his hands. Blink hears his friend, Bonsai 2 suggests what he might say, and he picks with his face." | 25 s |
| 3 | Friend: "Thai or pizza?" Nod, pick "More...", narrow down, pick. | "If none of the three fit, it asks him one question and narrows it down." | 20 s |
| 4 | Friend: "The game's about to start, can you tell Andrew to come back?" Nod, smile: "yeah one sec". Tiles: Andrew, a wording, Send, Yes. Text appears in Messages. | "Now the friend asks him to do something. Bonsai reads what's on the screen and offers the next step. Five small movements and the text is sent. Nothing sends without a yes." | 35 s |
| 5 | A text arrives from Andrew ("where are you"). Overlay: "Andrew texted you. Nod to answer." Nod, pick, Yes. | "When someone texts him, one nod answers it." | 20 s |
| 6 | Open mouth: menu. Quick phrases: "I need help". | "And some things shouldn't wait for a model." | 10 s |
| 7 | Show the Wi-Fi icon still off, and the doctor or the test run. | "Bonsai 2 for prediction, Argmax for hearing and speaking, the Mac's accessibility API for hands. All local." | 10 s |

For the 2-minute video, cut beat 3 or 6.

## If something goes wrong on stage

| Problem | Fix |
|---|---|
| A gesture doesn't register | Use the keyboard in the Chrome tab, or `curl -X POST localhost:8000/recalibrate` |
| Stuck on a screen | Open mouth (back), or the Reset button in the Chrome tab |
| It doesn't hear the friend | `curl -s localhost:8790/ears` shows whether sound is arriving; check the sound input |
| Wrong tile came up first | Tilt to the right one. Or pick Done and ask again |
| A text is about to go to the wrong person | The confirmation names the person. Pick No (it is already highlighted) |
| Everything is odd | `./stop.sh && ./start.sh`, about 20 seconds |

## Rehearse anywhere, with no Mac permissions

```bash
USE_CAMERA=0 MOCK_SIDECAR=1 .venv/bin/uvicorn hub.main:app --port 8000
```

Type the friend's lines in the dev box; `/text Andrew: where are you` for beat 5. The pretend Mac has Andrew, Sam and Mom in Messages and a Yankees link in Safari. `./rehearse.sh` does the same in one command.

## Watch it play by itself

```bash
./rehearse.sh                          # terminal 1
.venv/bin/python tools/autoplay.py     # terminal 2
```

It plays beats 2, 4, 5 and 6 on the pretend Mac at a pace a viewer can follow (add `ws://127.0.0.1:8000/ws 1.5` to slow it down by half). Useful for learning the story, and for recording clean footage of the screen for the video without needing the camera to cooperate.
