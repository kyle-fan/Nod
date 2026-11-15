# Overnight work, 15 Nov (branch `overnight`)

Everything here is on the local branch `overnight`. Nothing was pushed, `main` is untouched, and the state before the night is tagged `pre-overnight-2026-11-15`.

```bash
git checkout overnight          # the new work
git checkout main               # exactly what you had last night
git diff main overnight --stat  # what changed
```

No sound was played and no text message was sent. One side effect on your Mac: a test run asked the sidecar to open Messages, so Messages may be open in the background.

## Try it in two minutes

```bash
cd ~/Nod && git checkout overnight
./test.sh                                             # fast checks, a few seconds
NOD_OVERLAY=1 USE_CAMERA=1 ./start.sh                 # from Terminal, as before
./doctor.sh                                           # says what is missing, in plain words
```

Then, with Messages open:

1. Say "can you tell Caden Ruan to come back", nod, smile on the first tile each time. On the practice Mac the whole chain takes about 9 seconds.
2. Open your mouth for the menu. Try **Quick phrases** and **Text someone**.
3. In the Chrome tab's dev box type `/text Andrew: where are you` and nod. (To have real incoming texts trigger this, start with `NOD_WATCH_MESSAGES=1`.)

## What is new

### Faster

| | Before | Now |
|---|---|---|
| One computer step ("Reading the screen...") | 4 to 7 s | 1.5 to 2.5 s |
| Whole texting chain, friend's request to sent | about 45 s | about 9 s |
| First reply tile after a nod with nothing prepared | about 2 s for all three | first at 0.6 s, they appear one by one |
| Confirming a send | wait for the spoken question to finish | answer while it is still speaking |

How: prompts are split into a fixed part that llama-server caches and a changing part; computer steps are answered with short codes; the goal is decided once per task; the first step is prepared while "yeah one sec" is being spoken.

### More reliable

- **72 of 72** practice chains ended correctly (right person, right message, or right page), always picking the first tile, over five runs with the real model. Before the fixes below it was 16 of 18.
- On a chat list the size of yours (12 names) and a busy sports page (25 links), the first tile was right in all 16 cases I tried.
- **Wrong recipient fixed.** "Let mom know I'll call her tonight" used to open Andrew's chat, because the profile calls him the best friend. The person the friend names is now always the recipient, and the confirmation says who it goes to: `Send "i'll call you tonight" to Mom?`
- **No chat, no guess.** If the person has no chat on screen, the only tile is Done ("Can't see a chat with Coach in Messages"). Last night it opened someone else's chat. And if another person's chat is open, nothing can be typed or sent there: "Back to conversations" comes first.
- **Never stuck.** A sidecar restart mid-speech (what froze it on "Speaking" last night), a Bonsai failure, or any error now returns to listening with a plain message.
- **No repeated steps**, and Send is always the first tile once a message is typed.
- **Knows when it is finished**: "That looks done" with Done first, after Play or after opening the page that was asked for.
- **Messages worded properly**: "ask Andrew if he wants pizza" becomes "do you want pizza", not "sure", and "let mom know I'll call her tonight" becomes "i'll call you tonight", not "i'll call her tonight".
- **Passing something on is not hijacked** by the other person's last text in that chat. That only shapes the wording when the task is to answer it.
- **Replies fit the kind of line**: a yes-or-no question gets a clear yes and a clear no; "thai or pizza" gets one of each; a question about a fact Bonsai can't know gets "not sure" instead of a made-up answer (mostly: it still slips sometimes).

### Gestures

- A nod is now a dip and return relative to where your head just was, and "neutral" slowly follows you, so nods keep working after you slump or lean. Under the old rules a slump of about 10 degrees lost them.
- The dip that comes with a left tilt (20 degrees for you) is no longer read as a nod. Opening your mouth, which also tips the head, isn't either.
- If the new rules feel worse on your face, `NOD_GESTURES=classic ./start.sh` brings back last night's.
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

### Listening

- A pause in the middle of a sentence waits a little longer than a pause at the end of one, so people who stop to think are not cut in half.
- Names and teams from the profile are given to Whisper as hints. With background noise it used to hear "youngies"; it now hears "Yankees".
- A cough or sniff is no longer passed on as "*cough*".

### Tools

- `./doctor.sh`: checks models on disk, permissions, microphone, camera, sound devices and all three services.
- `./test.sh`: about 70 checks in a few seconds, no model or hardware needed.
- `./rehearse.sh` starts the pretend-Mac rehearsal in one command, and `tools/autoplay.py` plays the whole demo by itself on it. `./test.sh --all` also runs the real sidecar on a recording, silently.
- `NOD_SILENT=1` or `curl -X POST 'localhost:8790/quiet?on=1'`: rehearse without sound.

## What I could not check, and you should

I can't use the camera, grant Accessibility to my own processes, or play sound, so these are built but have not run on real hardware:

1. **The new gesture rules on your face.** They pass 16 tests built from your `--measure` numbers, but that is not you. Try nods after leaning back, and a left tilt. If a gesture is worse than last night, tell me which.
2. **Messages on the real Mac after the hub changes.** The sidecar's Messages code is unchanged from what worked last night; the hub that drives it is new. The practice Mac passes every time.
3. **Incoming texts from real Messages** (`NOD_WATCH_MESSAGES=1`). The hub side is tested; how the sidecar spots a new text (an "Unread" row, or a new bubble in the open chat) is from what I saw in your Messages last night and has never run. It is off unless you turn it on.
4. **Web pages.** `/screen` now lists a page's own links before the browser's toolbar buttons. Never run on a real browser.
5. **The overlay's position above the Dock**, from last night, and how the new captions look in it.
6. **Speech hints**: tested on recordings with added noise, not on live voices in a room.

## For Kyle

This branch changes `hub/`, `ui/` and `prompts/`, which are yours. Ivan asked for it overnight; it is on a branch so you can take all, some or none of it.

- `hub/brain.py`: prompt split and caching, short step codes, recipient and ordering rules, drafts, text replies, streaming, learned replies.
- `hub/main.py`: text flows, menu, error recovery, pre-work during speech, status endpoint.
- `hub/gestures.py` (new) holds the gesture rules; `hub/face.py` keeps the thresholds, camera and `--measure`.
- `prompts/*.txt`: each now has a `=====` line. Keep anything that changes between calls below it, or the cache is lost.
- `SPEC.md` is updated for every interface change.
- `tests/` runs the hub against `FakeMac` with a stand-in model, so prompt or logic changes can be checked in seconds.

## Known limits

- Bonsai still invents the odd fact when answering a text ("presentation at 7"). The confirmation shows the text, so read it before Yes.
- Typing into search boxes is not offered: nothing can press Return for you yet except Send in Messages.
- "Put the game on" depends on the page having a link and a Play button the sidecar can see.
