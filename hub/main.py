"""Blink hub. Run from the repo root:

    uvicorn hub.main:app --port 8000

Then open http://127.0.0.1:8000
"""
import asyncio
import time

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import brain, config, sidecar

app = FastAPI()

HOME_TILES = [
    {"label": "Talk", "kind": "home", "value": "talk"},
    {"label": "Quick phrases", "kind": "home", "value": "phrases"},
    {"label": "Use computer", "kind": "home", "value": "computer"},
    {"label": "Repeat last", "kind": "home", "value": "repeat"},
    {"label": "Back", "kind": "home", "value": "back"},
]
# Things he may need to say with no wait and no Bonsai (works even if the model is down).
# Override with "phrases" in hub/profile.json. Shown 3 at a time.
DEFAULT_PHRASES = ["Yes", "No", "Thank you", "I need help", "Give me a minute",
                   "Can you say that again?", "I'm okay", "Not right now", "Please stop"]
KIND_TILES = [
    {"label": "Yes", "kind": "kind", "value": "yes"},
    {"label": "No", "kind": "kind", "value": "no"},
    {"label": "Ask a question", "kind": "kind", "value": "question"},
    {"label": "Something else", "kind": "kind", "value": "else"},
]
# After this many narrowing rounds, stop guessing and offer replies that always work.
MAX_NARROW_ROUNDS = 3
STUCK_TILES = [
    {"label": "Hold on, let me think.", "kind": "say", "value": "Hold on, let me think."},
    {"label": "Can you ask me as a yes or no?", "kind": "say", "value": "Can you ask me as a yes or no?"},
    {"label": "Never mind.", "kind": "say", "value": "Never mind."},
    {"label": "Start over", "kind": "home", "value": "back"},
]
RISKY_WORDS = ("send", "post", "delete", "remove", "submit")


def new_narrow():
    # path: options picked (most specific last); rejected: replies passed on; seen: options shown
    return {"path": [], "rejected": [], "seen": [], "rounds": 0}


class State:
    def __init__(self):
        self.mode = "listen"   # listen | thinking | choose | speaking | computer
        self.status = "Listening"
        self.tiles: list[dict] = []
        self.index = 0
        self.history: list[dict] = []   # {"who": "friend"|"me", "text": ...}
        self.partial = ""
        self.last_said = ""
        self.last_gesture = ""
        self.gesture_n = 0   # counts handled gestures so the UI can react to each one
        self.stats = {"latency_ms": None, "model": "Bonsai 2 · on-device", "bonsai_ok": True,
                      "sidecar_ok": True}
        # camera: on = gestures come from the webcam; face = a face is in view right now
        self.camera = {"on": False, "face": False, "calibrating": False, "error": ""}
        self.in_computer = False
        self.screen: dict = {}
        self.pending: dict | None = None   # action waiting for confirmation
        self.goal: dict | None = None      # {"goal", "app"} of the computer task in progress
        self.drafts_seen: list[str] = []   # message wordings already offered or typed
        self.prepared: asyncio.Task | None = None   # first computer step, worked out during speech
        self.incoming: dict | None = None  # {"from", "text", "at"}: a text that came in, not yet answered
        self.texts_seen: list[str] = []    # answers to that text already offered
        self.sending: dict | None = None   # {"to", "text"} of the message waiting for Yes
        self.done: list[str] = []          # computer steps taken this session
        self.pregen: tuple | None = None   # (history_len, replies, offer_computer, bonsai_ok)
        self.pregen_task: asyncio.Task | None = None
        self.narrow = new_narrow()   # "More..." narrowing for the current turn

    def view(self):
        v = {k: getattr(self, k) for k in
             ("mode", "status", "tiles", "index", "history", "partial",
              "last_gesture", "gesture_n", "stats", "in_computer", "camera")}
        inc = self.incoming
        v["incoming"] = {"from": inc["from"], "text": inc["text"]} if inc else None
        # what the computer task is and how far it has got, for the progress line on screen
        v["task"] = {"goal": (self.goal or {}).get("goal", ""), "done": self.done[-4:]} if self.in_computer else None
        return v


S = State()
clients: set[WebSocket] = set()
lock = asyncio.Lock()


async def broadcast():
    data = S.view()
    for ws in list(clients):
        try:
            await ws.send_json(data)
        except Exception:
            clients.discard(ws)


def set_tiles(tiles, mode="choose", status="Choose"):
    S.tiles, S.index, S.mode, S.status = tiles, 0, mode, status


def go_listen(status="Listening"):
    S.mode, S.status, S.tiles, S.index, S.in_computer, S.pending = "listen", status, [], 0, False, None
    if status == "Listening" and text_waiting():
        S.status = f'{S.incoming["from"]} texted you. Nod to answer'


INCOMING_FRESH_S = 180   # an unanswered text is offered on the next nod for this long


def text_waiting() -> bool:
    """A text came in after the last thing said out loud, and hasn't been answered."""
    inc = S.incoming
    if not inc or time.time() - inc["at"] > INCOMING_FRESH_S:
        return False
    last_spoken = next((t for t in reversed(S.history) if t["who"] in ("friend", "me")), None)
    return last_spoken is None or last_spoken.get("at", 0) < inc["at"]


# ---------- talk mode ----------

async def show_replies(kind=None, path=None):
    fresh = kind is None and not path
    if fresh:
        S.narrow = new_narrow()
    task = S.pregen_task
    if fresh and not S.pregen and task and not task.done():
        # Nodded while pre-generation is mid-flight: wait for it, don't start a duplicate call.
        S.mode, S.status, S.tiles = "thinking", "Thinking...", []
        await broadcast()
        await asyncio.wait([task])   # doesn't raise if the task was cancelled
    if fresh and S.pregen and S.pregen[0] == len(S.history):
        _, replies, offer, ok = S.pregen   # pre-generated during the pause
        ms = 0
    else:
        S.mode, S.status, S.tiles = "thinking", "Thinking...", []
        await broadcast()
        offer = fresh and await brain.computer_task(S.history)
        replies, ms, ok = await brain.suggest_replies(
            S.history, kind=kind, path=path, avoid=None if fresh else S.narrow["rejected"], offer=offer)
    S.stats.update(latency_ms=ms or S.stats["latency_ms"], bonsai_ok=ok)
    # "learn": these came from Bonsai for this conversation, so a pick teaches it his voice
    tiles = [{"label": r, "kind": "say", "value": r, "learn": ok} for r in replies]
    if offer:   # the friend asked for something doable on the Mac: say it, then go do it
        tiles[0]["then"] = "computer"
    tiles.append({"label": "More...", "kind": "more"})
    set_tiles(tiles, status="Pick a reply")


async def show_intents():
    """Bonsai asks a clarifying question with 3 directions; each pick narrows further."""
    N = S.narrow
    N["rounds"] += 1
    if N["rounds"] > MAX_NARROW_ROUNDS:
        set_tiles(STUCK_TILES, status="Not getting it. Say one of these?")
        return
    S.mode, S.status, S.tiles = "thinking", "Narrowing it down...", []
    await broadcast()
    question, options, ms, ok = await brain.suggest_intents(
        S.history, N["path"], N["rejected"], N["seen"])
    S.stats.update(latency_ms=ms, bonsai_ok=ok)
    if not options:   # Bonsai failed: fixed categories still work
        set_tiles(KIND_TILES, status="What kind of reply?")
        return
    N["seen"] += options
    tiles = [{"label": o, "kind": "intent", "value": o} for o in options]
    tiles.append({"label": "Something else", "kind": "else"})
    set_tiles(tiles, status=question or "What do you want to say?")


async def say(text, then=None):
    S.mode, S.status, S.tiles = "speaking", "Speaking", []
    S.history.append({"who": "me", "text": text, "at": time.time()})
    S.last_said = text
    await broadcast()
    if then == "computer":   # he just agreed to do something on the Mac: start doing it
        start_task()
        # Read the screen and ask Bonsai for the first step while the reply is being spoken,
        # so the tiles are ready the moment he stops talking.
        S.prepared = asyncio.create_task(prepare_actions())
    await speak(text)
    if then == "computer":
        await show_actions()
    else:
        go_listen()


async def pregenerate(n_turns):
    await asyncio.sleep(config.PREGEN_PAUSE_S)
    if len(S.history) != n_turns or S.mode != "listen":
        return
    offer = await brain.computer_task(S.history)
    replies, ms, ok = await brain.suggest_replies(S.history, offer=offer)
    if len(S.history) == n_turns:
        S.pregen = (n_turns, replies, offer, ok)
        S.stats.update(latency_ms=ms, bonsai_ok=ok)


def show_phrases(page=0):
    phrases = brain.load_profile().get("phrases") or DEFAULT_PHRASES
    pages = max(1, -(-len(phrases) // 3))
    page %= pages
    tiles = [{"label": p, "kind": "say", "value": p} for p in phrases[page * 3:page * 3 + 3]]
    if pages > 1:
        tiles.append({"label": "More phrases", "kind": "phrases", "value": page + 1})
    tiles.append({"label": "Back", "kind": "home", "value": "back"})
    set_tiles(tiles, status="Quick phrases")


# ---------- answering a text that came in ----------

async def show_text_replies():
    inc = S.incoming
    S.mode, S.status, S.tiles = "thinking", f'Answering {inc["from"]}...', []
    await broadcast()
    ready, inc["ready"] = inc.get("ready"), None
    if ready and not S.texts_seen:        # written while the text was sitting there
        replies, ms, ok = await ready
    else:
        replies, ms, ok = await brain.suggest_text_replies(S.history, inc["from"], inc["text"], S.texts_seen)
    S.stats.update(latency_ms=ms, bonsai_ok=ok)
    S.texts_seen += replies
    tiles = [{"label": r, "kind": "textreply", "value": r} for r in replies]
    tiles.append({"label": "Other answers", "kind": "textmore"})
    tiles.append({"label": "Not now", "kind": "textskip"})
    set_tiles(tiles, status=f'Text {inc["from"]} back')


async def send_text(who, text):
    """Open Messages, open the chat, type the answer, then ask before sending.
    One pick does what would otherwise be four steps. If anything is not where it is
    expected, hand over to the normal step-by-step tiles instead of guessing."""
    start_task()
    S.in_computer = True
    S.goal = {"goal": f"reply to {who}'s text", "app": "Messages"}

    def find(pred):
        return next((e for e in S.screen.get("elements", []) if pred(e)), None)

    def box_for(person):
        return find(lambda e: e["role"] in sidecar.TYPEABLE and e["label"].startswith("Message ")
                    and person.lower() in e["label"].lower())

    async def step(status, action, done):
        S.mode, S.status, S.tiles = "thinking", status, []
        await broadcast()
        res = await sidecar.act(action)
        if not res.get("ok"):
            raise RuntimeError(res.get("error", "that didn't work"))
        S.done.append(done)
        await asyncio.sleep(0.4)
        S.screen = await sidecar.screen()

    try:
        S.mode, S.status, S.tiles = "thinking", "Reading the screen...", []
        await broadcast()
        S.screen = await sidecar.screen()
        if S.screen.get("app") != "Messages":
            await step("Opening Messages...", {"type": "open_app", "name": "Messages"}, "Opened Messages")
        if not box_for(who):
            back = find(lambda e: e["label"] == "Back to conversations")
            if back:   # another chat is open
                await step("Finding the chat...", {"type": "press", "id": back["id"]}, 'Pressed "Back to conversations"')
            row = find(lambda e: e["role"] not in sidecar.TYPEABLE and e["label"].lower() == who.lower()) \
                or find(lambda e: e["role"] not in sidecar.TYPEABLE and e["label"].lower().startswith(who.lower()))
            if not row:
                raise LookupError(f"no chat with {who} on screen")
            await step(f"Opening {who}'s chat...", {"type": "press", "id": row["id"]}, f'Pressed "{row["label"]}"')
        box = box_for(who)
        if not box:
            raise LookupError("no message box")
        await step("Writing...", {"type": "type", "id": box["id"], "text": text},
                   f'Typed "{text}" into "{box["label"].split(":")[0]}"')
        S.drafts_seen.append(text)
        send = find(lambda e: e["role"] not in sidecar.TYPEABLE and "send" in e["label"].lower())
        if not send:
            raise LookupError("no Send control")
        await ask_to_confirm({"type": "press", "id": send["id"], "label": send["label"]})
    except Exception as e:
        print(f"[text] couldn't do it in one go: {e!r}")
        await show_actions(note="Pick the next step")


# ---------- computer mode ----------

async def speak(text):
    """Speak through the sidecar. A sidecar that is down or restarting must never leave the
    hub stuck on "Speaking": report it and carry on."""
    try:
        await sidecar.speak(text)
    except Exception as e:
        print(f"[speak] failed: {e!r}")
        S.stats["sidecar_ok"] = False
    else:
        S.stats["sidecar_ok"] = True


def start_task():
    S.done, S.goal, S.drafts_seen = [], None, []
    if S.prepared:
        S.prepared.cancel()
    S.prepared = None


async def prepare_actions():
    screen = await sidecar.screen()
    return screen, await brain.suggest_actions(S.history, screen, S.done, S.goal)


async def show_actions(note=""):
    S.in_computer = True
    S.mode, S.status, S.tiles = "thinking", "Reading the screen...", []
    await broadcast()
    prepared, S.prepared = S.prepared, None
    try:
        S.screen, (acts, ms, ok, info) = await (prepared or prepare_actions())
    except Exception as e:   # sidecar down: say so instead of hanging
        print(f"[screen] failed: {e!r}")
        go_listen(status="Can't reach the Mac helper")
        return
    S.stats.update(latency_ms=ms, bonsai_ok=ok)
    if info.get("goal"):
        S.goal = {"goal": info["goal"], "app": info["app"]}
    tiles = [{"label": a["label"], "kind": "redraft" if a.get("draft") else "action", "value": a}
             for a in acts]
    done = {"label": "Done", "kind": "done"}
    app = S.screen.get("app", "")
    if info.get("finished") or not tiles:
        # Bonsai thinks the task is complete: "Done" comes first, one smile ends it.
        set_tiles([done] + tiles, mode="computer", status=note or "That looks done")
    else:
        set_tiles(tiles + [done], mode="computer", status=note or f"Computer · {app}")


async def show_drafts(action):
    """3 (more) wordings for a message box."""
    S.mode, S.status, S.tiles = "thinking", "Writing...", []
    await broadcast()
    goal = (S.goal or {}).get("goal", "")
    try:
        acts = await brain.draft_actions(S.history, S.screen, action["id"], goal, S.drafts_seen)
    except Exception as e:
        print(f"[brain] Bonsai error: {e}")
        acts = []
    if not acts:
        await show_actions(note="Couldn't write that. Try another step?")
        return
    S.drafts_seen += [a["text"] for a in acts]
    tiles = [{"label": a["label"], "kind": "action", "value": a} for a in acts]
    tiles.append({"label": "More wordings", "kind": "redraft", "value": action})
    set_tiles(tiles, mode="computer", status="Pick the wording")


def element(action):
    return next((e for e in S.screen.get("elements", []) if e["id"] == action.get("id")), {})


def is_risky(action):
    return action.get("type") == "press" and any(w in action["label"].lower() for w in RISKY_WORDS)


def confirm_question(action):
    """Say what is about to go out, not just the button name."""
    draft = next((e["value"] for e in S.screen.get("elements", [])
                  if e["role"] in sidecar.TYPEABLE and e.get("value")), "")
    if draft and "send" in action["label"].lower():
        # Name who it goes to, so a text can't go to the wrong person unnoticed.
        box = next((e["label"] for e in S.screen.get("elements", [])
                    if e["role"] in sidecar.TYPEABLE and e.get("value")), "")
        who = box[len("Message "):].split(":")[0].strip() if box.startswith("Message ") else ""
        return f'Send "{draft}" to {who}?' if who else f'Send "{draft}"?'
    return f'{action["label"]}?'


def draft_on_screen() -> dict | None:
    """The message that is typed and about to go out: {"to", "text"}."""
    box = next((e for e in S.screen.get("elements", [])
                if e["role"] in sidecar.TYPEABLE and e.get("value")), None)
    if not box:
        return None
    label = box["label"]
    who = label[len("Message "):].split(":")[0].strip() if label.startswith("Message ") else ""
    return {"to": who, "text": box["value"]}


async def ask_to_confirm(action):
    """Anything that sends, posts or deletes: say exactly what will happen and wait for Yes."""
    S.pending = action
    S.sending = draft_on_screen() if "send" in action["label"].lower() else None
    set_tiles([{"label": "Yes, do it", "kind": "confirm", "value": True},
               {"label": "No", "kind": "confirm", "value": False}],
              mode="computer", status=confirm_question(action))
    S.index = 1   # highlight "No": a stray smile must never send something
    await broadcast()
    await speak(S.status)


def describe(action):
    """One line for the "already done" list Bonsai sees on the next step."""
    if action["type"] == "type":
        return f'Typed "{action["text"]}" into "{element(action).get("label", "the text field")}"'
    if action["type"] == "open_app":
        return f'Opened {action["name"]}'
    return f'Pressed "{action["label"]}"'


async def run_action(action, finish=False):
    """finish=True: this was a confirmed send/post/delete, so the task is over."""
    S.mode, S.status, S.tiles = "thinking", "Doing it...", []
    await broadcast()
    try:
        res = await sidecar.act({k: v for k, v in action.items() if k != "label"})
    except Exception as e:   # sidecar down or crashed: stay usable
        res = {"ok": False, "error": str(e)}
    if not res.get("ok"):
        print(f"[act] failed: {res}")
        await asyncio.sleep(0.4)
        await show_actions(note="That didn't work. Try another?")
        return
    S.done.append(describe(action))
    if action["type"] == "type":
        S.drafts_seen.append(action["text"])
    if finish:
        sent = S.sending
        S.sending = None
        if sent:   # remember what was texted to whom: context for later replies, and shown on screen
            S.history.append({"who": "me_text", "to": sent["to"], "text": sent["text"]})
            if S.incoming and S.incoming["from"].lower() == sent["to"].lower():
                S.incoming = None
        go_listen(status="Sent" if sent else "Done")
        return
    await asyncio.sleep(0.4)   # let the app update before reading it again
    await show_actions()


# ---------- gestures ----------

async def handle(gesture):
    S.last_gesture = gesture
    S.gesture_n += 1
    if S.mode in ("thinking", "speaking"):
        return   # ignore input while busy

    if S.mode == "listen":
        if gesture == "nod":
            if text_waiting():
                await show_text_replies()
            else:
                await show_replies()
        elif gesture == "cancel":
            set_tiles(HOME_TILES, mode="choose", status="Menu")
        return

    # choose / computer: tiles are on screen
    n = len(S.tiles)
    if gesture == "left":
        S.index = (S.index - 1) % n
    elif gesture == "right":
        S.index = (S.index + 1) % n
    elif gesture == "cancel":
        go_listen()
    elif gesture == "select":
        await pick(S.tiles[S.index])


async def pick(tile):
    k = tile["kind"]
    if k == "say":
        friend = next((t["text"] for t in reversed(S.history) if t["who"] == "friend"), "")
        if tile.get("learn") and not tile.get("then") and S.history and S.history[-1]["who"] == "friend":
            brain.remember_pick(friend, tile["value"])
        await say(tile["value"], then=tile.get("then"))
    elif k == "more":
        S.narrow["rejected"] += [t["label"] for t in S.tiles if t.get("kind") == "say"]
        await show_intents()
    elif k == "intent":
        S.narrow["path"].append(tile["value"])
        await show_replies(path=list(S.narrow["path"]))
    elif k == "else":
        await show_intents()
    elif k == "kind":
        await show_replies(kind=tile["value"])
    elif k == "home":
        v = tile["value"]
        if v == "talk":
            await show_replies()
        elif v == "phrases":
            show_phrases()
        elif v == "computer":
            start_task()
            await show_actions()
        elif v == "repeat" and S.last_said:
            await speak(S.last_said)
            go_listen()
        else:
            go_listen()
    elif k == "action":
        action = tile["value"]
        if is_risky(action):
            await ask_to_confirm(action)
        else:
            await run_action(action)
    elif k == "phrases":
        show_phrases(tile["value"])
    elif k == "textreply":
        await send_text(S.incoming["from"], tile["value"])
    elif k == "textmore":
        await show_text_replies()
    elif k == "textskip":
        S.incoming = None
        go_listen()
    elif k == "redraft":
        await show_drafts(tile["value"])
    elif k == "confirm":
        action, S.pending = S.pending, None
        if tile["value"] and action:
            await run_action(action, finish=True)
        else:
            await show_actions()
    elif k == "done":
        go_listen()


async def on_gesture(gesture):
    if lock.locked():
        return   # busy (thinking/speaking): drop the gesture instead of queueing it
    async with lock:
        try:
            await handle(gesture)
        except Exception as e:   # a bug or a dead helper must never freeze the screen
            import traceback
            traceback.print_exc()
            go_listen(status="Something went wrong. Nod to try again")
    await broadcast()


# ---------- HTTP / websocket ----------

class Heard(BaseModel):
    text: str
    final: bool = True


@app.post("/heard")
async def heard(h: Heard):
    """Sidecar posts what the friend said."""
    text = h.text.strip()
    if not h.final:
        S.partial = text
    elif text:
        S.partial = ""
        S.history.append({"who": "friend", "text": text, "at": time.time()})
        S.pregen = None
        if S.pregen_task:
            S.pregen_task.cancel()
        S.pregen_task = asyncio.create_task(pregenerate(len(S.history)))
    await broadcast()
    return {"ok": True}


@app.post("/incoming")
async def incoming(body: dict):
    """The sidecar saw a new text message arrive. The next nod offers answers to it."""
    who = str(body.get("from") or body.get("sender") or "").strip()
    text = str(body.get("text") or "").strip()
    if not who or not text:
        return {"ok": False, "error": "need from and text"}
    last = next((t for t in reversed(S.history) if t["who"] == "text"), None)
    if last and last.get("from") == who and last["text"] == text:
        return {"ok": True, "duplicate": True}
    S.incoming = {"from": who, "text": text, "at": time.time()}
    S.texts_seen = []
    # start writing answers now, so they are ready by the time he nods
    S.incoming["ready"] = asyncio.create_task(brain.suggest_text_replies(list(S.history), who, text))
    S.history.append({"who": "text", "from": who, "text": text})
    if config.MOCK_SIDECAR:
        sidecar.FAKE.received[who] = text
    if S.mode == "listen":
        go_listen()
    await broadcast()
    return {"ok": True}


@app.post("/recalibrate")
async def recalibrate():
    """Re-learn the neutral face pose (UI key C, or curl from anywhere on the machine)."""
    det = getattr(app.state, "detector", None)
    if det:
        det.recalibrate()
    return {"ok": det is not None}


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    clients.add(ws)
    await ws.send_json(S.view())
    try:
        while True:
            msg = await ws.receive_json()
            if msg.get("type") == "gesture":
                asyncio.create_task(on_gesture(msg["name"]))
            elif msg.get("type") == "heard":   # dev box: type what the friend said
                await heard(Heard(text=msg.get("text", ""), final=True))
            elif msg.get("type") == "incoming":   # dev box: "/text Andrew: where are you"
                await incoming({"from": msg.get("from", ""), "text": msg.get("text", "")})
            elif msg.get("type") == "recalibrate":
                await recalibrate()
            elif msg.get("type") == "reset":
                S.history.clear()
                S.incoming, S.pregen = None, None
                sidecar.FAKE.reset()
                go_listen()
                await broadcast()
    except WebSocketDisconnect:
        clients.discard(ws)


@app.get("/")
async def index():
    return FileResponse(config.UI_DIR / "index.html")


async def watch_camera():
    """Tell the screen when the camera loses the face or is learning the neutral pose."""
    while True:
        status = app.state.detector.status()
        if status != S.camera:
            S.camera = status
            await broadcast()
        await asyncio.sleep(0.4)


@app.on_event("startup")
async def startup():
    if config.USE_CAMERA:
        from .face import GestureDetector
        loop = asyncio.get_running_loop()
        app.state.detector = GestureDetector(
            on_event=lambda g: asyncio.run_coroutine_threadsafe(on_gesture(g), loop),
            camera_index=config.CAMERA_INDEX,
        )
        app.state.detector.start()
        asyncio.create_task(watch_camera())
    print(f"[hub] mock sidecar: {config.MOCK_SIDECAR}  camera: {config.USE_CAMERA}")
