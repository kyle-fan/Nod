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
    {"label": "Use computer", "kind": "home", "value": "computer"},
    {"label": "Repeat last", "kind": "home", "value": "repeat"},
    {"label": "Back", "kind": "home", "value": "back"},
]
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
        self.stats = {"latency_ms": None, "model": "Bonsai 2 · on-device", "bonsai_ok": True}
        self.in_computer = False
        self.screen: dict = {}
        self.pending: dict | None = None   # action waiting for confirmation
        self.pregen: tuple | None = None   # (history_len, replies)
        self.pregen_task: asyncio.Task | None = None
        self.narrow = new_narrow()   # "More..." narrowing for the current turn

    def view(self):
        return {k: getattr(self, k) for k in
                ("mode", "status", "tiles", "index", "history", "partial",
                 "last_gesture", "gesture_n", "stats", "in_computer")}


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
        replies, ms, ok = S.pregen[1], 0, True   # pre-generated during the pause
    else:
        S.mode, S.status, S.tiles = "thinking", "Thinking...", []
        await broadcast()
        replies, ms, ok = await brain.suggest_replies(
            S.history, kind=kind, path=path, avoid=None if fresh else S.narrow["rejected"])
    S.stats.update(latency_ms=ms or S.stats["latency_ms"], bonsai_ok=ok)
    tiles = [{"label": r, "kind": "say", "value": r} for r in replies]
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


async def say(text):
    S.mode, S.status, S.tiles = "speaking", f"Speaking", []
    S.history.append({"who": "me", "text": text})
    S.last_said = text
    await broadcast()
    await sidecar.speak(text)
    go_listen()


async def pregenerate(n_turns):
    await asyncio.sleep(config.PREGEN_PAUSE_S)
    if len(S.history) != n_turns or S.mode != "listen":
        return
    replies, ms, ok = await brain.suggest_replies(S.history)
    if len(S.history) == n_turns:
        S.pregen = (n_turns, replies)
        S.stats.update(latency_ms=ms, bonsai_ok=ok)


# ---------- computer mode ----------

async def show_actions():
    S.in_computer = True
    S.mode, S.status, S.tiles = "thinking", "Reading the screen...", []
    await broadcast()
    S.screen = await sidecar.screen()
    acts, ms, ok = await brain.suggest_actions(S.history, S.screen)
    S.stats.update(latency_ms=ms, bonsai_ok=ok)
    tiles = [{"label": a["label"], "kind": "action", "value": a} for a in acts]
    tiles.append({"label": "Done", "kind": "done"})
    set_tiles(tiles, mode="computer", status=f"Computer · {S.screen.get('app', '')}")


def is_risky(action):
    if action.get("type") != "press":
        return False
    label = next((e["label"] for e in S.screen.get("elements", []) if e["id"] == action.get("id")), "")
    return any(w in (label + " " + action.get("label", "")).lower() for w in RISKY_WORDS)


async def run_action(action):
    S.status = "Doing it..."
    await broadcast()
    res = await sidecar.act({k: v for k, v in action.items() if k != "label"})
    if not res.get("ok"):
        print(f"[act] failed: {res}")
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
        await say(tile["value"])
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
        elif v == "computer":
            await show_actions()
        elif v == "repeat" and S.last_said:
            await sidecar.speak(S.last_said)
            go_listen()
        else:
            go_listen()
    elif k == "action":
        action = tile["value"]
        if is_risky(action):
            S.pending = action
            set_tiles([{"label": "Yes, do it", "kind": "confirm", "value": True},
                       {"label": "No", "kind": "confirm", "value": False}],
                      mode="computer", status=f"Confirm: {action['label']}?")
            await broadcast()
            await sidecar.speak(f"{action['label']}?")
        else:
            await run_action(action)
    elif k == "confirm":
        action, S.pending = S.pending, None
        if tile["value"] and action:
            await run_action(action)
        else:
            await show_actions()
    elif k == "done":
        go_listen()


async def on_gesture(gesture):
    if lock.locked():
        return   # busy (thinking/speaking): drop the gesture instead of queueing it
    async with lock:
        await handle(gesture)
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
        S.history.append({"who": "friend", "text": text})
        S.pregen = None
        if S.pregen_task:
            S.pregen_task.cancel()
        S.pregen_task = asyncio.create_task(pregenerate(len(S.history)))
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
            elif msg.get("type") == "recalibrate":
                await recalibrate()
            elif msg.get("type") == "reset":
                S.history.clear()
                go_listen()
                await broadcast()
    except WebSocketDisconnect:
        clients.discard(ws)


@app.get("/")
async def index():
    return FileResponse(config.UI_DIR / "index.html")


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
    print(f"[hub] mock sidecar: {config.MOCK_SIDECAR}  camera: {config.USE_CAMERA}")
