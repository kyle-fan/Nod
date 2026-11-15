"""The hub's whole loop with the pretend Mac (hub/sidecar.py FakeMac) and a stand-in for
Bonsai, so it runs in a second with no model. What is tested is everything around the
model: the state machine, the filtering of steps, confirmations, and recovery from errors.

Run: .venv/bin/python tests/test_hub_flow.py
"""
import asyncio
import json
import os
import re
import sys
from pathlib import Path

os.environ.update(MOCK_SIDECAR="1", USE_CAMERA="0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hub import brain, config, main, sidecar   # noqa: E402

CALLS = []


async def fake_chat(prompt, max_tokens=220, schema=None, slot=None):
    """Answers like a sensible Bonsai would, from what the prompt says."""
    fixed, user = prompt if isinstance(prompt, tuple) else ("", prompt)
    CALLS.append(slot)
    if slot == "task":
        last = user.strip().splitlines()[-4:]
        text = " ".join(last).lower()
        return json.dumps({"task": "message" if ("tell " in text or "text " in text) else
                           "computer" if "put " in text else "none"})
    if slot == "reply":
        return json.dumps({"replies": ["yeah one sec", "what time", "nah"]})
    if slot == "intent":
        return json.dumps({"question": "In or out?", "options": ["Say yes", "Say no", "Ask when"]})
    if slot == "message":
        return json.dumps({"replies": ["come back", "you coming", "game's starting"]})
    if slot == "actions":
        lines = {m.group(1): m.group(2) for m in re.finditer(r"^((?:press|type) \d+|open \w+) = (.*)$", user, re.M)}
        done = user.split("Steps already done just now:")[1].split("App in front")[0]
        want_safari = "put the" in user or "Goal: put" in user
        order = []
        if want_safari:
            for code, text in lines.items():
                if "Yankees" in text and "Pressed" not in done:
                    order.append(code)
            order += [c for c, t in lines.items() if '"Play"' in t]
            order += [c for c in lines if c == "open Safari"]
        else:
            order += [c for c, t in lines.items() if c.startswith("type") and "(empty)" in t and "Message" in t]
            order += [c for c, t in lines.items() if '"Andrew"' in t]
            order += [c for c, t in lines.items() if '"Send"' in t]
            order += [c for c in lines if c == "open Messages"]
        order += [c for c in lines if c not in order]
        out = {"finished": False, "steps": (order * 3)[:3]}
        if "goal" in schema["properties"]:
            out = {"goal": "put the yankees game on" if want_safari else "text Andrew: come back",
                   "app": "Safari" if want_safari else "Messages", **out}
        return json.dumps(out)
    raise AssertionError(f"unexpected prompt kind {slot}")


async def no_wait_speak(text):
    SPOKEN.append(text)


SPOKEN = []
brain._chat = fake_chat
sidecar.speak = no_wait_speak
config.PREGEN_PAUSE_S = 0.01
S = main.S


def labels():
    return [t["label"] for t in S.tiles]


async def fresh(app="Messages"):
    config.MOCK_APP = app
    sidecar.FAKE.reset()
    S.history.clear()
    S.pregen = None
    main.go_listen()
    SPOKEN.clear()
    CALLS.clear()


async def friend(text):
    await main.heard(main.Heard(text=text, final=True))
    await asyncio.sleep(0.05)   # lets the pre-generation finish


async def g(*names):
    for n in names:
        await main.on_gesture(n)


async def test_talk_turn():
    await fresh()
    await friend("want to grab food")
    await g("nod")
    assert S.mode == "choose" and labels() == ["yeah one sec", "what time", "nah", "More..."]
    assert "then" not in S.tiles[0]
    await g("right", "select")
    assert SPOKEN == ["what time"] and S.mode == "listen"
    assert S.history[-1] == {"who": "me", "text": "what time"}


async def test_texting_chain_sends_after_confirmation():
    await fresh()
    await friend("the game's about to start, can you tell Andrew to come back")
    await g("nod")
    assert S.tiles[0].get("then") == "computer"
    await g("select")                                   # "yeah one sec", then computer mode
    assert S.mode == "computer" and labels()[0] == "Andrew", labels()
    await g("select")                                   # open Andrew's chat
    assert labels()[:3] == ["come back", "you coming", "game's starting"], labels()
    await g("select")                                   # type the first wording
    assert labels()[0] == "Send", labels()
    assert S.view()["task"]["goal"] == "text Andrew: come back"
    await g("select")                                   # Send -> asks first
    assert S.status == 'Send "come back" to Andrew?' and labels() == ["Yes, do it", "No"]
    assert S.index == 1, "No must be highlighted"
    assert sidecar.FAKE.sent == []
    await g("left", "select")
    assert sidecar.FAKE.sent == [("Andrew", "come back")]
    assert S.mode == "listen" and S.status == "Done"


async def test_no_at_confirmation_sends_nothing():
    await fresh()
    await friend("can you tell Andrew to come back")
    await g("nod", "select", "select", "select", "select")
    assert labels() == ["Yes, do it", "No"]
    await g("select")                                   # "No" is highlighted
    assert sidecar.FAKE.sent == [] and S.mode == "computer"
    assert "Send" in labels()


async def test_opens_the_right_app_first():
    await fresh(app="Safari")
    await friend("can you tell Andrew to come back")
    await g("nod", "select")
    assert labels()[0] == "Open Messages", labels()
    await g("select")
    assert labels()[0] == "Andrew", labels()


async def test_game_chain_ends_with_done_first():
    await fresh(app="Safari")
    await friend("can you put the yankees game on")
    await g("nod", "select")
    assert labels()[0].startswith("Yankees vs"), labels()
    await g("select")
    assert labels()[0] == "Play", labels()
    await g("select")
    assert S.status == "That looks done" and labels()[0] == "Done", (S.status, labels())
    await g("select")
    assert S.mode == "listen"


async def test_a_pressed_control_is_not_offered_again():
    await fresh()
    await friend("can you tell Andrew to come back")
    await g("nod", "select", "select")
    assert "Andrew" not in labels()


async def test_more_wordings():
    await fresh()
    await friend("can you tell Andrew to come back")
    await g("nod", "select", "select", "select")       # typed "come back"
    assert "Write a different message" in labels(), labels()
    i = labels().index("Write a different message")
    await g(*["right"] * i, "select")
    assert S.status == "Pick the wording" and labels()[-1] == "More wordings"
    assert "come back" not in labels(), "the wording already typed is not offered again"


async def test_quick_phrases_need_no_model():
    await fresh()
    CALLS.clear()
    await g("cancel")
    assert labels() == ["Talk", "Quick phrases", "Use computer", "Repeat last", "Back"]
    await g("right", "select")
    assert S.status == "Quick phrases" and labels()[:3] == ["Yes", "No", "Thank you"]
    await g("right", "right", "right", "select")        # More phrases
    assert labels()[0] == "I need help"
    await g("select")
    assert SPOKEN == ["I need help"] and S.mode == "listen"
    assert CALLS == [], "quick phrases must not call Bonsai"


async def test_dead_voice_helper_does_not_freeze_the_hub():
    await fresh()

    async def broken(text):
        raise ConnectionError("sidecar is restarting")
    old, sidecar.speak = sidecar.speak, broken
    try:
        await friend("want to grab food")
        await g("nod", "select")
        assert S.mode == "listen", S.mode
        assert S.stats["sidecar_ok"] is False
    finally:
        sidecar.speak = old
    await friend("you there")
    await g("nod")
    assert S.mode == "choose"


async def test_bonsai_down_still_gives_tiles():
    await fresh()

    async def down(*a, **k):
        raise ConnectionError("no model")
    old, brain._chat = brain._chat, down
    try:
        await friend("want to grab food")
        await g("nod")
        assert S.mode == "choose" and labels()[:3] == ["Yes", "No", "Give me a second"]
        assert S.stats["bonsai_ok"] is False
    finally:
        brain._chat = old


async def test_more_narrows_down():
    await fresh()
    await friend("want to grab food")
    await g("nod", "right", "right", "right", "select")     # More...
    assert S.status == "In or out?" and labels() == ["Say yes", "Say no", "Ask when", "Something else"]
    await g("select")
    assert S.mode == "choose" and labels()[-1] == "More..."


async def test_open_mouth_goes_back():
    await fresh()
    await friend("want to grab food")
    await g("nod", "cancel")
    assert S.mode == "listen" and S.tiles == []


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]


async def run_all():
    failed = 0
    for t in TESTS:
        try:
            await t()
            print("ok  ", t.__name__)
        except Exception as e:   # noqa: BLE001
            failed += 1
            print("FAIL", t.__name__, repr(e))
    return failed

if __name__ == "__main__":
    sys.exit(1 if asyncio.run(run_all()) else 0)
