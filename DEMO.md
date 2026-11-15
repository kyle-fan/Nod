# Demo script

Two versions: a 2-minute video and a 4 to 5 minute live demo. Both use the same beats.

## Before you start

```bash
NOD_OVERLAY=1 USE_CAMERA=1 ./start.sh     # from Terminal
./doctor.sh                               # everything should say OK, including "Andrew has a chat"
```

- Sit neutral while it starts. If gestures drift later: `curl -X POST localhost:8000/recalibrate`.
- Sound on the MacBook's own microphone and speakers, not headphones. Volume up.
- Messages open, with a chat for the person you will text near the top. Rename a safe contact to **Andrew** so the line reads well.
- For the "put the game on" beat: open **http://127.0.0.1:8000/demo** in Safari beforehand. It is a stand-in sports page served from the Mac (a "Yankees vs Red Sox - Watch Live" link, then a player with Play), so this beat works with Wi-Fi off. Real sports sites can't load without a network.
- Do Not Disturb on. Mac on power.
- Turn Wi-Fi off last, on camera.
- Rehearse the texting chain until it is boring. `./test.sh` before you record.

Keep the Chrome tab (http://127.0.0.1:8000) open on a second desktop: the keyboard works there if a gesture misfires (`N`, arrows, `Space`, `1`-`5`).

On the camera, opening the mouth means "my turn" and a nod means back (swapped Sunday morning; `NOD_SWAP=0` undoes it). The screen keeps the normal Nod wording.

## The demo guide (scripted lines)

For the demo, the lines below are guided by `prompts/demo.json`: Bonsai is told which three options to give for them and the hub keeps those three, in that order. Anything else anyone says is still left to Bonsai, and so are "More...", "Other answers", the computer steps and who a text goes to. `DEMO_GUIDE=0 ./start.sh` (or deleting the file) turns it off. Edit the file to change a wording; no restart needed.

| Say or send this (wording can vary, the key words matter) | The three tiles |
|---|---|
| "Do you want to get **lunch**?" / "Are you **hungry**?" | yeah, I'm starving / sure, where were you thinking / nah, I'm good for now |
| "**Thai** or **pizza**, or anything else?" | thai sounds good / pizza, honestly / either works, you pick |
| "Want to watch the **Yankees** game tonight?" | yeah, I'm in / what time does it start / nah, not tonight |
| Any request to do something ("can you text...", "can you put...") | yeah, one sec / sure, give me a minute / not right now |
| "Can you text Andrew and ask if he wants to come to **lunch**?" | want to come get lunch with us / we're getting lunch, you in / getting lunch, want to come |
| "Can you tell Andrew we're getting **thai**?" (or pizza) | we're getting thai for lunch / getting thai, want to come / thai for lunch, you in |
| "The game's about to start, can you tell Andrew to **come back**?" | game's starting, come back / game's about to start, where are you / come back, the game's on |
| "Can you **put the Yankees game on**?" | Open Safari, then the Yankees link, Play, Done (chosen by Bonsai from the screen, not scripted) |
| Text in: "are we still getting **lunch**?" | yeah, still down / can we push it a bit / nah, something came up |
| Text in: "**where** do you want to **eat**?" | thai sounds good / pizza works / you pick, anything but seafood |
| Text in: "**what time** is **lunch**?" | whenever you're ready / give me ten minutes / what time works for you |
| Text in: "**thai** or **pizza**?" | thai / pizza / either works, you pick |
| Text in: "is the **game** on yet?" | starting soon, come back / yeah, it's on / not yet |
| Text in: "want me to **bring** anything?" | yeah, grab some snacks / nah, we're good / what are you getting |
| Text in: "**on my way** back" | cool, see you soon / hurry up, it's starting / saved you a seat |

Works with any contact name. If a judge asks: these rehearsed lines are steered so the demo is repeatable; say something off-script to show Bonsai unguided.

## The beats

| # | What happens | What to say (voice-over or presenter) | Time |
|---|---|---|---|
| 1 | Turn Wi-Fi off in the menu bar | "Everything you're about to see runs on this laptop. No cloud." | 10 s |
| 2 | Friend: "Hey, want to watch the Yankees tonight?" Captions appear. Nod, tilt, smile. The Mac speaks. | "Theo can't speak clearly or use his hands. Blink hears his friend, Bonsai 2 suggests what he might say, and he picks with his face." | 25 s |
| 3 | Friend: "Thai or pizza?" Nod, pick "More...", narrow down, pick. | "If none of the three fit, it asks him one question and narrows it down." | 20 s |
| 4 | Friend: "The game's about to start, can you tell Andrew to come back?" Nod, smile: "yeah one sec". Tiles: Andrew, a wording, Send, Yes. Text appears in Messages. | "Now the friend asks him to do something. Bonsai reads what's on the screen and offers the next step. Five small movements and the text is sent. Nothing sends without a yes." | 35 s |
| 5 | A text arrives from Andrew ("where are you"): type `/text Andrew: where are you` in the Chrome tab's dev box (see the note below). Overlay: "Andrew texted you. Nod to answer." Nod, pick, Yes. | "When someone texts him, one nod answers it." | 20 s |
| 6 | Open mouth: menu. Quick phrases: "I need help". | "And some things shouldn't wait for a model." | 10 s |
| 7 | Show the Wi-Fi icon still off, and the doctor or the test run. | "Bonsai 2 for prediction, Argmax for hearing and speaking, the Mac's accessibility API for hands. All local." | 10 s |

Optional beat, after 4: friend says "can you put the Yankees game on". Nod, "sure thing", Open Safari, the Yankees link, Play, then Done (it comes up first once the game is playing). Not yet run on the real Mac, so rehearse it before relying on it.

For the 2-minute video, cut beat 3 or 6.

Beat 5 with a real incoming text: start with `NOD_WATCH_MESSAGES=1` and have someone text you. That watcher has not run on a real Mac yet, so try it before the demo; if it doesn't fire, the dev-box line above shows the same flow, and the answer is still really sent through Messages.

http://127.0.0.1:8000/debug shows the live gesture readings against their thresholds. Keep it on a second screen while rehearsing.

## If something goes wrong on stage

| Problem | Fix |
|---|---|
| A gesture doesn't register | Use the keyboard in the Chrome tab, or `curl -X POST localhost:8000/recalibrate` |
| Gestures misfire all the time | Tick **Classic gestures** in the Chrome tab's dev bar (last night's rules), sit neutral for two seconds |
| Stuck on a screen | Open mouth (back), or the Reset button in the Chrome tab |
| It captions people at other tables | Restart with `NOD_MIN_DB=-35` (the `/debug` page shows the level to pick), or use an external microphone close to the friend |
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
