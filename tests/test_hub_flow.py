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
LAST_MESSAGE_PROMPT = []


async def fake_chat(prompt, max_tokens=220, schema=None, slot=None, on_text=None):
    """Answers like a sensible Bonsai would, from what the prompt says. With on_text it
    arrives in small pieces, the way the real server streams."""
    out = await fake_answer(prompt, schema, slot)
    if on_text:
        for i in range(8, len(out) + 8, 8):
            await on_text(out[:i])
            await asyncio.sleep(0)
    return out


async def fake_answer(prompt, schema, slot):
    fixed, user = prompt if isinstance(prompt, tuple) else ("", prompt)
    CALLS.append(slot)
    if slot == "task" and "tasks" in (schema or {}).get("properties", {}):
        return json.dumps({"tasks": [{"goal": "text Andrew", "app": "Messages"},
                                     {"goal": "put the yankees game on", "app": "Safari"},
                                     {"goal": "look up the score", "app": "Safari"}]})
    if slot == "task":
        said = [l for l in user.splitlines() if l.startswith("Friend: ")]
        text = (said[-1] if said else "").lower()
        return json.dumps({"task": "message" if ("tell " in text or "text " in text) else
                           "computer" if "put " in text else "none"})
    if slot == "reply":
        return json.dumps({"replies": ["yeah one sec", "what time", "nah"]})
    if slot == "intent":
        return json.dumps({"question": "In or out?", "options": ["Say yes", "Say no", "Ask when"]})
    if slot == "textreply" and "You're texting" in user:
        assert "You're texting Andrew (roommate and best friend)" in user, user
        return json.dumps({"replies": ["you around", "want food", "what's the plan tonight"]})
    if slot == "textreply":
        n = len(re.findall(r"none was right", user))
        return json.dumps({"replies": ["in my room", "why", "be there soon"] if not n
                           else ["at the dorm", "who's asking", "one minute"]})
    if slot == "message":
        LAST_MESSAGE_PROMPT.append(user)
        if "bring the controller" in user:     # the kinds of slips the real model makes
            return json.dumps({"replies": ["bring the controller", "on my way", "tell sam to bring it"]})
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
            who = "Coach" if "Coach" in user else "Andrew"
            out = {"goal": "put the yankees game on" if want_safari else f"text {who}: come back",
                   "app": "Safari" if want_safari else "Messages", **out}
        return json.dumps(out)
    raise AssertionError(f"unexpected prompt kind {slot}")


async def no_wait_speak(text):
    SPOKEN.append(text)


SPOKEN = []
brain._chat = fake_chat
sidecar.speak = no_wait_speak
config.PREGEN_PAUSE_S = 0.01
config.LEARNED_PATH = Path(__import__("tempfile").mkdtemp()) / "learned.json"   # never touch the real one
S = main.S


def labels():
    return [t["label"] for t in S.tiles]


async def fresh(app="Messages"):
    config.MOCK_APP = app
    sidecar.FAKE.reset()
    S.history.clear()
    S.pregen, S.incoming, S.texts_seen, S.paused = None, None, [], False
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
    assert (S.history[-1]["who"], S.history[-1]["text"]) == ("me", "what time")


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
    assert S.mode == "listen" and S.status == "Sent"
    assert S.history[-1] == {"who": "me_text", "to": "Andrew", "text": "come back"}


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


async def text_arrives(who="Andrew", text="where are you"):
    return await main.incoming({"from": who, "text": text})


async def test_incoming_text_is_answered_in_one_pick():
    await fresh(app="Safari")
    assert (await text_arrives())["ok"]
    assert S.status == "Andrew texted you. Nod to answer" and S.view()["incoming"]["from"] == "Andrew"
    await g("nod")
    assert S.status == "Text Andrew back", S.status
    assert labels() == ["in my room", "why", "be there soon", "Other answers", "Not now"], labels()
    await g("select")
    # one pick opened Messages, opened Andrew's chat and typed the answer; now it asks
    assert S.status == 'Send "in my room" to Andrew?', S.status
    assert labels() == ["Yes, do it", "No"] and S.index == 1
    assert sidecar.FAKE.sent == [] and sidecar.FAKE.app == "Messages" and sidecar.FAKE.draft == "in my room"
    assert S.view()["task"]["done"] == ['Opened Messages', 'Pressed "Andrew"', 'Typed "in my room" into "Message Andrew"']
    await g("left", "select")
    assert sidecar.FAKE.sent == [("Andrew", "in my room")]
    assert S.mode == "listen" and S.status == "Sent" and S.incoming is None
    assert S.history[-2:] == [{"who": "text", "from": "Andrew", "text": "where are you"},
                              {"who": "me_text", "to": "Andrew", "text": "in my room"}]
    await g("nod")                                     # answered: a nod is a normal talk turn again
    assert labels()[-1] == "More..."


async def test_a_very_long_or_odd_incoming_text_is_kept_short_and_harmless():
    await fresh(app="Safari")
    assert (await main.incoming({"from": "Andrew", "text": "a" * 20000}))["ok"]
    assert len(S.incoming["text"]) == main.MAX_TEXT_CHARS and len(S.history[-1]["text"]) == main.MAX_TEXT_CHARS
    assert (await main.incoming({"from": "Andrew"})) == {"ok": False, "error": "need from and text"}
    assert (await main.incoming({"text": 5}))["ok"] is False
    await g("nod")
    assert S.status == "Text Andrew back", S.status


async def test_incoming_text_other_answers_and_not_now():
    await fresh()
    await text_arrives()
    await g("nod", "right", "right", "right", "select")          # Other answers
    assert labels()[:3] == ["at the dorm", "who's asking", "one minute"], labels()
    await g("right", "right", "right", "right", "select")        # Not now
    assert S.mode == "listen" and S.incoming is None and S.status == "Listening"
    assert sidecar.FAKE.sent == []


async def test_same_text_reported_twice_counts_once():
    await fresh()
    await text_arrives()
    assert (await text_arrives()).get("duplicate")
    assert sum(1 for t in S.history if t["who"] == "text") == 1


async def test_talking_after_a_text_goes_back_to_talk_replies():
    await fresh()
    await text_arrives()
    await friend("want to grab food")
    await g("nod")
    assert labels() == ["yeah one sec", "what time", "nah", "More..."]


async def test_text_from_unknown_chat_falls_back_to_steps():
    await fresh()
    await text_arrives(who="Coach", text="where are you")
    await g("nod", "select")
    # there is no chat called Coach on the pretend Mac: it hands over to step-by-step tiles
    assert S.mode == "computer" and S.status == "Pick the next step", (S.mode, S.status)
    assert sidecar.FAKE.sent == []


async def test_picked_replies_are_remembered_and_shown_to_bonsai():
    await fresh()
    config.LEARNED_PATH.unlink(missing_ok=True)
    await friend("want to grab food")
    await g("nod", "right", "select")                   # picks "what time"
    assert brain.load_learned() == [{"friend": "want to grab food", "reply": "what time"}]
    assert 'to "want to grab food" he chose: what time' in brain._learned_text()
    await g("cancel", "right", "select", "select")      # a quick phrase is not learned
    assert len(brain.load_learned()) == 1
    await friend("can you tell Andrew to come back")    # agreeing to a task is not learned either
    await g("nod", "select")
    assert len(brain.load_learned()) == 1


async def test_text_someone_from_the_menu():
    await fresh(app="Safari")
    await g("cancel", "right", "right", "select")            # Menu -> Text someone
    assert S.status == "Text who?" and labels() == ["Andrew", "Someone else", "Back"], labels()
    await g("select")
    assert S.status == "Text Andrew" and labels()[:3] == ["you around", "want food", "what's the plan tonight"]
    await g("right", "select")
    assert S.status == 'Send "want food" to Andrew?', S.status
    assert S.view()["task"]["goal"] == "text Andrew"
    await g("left", "select")
    assert sidecar.FAKE.sent == [("Andrew", "want food")] and S.status == "Sent"


async def test_stop_listening_drops_what_is_said():
    await fresh()
    await g("cancel", "right", "right", "right", "select")   # Menu -> More
    assert labels() == ["Use computer", "Repeat last", "Stop listening", "Back"]
    await g("right", "right", "select")
    assert S.paused and S.status == "Not listening. Nod to listen again"
    await friend("this is private")
    await main.heard(main.Heard(text="half a sentence", final=False))
    assert S.history == [] and S.partial == ""
    await g("nod")
    assert not S.paused and S.status == "Listening" and S.mode == "listen"
    await friend("want to grab food")
    assert len(S.history) == 1


async def test_tiles_left_alone_are_put_away():
    await fresh()
    await friend("want to grab food")
    await g("nod")
    assert S.mode == "choose"
    old = main.TILES_TIMEOUT_S
    main.TILES_TIMEOUT_S = 0.05
    S.last_input -= 1
    task = asyncio.create_task(main.put_stale_tiles_away())
    try:
        await asyncio.sleep(2.3)
        assert S.mode == "listen" and S.tiles == []
    finally:
        task.cancel()
        main.TILES_TIMEOUT_S = old


async def test_screen_that_cannot_be_read_says_why():
    await fresh()

    async def blind():
        return {"app": "", "elements": [], "error": "Accessibility permission missing."}
    old, sidecar.screen = sidecar.screen, blind
    try:
        await friend("can you tell Andrew to come back")
        await g("nod", "select")
        assert S.mode == "listen" and "Accessibility" in S.status, S.status
    finally:
        sidecar.screen = old


async def test_click_or_number_key_picks_a_tile():
    await fresh()
    await friend("want to grab food")
    await g("nod")
    await main.on_pick(2)
    assert SPOKEN == ["nah"] and S.mode == "listen"
    await main.on_pick(0)                               # nothing on screen: ignored
    assert S.mode == "listen"


async def test_confirmation_can_be_answered_while_it_is_spoken():
    await fresh()
    gate = asyncio.Event()

    async def slow(text):
        SPOKEN.append(text)
        if text.startswith("Send"):
            await gate.wait()                           # still talking
    old, sidecar.speak = sidecar.speak, slow
    try:
        await friend("can you tell Andrew to come back")
        await g("nod", "select", "select", "select", "select")
        assert labels() == ["Yes, do it", "No"]
        await g("left", "select")                       # answered before the question finished
        assert sidecar.FAKE.sent == [("Andrew", "come back")]
    finally:
        gate.set()
        sidecar.speak = old


async def test_replies_follow_the_friend_if_they_keep_talking():
    await fresh()
    await friend("want to grab food")
    await g("nod")
    CALLS.clear()
    await friend("actually never mind, I'm not hungry")     # he hasn't touched the tiles
    await asyncio.sleep(0.05)
    assert "reply" in CALLS and S.mode == "choose", CALLS
    await g("right")                                         # now he is choosing
    CALLS.clear()
    await friend("or maybe later")
    await asyncio.sleep(0.05)
    assert CALLS == [] and S.index == 1, "tiles he is choosing from must not change under him"


async def test_replies_appear_one_by_one_while_thinking():
    await fresh()
    S.history.append({"who": "friend", "text": "want to grab food", "at": 1.0})   # no pre-generation
    seen = []
    old = main.broadcast

    async def spy():
        if S.mode == "thinking":
            seen.append(tuple(labels()))
        await old()
    main.broadcast = spy
    try:
        await g("nod")
    finally:
        main.broadcast = old
    assert ("yeah one sec",) in seen and ("yeah one sec", "what time") in seen, seen
    assert S.mode == "choose" and labels() == ["yeah one sec", "what time", "nah", "More..."]
    await g("select")          # only after they are all there can he pick
    assert SPOKEN == ["yeah one sec"]


def test_finished_strings():
    f = brain.finished_strings
    assert f('{"replies": ["yeah one sec", "what ti') == ["yeah one sec"]
    assert f('{"replies": ["a", "b \\"quoted\\"", "c"]}') == ["a", 'b "quoted"', "c"]
    assert f('{"repl') == [] and f('{"replies": [') == []


async def open_use_computer():
    await g("cancel", "right", "right", "right", "select")      # Menu -> More
    await g("select")                                           # Use computer


async def test_use_computer_with_no_request_offers_things_to_do():
    await fresh(app="Safari")
    await open_use_computer()
    assert S.status == "Do what?", S.status
    assert labels() == ["text Andrew", "put the yankees game on", "look up the score", "Just show the screen", "Back"]
    await g("right", "select")                                  # put the yankees game on
    assert S.mode == "computer" and labels()[0].startswith("Yankees vs"), labels()
    assert S.view()["task"]["goal"] == "put the yankees game on"


async def test_use_computer_text_task_takes_the_texting_route():
    await fresh()
    await open_use_computer()
    await g("select")                                           # text Andrew
    assert S.status == "Text Andrew" and labels()[0] == "you around", (S.status, labels())


async def test_use_computer_right_after_a_request_does_that_request():
    await fresh()
    await friend("can you tell Andrew to come back")
    await open_use_computer()
    assert S.mode == "computer" and labels()[0] == "Andrew", (S.status, labels())


async def test_no_chat_for_that_person_offers_nobody_else():
    """The friend names someone who has no chat on screen. Bonsai (here, the stand-in) would
    press Andrew anyway; the guard must not let another person's chat be the next step."""
    await fresh()
    await friend("can you tell Coach to come back")
    await g("nod", "select")
    assert S.mode == "computer" and labels() == ["Done"], labels()
    assert S.status == "Can't see a chat with Coach in Messages", S.status
    assert sidecar.FAKE.sent == [] and sidecar.FAKE.chat is None


async def test_another_persons_open_chat_is_never_typed_into():
    await fresh()
    sidecar.FAKE.chat = "Sam"                               # Sam's chat happens to be open
    await friend("can you tell Andrew to come back")
    await g("nod", "select")
    assert labels()[0] == "Back to conversations", labels()
    assert not any(t["kind"] == "redraft" or t["label"] == "Send" for t in S.tiles), labels()
    await g("select")
    assert labels()[0] == "Andrew", labels()


async def test_express_texting_is_three_picks():
    await fresh(app="Safari")
    config.EXPRESS = True
    try:
        await friend("the game's about to start, can you tell Andrew to come back")
        await g("nod", "select")                                # 1: "yeah one sec"
        assert S.status == "Text Andrew", S.status
        assert labels()[:3] == ["come back", "you coming", "game's starting"], labels()
        await g("select")                                       # 2: the wording
        assert S.status == 'Send "come back" to Andrew?'
        await g("left", "select")                               # 3: yes
        assert sidecar.FAKE.sent == [("Andrew", "come back")]
        # someone not in his profile still goes step by step
        await fresh()
        await friend("can you tell Sam we're late")
        await g("nod", "select")
        assert S.mode == "computer" and labels()[0] == "Sam"
    finally:
        config.EXPRESS = False


async def test_settings_can_be_switched_while_running():
    await fresh()
    try:
        want = {"express": True, "learn": False, "classic_gestures": False}
        assert (await main.set_settings({"express": True, "learn": False})) == want
        assert S.view()["settings"] == want
        await friend("can you tell Andrew to come back")
        await g("nod", "select")
        assert S.status == "Text Andrew"
    finally:
        await main.set_settings({"express": False, "learn": True})


async def test_passing_something_on_ignores_the_other_persons_last_text():
    """Andrew's last text is unanswered, but the friend asked to tell him something else:
    the message prompt must be about the friend's request, not about answering that text."""
    await fresh()
    sidecar.FAKE.received["Andrew"] = "what's for dinner"
    await friend("can you tell Andrew to come back")
    await g("nod", "select", "select")
    assert "who asked you" not in LAST_MESSAGE_PROMPT[-1], LAST_MESSAGE_PROMPT[-1]
    assert 'labeled "Message Andrew"' in LAST_MESSAGE_PROMPT[-1]


async def test_only_the_named_persons_chat_is_offered():
    await fresh()
    await friend("can you tell Andrew to come back")
    await g("nod", "select")
    assert "Andrew" in labels() and "Sam" not in labels() and "Mom" not in labels(), labels()


async def test_wordings_that_are_not_messages_to_the_person_are_dropped():
    drafts = await brain._draft_messages(brain.load_profile(), [{"who": "friend", "text": "tell Sam to bring the controller"}],
                                         "Messages", "Message Sam", "text Sam")
    assert drafts == ["bring the controller"], drafts


async def test_first_computer_step_is_ready_before_he_agrees():
    await fresh()
    await friend("can you tell Andrew to come back")       # pre-generation also prepares step 1
    await asyncio.sleep(0.05)
    assert S.prepared is not None and S.prepared_for == 1
    CALLS.clear()
    await g("nod", "select")
    assert labels()[0] == "Andrew", labels()
    assert "actions" not in CALLS, "the first step was already worked out"
    # a prepared step for an old line is not reused for a new request
    await fresh()
    await friend("can you tell Andrew to come back")
    await asyncio.sleep(0.05)
    await friend("actually can you put the yankees game on")
    await asyncio.sleep(0.05)
    await g("nod", "select")
    assert S.view()["task"]["goal"] == "put the yankees game on", S.view()["task"]


async def test_a_smile_right_as_tiles_appear_does_not_pick():
    await fresh()
    await friend("want to grab food")
    await g("nod")
    await main.on_gesture("select", source="camera")        # he was already smiling at the friend
    assert S.mode == "choose" and SPOKEN == []
    S.tiles_at -= 1                                          # a moment later it is a real choice
    await main.on_gesture("select", source="camera")
    assert SPOKEN == ["yeah one sec"]


class RealShapedMac(sidecar.FakeMac):
    """What the Swift sidecar actually returns for Messages on macOS 26 (seen on the demo Mac):
    the chat list is names only, with no search box or compose button, and an open chat is
    Back, the message box (whose label mentions an unanswered text) and Send."""
    NAMES = ["Alan", "Sammy", "Harker Twins", "Tiffany Sun", "Caden Ruan", "Kairui Sun", "Andrew", "Mom"]

    def screen(self):
        if self.app != "Messages":
            return {"app": self.app, "elements": [{"id": 1, "role": "textfield", "label": "Address and search bar", "value": ""},
                                                  {"id": 2, "role": "button", "label": "Reload"}]}
        if self.chat:
            asked = self.received.get(self.chat)
            box = f"Message {self.chat}" + (f": your answer to {self.chat}, who asked you '{asked}'" if asked else "")
            return {"app": "Messages", "elements": [
                {"id": 1, "role": "button", "label": "Back to conversations"},
                {"id": 2, "role": "textfield", "label": box, "value": self.draft},
                {"id": 3, "role": "button", "label": "Send"}]}
        return {"app": "Messages", "elements": [{"id": i + 1, "role": "button", "label": n} for i, n in enumerate(self.NAMES)]}

    def act(self, action):
        if action.get("type") == "press" and self.app == "Messages" and not self.chat:
            el = next((e for e in self.screen()["elements"] if e["id"] == action.get("id")), None)
            if not el:
                return {"ok": False, "error": "unknown id"}
            self.chat = el["label"]
            return {"ok": True}
        return super().act(action)


async def with_real_shapes(app="Messages"):
    await fresh(app)
    mac = RealShapedMac()
    mac.app = app
    sidecar.FAKE = mac
    return mac


async def test_texting_chain_on_the_real_macs_shapes():
    old = sidecar.FAKE
    try:
        mac = await with_real_shapes()
        mac.received["Andrew"] = "what's for dinner"            # an old unanswered text in that chat
        await friend("the game's about to start, can you tell Andrew to come back")
        await g("nod", "select")
        assert labels() == ["Andrew", "Done"], labels()        # nobody else is offered
        await g("select")
        assert labels()[:3] == ["come back", "you coming", "game's starting"], labels()
        await g("select")
        assert labels()[0] == "Send", labels()
        await g("select")
        assert S.status == 'Send "come back" to Andrew?', S.status
        await g("left", "select")
        assert mac.sent == [("Andrew", "come back")] and S.status == "Sent"
    finally:
        sidecar.FAKE = old


async def test_answering_a_text_on_the_real_macs_shapes():
    old = sidecar.FAKE
    try:
        mac = await with_real_shapes(app="Safari")
        mac.chat = None
        await main.incoming({"from": "Andrew", "text": "where are you"})
        mac.received["Andrew"] = "where are you"
        await g("nod", "select")
        assert S.status == 'Send "in my room" to Andrew?', S.status
        await g("left", "select")
        assert mac.sent == [("Andrew", "in my room")]
        # and when another chat is open, it goes back to the list first
        mac.chat, mac.draft = "Mom", ""
        await main.incoming({"from": "Andrew", "text": "where are you now"})
        await g("nod", "select")
        assert S.status.startswith('Send "') and S.status.endswith('to Andrew?'), S.status
        assert mac.chat == "Andrew"
    finally:
        sidecar.FAKE = old


async def test_a_dropped_head_start_is_redone_not_fatal():
    await fresh()
    await friend("can you tell Andrew to come back")
    await asyncio.sleep(0.05)
    S.prepared.cancel()                                  # something cancelled the prepared step
    await asyncio.sleep(0)
    await g("nod", "select")
    assert S.mode == "computer" and labels()[0] == "Andrew", (S.mode, S.status, labels())


async def test_bonsai_down_during_a_computer_step_says_so():
    await fresh()
    await friend("can you tell Andrew to come back")
    await asyncio.sleep(0.05)
    await g("nod")

    async def down(*a, **k):
        raise ConnectionError("no model")
    old, brain._chat = brain._chat, down
    S.prepared, S.prepared_for = None, -1              # no head start: the step is worked out now
    try:
        await g("select")
        assert S.mode == "computer" and labels() == ["Done"], (S.mode, labels())
        assert "Bonsai" in S.status, S.status
    finally:
        brain._chat = old


async def test_request_heard_in_two_pieces_still_works():
    await fresh()
    await friend("can you tell Andrew")
    await friend("to come back")
    await g("nod")
    assert S.tiles[0].get("then") == "computer", labels()
    await g("select")
    assert labels() == ["Andrew", "New Message", "Done"] or labels()[0] == "Andrew", labels()


async def test_sent_message_settles_back_to_listening():
    await fresh()
    old = main.SETTLE_S
    main.SETTLE_S = 0.05
    try:
        await text_arrives()
        await g("nod", "select", "left", "select")
        assert S.status == "Sent"
        await text_arrives(text="you there?")              # another text came in meanwhile
        await asyncio.sleep(0.15)
        assert S.status == "Andrew texted you. Nod to answer", S.status
    finally:
        main.SETTLE_S = old


async def test_quick_phrases_need_no_model():
    await fresh()
    CALLS.clear()
    await g("cancel")
    assert labels() == ["Talk", "Quick phrases", "Text someone", "More", "Back"]
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
            r = t()
            if asyncio.iscoroutine(r):
                await r
            print("ok  ", t.__name__)
        except Exception as e:   # noqa: BLE001
            failed += 1
            print("FAIL", t.__name__, repr(e))
    return failed

if __name__ == "__main__":
    sys.exit(1 if asyncio.run(run_all()) else 0)
