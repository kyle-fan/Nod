# Overnight work, 15 Nov (branch `overnight`)

Everything here is on the local branch `overnight`. Nothing was pushed, `main` is untouched, and the state before the night is tagged `pre-overnight-2026-11-15`.

**Your `~/Nod` folder is already on `overnight`**, so `./start.sh` runs the new version as it is.

```bash
git branch --show-current       # overnight
git checkout main               # exactly what you had last night (stop Nod first: ./stop.sh)
git checkout overnight          # back to the new work
git diff main overnight --stat  # what changed
```

No sound was played and no text message was sent. One side effect on your Mac: a test run asked the sidecar to open Messages, so Messages may be open in the background.

**Changed Sunday morning at your request:** on the camera, opening your mouth now means "my turn" and a nod means back or menu. Where the steps below say nod, open your mouth, and the other way round. `NOD_SWAP=0 ./start.sh` or the **Mouth = my turn** checkbox in the dev bar puts it back.

## Try it: ten minutes, in this order

Run these from Terminal (it has the permissions).

```bash
cd ~/Nod
./test.sh                                   # 1. fast checks: should end "ALL TESTS PASSED"
NOD_OVERLAY=1 USE_CAMERA=1 ./start.sh       # 2. sit neutral while it starts
./doctor.sh                                 # 3. everything should say OK
```

| Step | What to do | What should happen | If it doesn't |
|---|---|---|---|
| 4. Gestures | Open http://127.0.0.1:8000/debug in Chrome. Nod, tilt each way, smile, open your mouth. | Each bar passes its red line and "enough" lights up; the log below shows nod, left, right, select, cancel. | Tell me which gesture and what the "last 4 s" numbers say. Tick **Classic gestures** in the Chrome tab's dev bar to switch to last night's rules on the spot (sit neutral for two seconds after), or start with `NOD_GESTURES=classic ./start.sh`. |
| 5. Talking | Say something, nod, tilt, smile. | Caption in the overlay, three replies appear one by one, the Mac speaks, and its own voice does not come back as a caption. | `curl -s localhost:8790/ears` shows whether sound is arriving. |
| 6. Texting | Messages open. Say "can you tell Caden Ruan to come back". Nod, then smile on the first tile each time; at the question, tilt to Yes and smile. | Tiles go: agree, Caden Ruan, three wordings, Send, `Send "..." to Caden Ruan?`. The text appears in Messages. | Tell me which tile was wrong; `/debug` has the log. |
| 7. A text arrives | In the Chrome tab's dev box type `/text Caden Ruan: where are you`, then nod. | "Caden Ruan texted you. Nod to answer", three answers, one pick types it, Yes sends. | Same. |
| 8. The menu | Open your mouth. Try Quick phrases and Text someone. | Phrases speak at once. | |
| 9. The game | Open http://127.0.0.1:8000/demo in Safari. Say "can you put the Yankees game on". | Open Safari, the Yankees link, Play, then "That looks done". | This one has never run on a real browser. If the tiles are wrong, send me `curl -s "localhost:8790/screen?app=Safari"`. |

To go back to last night's version at any point: `./stop.sh && git checkout main && ./start.sh`.

## What is new

### Faster

| | Before | Now |
|---|---|---|
| One computer step ("Reading the screen...") | 4 to 7 s | 1.5 to 2.5 s |
| Whole texting chain, friend's request to sent | about 45 s | about 9 s |
| First reply tile after a nod with nothing prepared | about 2 s for all three | first at 0.6 s, they appear one by one |
| Confirming a send | wait for the spoken question to finish | answer while it is still speaking |

How: prompts are split into a fixed part that llama-server caches and a changing part; computer steps are answered with short codes; the goal is decided once per task; the first step is worked out as soon as the friend asks, before you have even agreed.

### More reliable

- **279 of 279** practice chains ended correctly (right person, right message, or right page), always picking the first tile, with the real model. Before the fixes below it was 16 of 18.
- On chat lists the size of yours (12 and 30 names) and a busy sports page (25 links), the first tile was right in all 21 cases I tried. The sidecar now lists up to 30 chats, so someone further down can be found.
- About 1,200 random gestures, friend lines and incoming texts thrown at it with the real model, and 40,000 more with a stand-in model while Bonsai, the screen reader, the clicker and the voice were made to fail at random: no crash, never stuck, and nothing was sent without a Yes (over 150 texts were sent in those runs, every one after a Yes). A 3,000-action version now runs in `./test.sh`.
- **Wrong recipient fixed.** "Let mom know I'll call her tonight" used to open Andrew's chat, because the profile calls him the best friend. The person the friend names is now always the recipient, and the confirmation says who it goes to: `Send "i'll call you tonight" to Mom?`
- **No chat, no guess.** If the person has no chat on screen, the only tile is Done ("Can't see a chat with Coach in Messages"). Last night it opened someone else's chat. And if another person's chat is open, nothing can be typed or sent there: "Back to conversations" comes first.
- **Never stuck.** A sidecar restart mid-speech (what froze it on "Speaking" last night), a Bonsai failure, or any error now returns to listening with a plain message.
- **No repeated steps**, and Send is always the first tile once a message is typed.
- **Knows when it is finished**: "That looks done" with Done first, after Play or after opening the page that was asked for.
- **Messages worded properly**: "ask Andrew if he wants pizza" becomes "do you want pizza", not "sure", and "let mom know I'll call her tonight" becomes "i'll call you tonight", not "i'll call her tonight".
- **Passing something on is not hijacked** by the other person's last text in that chat. That only shapes the wording when the task is to answer it.
- **"More..." and "Other answers" really give something new.** Bonsai used to hand back the same three when shown what you had passed on; and choosing "Ask when" produced long rambling lines. Both fixed: you now get short real questions ("when do you need it?") and genuinely different options.
- **No more jokes at bad news.** Bonsai liked to paste the profile's example lines into other conversations ("want to get food about it" when someone's grandmother is in hospital, "movie's on, hurry up" for a baseball game). Example lines are now only allowed in the conversation they belong to, and replaced otherwise.
- **A request heard in two pieces** ("can you tell Andrew" ... "to come back") is treated as the one request it was.
- **Replies fit the kind of line**: a yes-or-no question gets a clear yes and a clear no; "thai or pizza" gets one of each; a question about a fact Bonsai can't know gets "not sure" instead of a made-up answer (mostly: it still slips sometimes).

### Gestures

- A nod is now a dip and return relative to where your head just was, and "neutral" slowly follows you, so nods keep working after you slump or lean. Under the old rules a slump of about 10 degrees lost them.
- The dip that comes with a left tilt (20 degrees for you) is no longer read as a nod. Opening your mouth, which also tips the head, isn't either.
- A smile in the first 0.6 s after new tiles appear is ignored, so smiling at your friend as you nod doesn't pick the first tile.
- They behave the same on a slow camera (10 frames a second) as a fast one, and with tracking jitter added.
- If the new rules feel worse on your face, the **Classic gestures** checkbox in the Chrome tab's dev bar switches to last night's rules without a restart (it relearns your neutral face, about two seconds). `NOD_GESTURES=classic ./start.sh` starts that way.
- `python -m hub.face --record trace.json` saves 40 s of your real gestures; `--replay trace.json` shows what the new and the old rules fire on it. If something misfires, record it and send me the file.
- The screen says "Learning your neutral face", "Can't see your face", or "Camera not available" when that is the problem.

### New things it can do

- **Answer a text that arrives.** "Andrew texted you. Nod to answer" gives three answers; one pick opens Messages, opens the chat and types it; then Yes sends.
- **Text someone** from the menu, with messages suggested from the conversation.
- **Use computer** from the menu now offers things to do ("text Andrew", "put the game on") when nobody asked for anything.
- **Express texting** (`EXPRESS=1 ./start.sh`, off by default): "tell Andrew to come back" takes 3 picks instead of 5. Agree, pick a wording, confirm. The Mac opens the chat and types it. Kyle's step-by-step version stays the default because it shows Bonsai reading the screen.
- **Quick phrases** ("Yes", "No", "Thank you", "I need help", ...) that work even with Bonsai off. Set your own with `"phrases"` in `hub/profile.json`.
- **Stop listening** from the menu: nothing said is kept until the next nod.
- **Learns your voice.** Replies you pick are saved in `hub/learned.json` (this Mac only) and shown to Bonsai as examples. `LEARN=0` turns it off; delete the file to forget.
- **A game page that works offline.** `http://127.0.0.1:8000/demo` is a stand-in sports site served by the hub: a "Yankees vs Red Sox - Watch Live" link and a player with Play. Open it in Safari before the demo and "put the game on" can be shown with Wi-Fi off, which no real site can.
- **Reword a typed message**: "Write a different message" gives three more.
- If the friend keeps talking after you nod and you haven't moved yet, the replies are rewritten for what they just said.
- Number keys `1`-`5` and mouse clicks pick tiles, for a helper or for rehearsing.
- The dev bar in the Chrome tab has two checkboxes, **Express texts** and **Learn replies**, so you can switch them without restarting.

### Screen

- Captions in the overlay: "hearing", "heard", "you said", "text from".
- Chat bubbles for the conversation, including texts sent and received.
- A progress line during computer tasks: the goal and each step done.
- A welcome card that explains the four gestures until the first words are spoken.
- `?scale=1.3` on the page address makes everything bigger.
- The Yes tile on a send is red and says "this sends it".
- "Sent" and "Done" go back to "Listening" by themselves after five seconds, so the screen never sits on an old result.

### Listening

- A pause in the middle of a sentence waits a little longer than a pause at the end of one, so people who stop to think are not cut in half.
- Names and teams from the profile are given to Whisper as hints. With background noise it used to hear "youngies"; it now hears "Yankees".
- A cough or sniff is no longer passed on as "*cough*".

### Tools

- `./doctor.sh`: checks models on disk, permissions, microphone, camera, sound devices and all three services. With Messages open it also says whether the people in the profile (Andrew) have a chat it can see, which is the thing most likely to trip the demo line.
- `./start.sh` now ends by running the health check, so a missing permission is the first thing you see.
- `./start.sh` now starts the hub before the sidecar. Last night the hub turning the camera on stopped the microphone that was already running; this order avoids it, and the sidecar restarts the microphone by itself if it ever goes quiet.
- **http://127.0.0.1:8000/debug**: a live "under the hood" page. It shows what the camera reads for nod, tilt, smile and mouth against each threshold (so you can see how far your nod actually dips), the microphone level against the speech threshold, how long each Bonsai call took, and a log of every gesture, sentence heard and action. Keep it open while tuning.
- `./test.sh`: 84 checks in a few seconds, no model or hardware needed.
- `./rehearse.sh` starts the pretend-Mac rehearsal in one command, and `tools/autoplay.py` plays the whole demo by itself on it. `./test.sh --all` also runs the real sidecar on a recording, silently.
- `NOD_SILENT=1` or `curl -X POST 'localhost:8790/quiet?on=1'`: rehearse without sound.

## What I could not check, and you should

I can't use the camera, grant Accessibility to my own processes, or play sound, so these are built but have not run on real hardware:

1. **The new gesture rules on your face.** They pass 25 scenarios built from your `--measure` numbers, with jitter and at several frame rates, but that is not you. Try nods after leaning back, and a left tilt. If a gesture is worse than last night, tell me which.
2. **Messages on the real Mac after the hub changes.** The sidecar's Messages code is unchanged from what worked last night; the hub that drives it is new. The practice Mac passes every time.
3. **Incoming texts from real Messages** (`NOD_WATCH_MESSAGES=1`). The hub side is tested; how the sidecar spots a new text (an "Unread" row, or a new bubble in the open chat) is from what I saw in your Messages last night and has never run. It is off unless you turn it on.
4. **Web pages.** `/screen` now lists a page's own links before the browser's toolbar buttons, and the stand-in game page was built for it. Neither has been read through the real Safari yet, so try "put the Yankees game on" with that page open before relying on it.
5. **How the overlay looks.** Its window now reports itself sitting just above the Dock and the page loads in it, but I can't see the screen, so the look of it and of the new captions is yours to judge.
6. **Speech hints and the longer mid-sentence pause**: tested on recordings with added noise, not on live voices in a room.
7. **The voice.** I can't listen, but I had Whisper transcribe six generated replies and it got every word, so it is at least intelligible.

## For Kyle

This branch changes `hub/`, `ui/` and `prompts/`, which are yours. Ivan asked for it overnight; it is on a branch so you can take all, some or none of it.

- `hub/brain.py`: prompt split and caching, short step codes, recipient and ordering rules, drafts, text replies, streaming, learned replies.
- `hub/main.py`: text flows, menu, error recovery, pre-work during speech, status endpoint.
- `hub/gestures.py` (new) holds the gesture rules; `hub/face.py` keeps the thresholds, camera and `--measure`.
- `prompts/*.txt`: each now has a `=====` line. Keep anything that changes between calls below it, or the cache is lost.
- `SPEC.md` is updated for every interface change.
- `tests/` runs the hub against `FakeMac` with a stand-in model, so prompt or logic changes can be checked in seconds. On Windows: `python tests/run_all.py`.

## Known limits

- Bonsai still invents the odd fact when answering a text ("presentation at 7"). The confirmation shows the text, so read it before Yes.
- Typing into search boxes is not offered: nothing can press Return for you yet except Send in Messages.
- "Put the game on" depends on the page having a link and a Play button the sidecar can see.
