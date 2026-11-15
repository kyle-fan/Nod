"""The fixed rules around Bonsai's answers: who a message is for, when a task is finished,
how names in transcripts and prompts are handled. No model needed."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hub import brain   # noqa: E402

LIST = [{"id": 1, "role": "button", "label": "Andrew"}, {"id": 2, "role": "button", "label": "Sam"},
        {"id": 3, "role": "button", "label": "Mom"}, {"id": 4, "role": "textfield", "label": "Search", "value": ""},
        {"id": 5, "role": "button", "label": "New Message"}, {"id": 6, "role": "button", "label": "Send"},
        {"id": 7, "role": "button", "label": "Caden Ruan"}]


def said(text):
    return [{"who": "friend", "text": text}, {"who": "me", "text": "one sec"}]


def named(text):
    e = brain._named_on_screen(said(text), LIST)
    return e["label"] if e else None


def test_recipient_comes_from_what_the_friend_said():
    assert named("let mom know I'll call her tonight") == "Mom"
    assert named("can you tell Andrew to come back") == "Andrew"
    assert named("text sam that we're late") == "Sam"
    assert named("can you tell Caden to come back") == "Caden Ruan"      # first name is enough
    assert named("send Andrew a text") == "Andrew"                       # "send" is not a person
    assert named("can you tell him to come back") is None
    assert named("tell Sam and Andrew the food is here") is None         # two people: leave it to Bonsai
    assert named("search for the new message") is None


def test_goal_is_rewritten_for_the_named_person():
    assert brain._goal_for("text Andrew: I'll call mom tonight", "Mom") == "text Mom: I'll call mom tonight"
    assert brain._goal_for("text Mom: running late", "Mom") == "text Mom: running late"
    assert brain._goal_for("reply to Andrew: yes", "Sam") == "reply to Sam: yes"
    assert brain._goal_for("say hi", "Sam") == "text Sam: say hi"


def test_goal_person():
    g = brain._goal_person
    assert g("text Andrew: come back") == "Andrew"
    assert g("reply to Sammy Tong's text") == "Sammy Tong"
    assert g("text my mom: running late") == "mom"
    assert g("text him: come back") == "" and g("put the game on") == "" and g("look up the score") == ""
    assert g("text Zoe happy birthday") == "Zoe" and g("text Quentin hi") == "Quentin"
    assert g("text mom happy birthday") == "mom" and g("reply to Andrew") == "Andrew"
    assert g("text Caden Ruan: come back") == "Caden Ruan" and g("respond to Tiffany Sun's message") == "Tiffany Sun"


def test_same_person():
    s = brain._same_person
    assert s("Andrew", "Andrew") and s("mom", "Mom") and s("Caden", "Caden Ruan") and s("Caden Ruan", "Caden")
    assert not s("Andrew", "Sam") and not s("", "Sam") and not s("Sam", "Sammy")


def test_messages_are_written_to_the_person_not_about_them():
    f = brain._second_person
    assert f("i'll call her tonight") == "i'll call you tonight"
    assert f("what time he's getting back") == "what time you're getting back"
    assert f("when is he coming back") == "when are you coming back"
    assert f("is she still coming tonight") == "are you still coming tonight"
    assert f("does he want pizza") == "do you want pizza"
    assert f("he wants pizza") == "you want pizza"
    assert f("tell him the food is here") == "tell you the food is here"   # such drafts are dropped anyway
    assert f("bring her charger") == "bring your charger"
    assert f("calling her later") == "calling you later"
    assert f("when's he getting back") == "when are you getting back"
    assert f("where's she at") == "where are you at"
    assert f("waiting for him") == "waiting for you"
    assert f("game's starting, come back") == "game's starting, come back"
    assert f("do you want pizza") == "do you want pizza"
    assert f("the weather is nice") == "the weather is nice"


def test_long_screens_are_cut_down_but_keep_what_was_named():
    names = ["Person %d" % i for i in range(1, 30)] + ["Zed Marlow"]
    screen = [{"id": i + 1, "role": "button", "label": n} for i, n in enumerate(names)]
    short = brain._shortlist(screen, said("can you tell Zed to come back"), "")
    labels = [e["label"] for e in short]
    assert len(short) <= 18 and "Zed Marlow" in labels and labels[:3] == ["Person 1", "Person 2", "Person 3"], labels
    assert brain._shortlist(screen[:10], said("x"), "") == screen[:10]          # short screens are untouched
    page = [{"id": 1, "role": "textfield", "label": "Address", "value": ""}] + \
           [{"id": i + 2, "role": "link", "label": "Story %d" % i} for i in range(40)] + \
           [{"id": 99, "role": "link", "label": "Yankees vs Red Sox: watch live"}, {"id": 100, "role": "button", "label": "Play"}]
    labels = [e["label"] for e in brain._shortlist(page, said("put the yankees game on"), "")]
    assert "Yankees vs Red Sox: watch live" in labels and "Play" in labels and "Address" in labels, labels


def test_a_request_split_by_a_pause_is_read_as_one():
    h = [{"who": "friend", "text": "hey"}, {"who": "me", "text": "hey"},
         {"who": "friend", "text": "Can you tell Andrew"}, {"who": "friend", "text": "to come back?"},
         {"who": "me", "text": "one sec"}]
    assert brain.friend_said(h) == "Can you tell Andrew to come back?"
    assert brain._named_on_screen(h, LIST)["label"] == "Andrew"
    assert brain.friend_said([]) == "" and brain.friend_said([{"who": "me", "text": "x"}]) == ""
    # an older turn is not mixed in
    assert brain.friend_said(h[:2] + [{"who": "friend", "text": "bye"}]) == "bye"


def test_example_lines_are_only_allowed_in_their_own_conversation():
    p = {"examples": [{"friend": "I think I failed my midterm", "replies": ["want to get food about it", "that sucks, which class"]},
                      {"friend": "want to grab food", "replies": ["yeah I could eat"]}]}
    sad = brain._misplaced_examples(p, said("my grandma's in the hospital"))
    assert "want to get food about it" in sad and "yeah i could eat" in sad
    assert brain._pasted("want to grab food about it", sad)            # a near copy counts too
    assert not brain._pasted("how is she doing", sad)
    same = brain._misplaced_examples(p, said("I think I failed my midterm"))
    assert "want to get food about it" not in same                     # there it is his answer
    food = brain._misplaced_examples(p, said("want to grab food later?"))
    assert "yeah i could eat" not in food and "that sucks, which class" in food


def test_finished_rules():
    assert brain._looks_finished("put the yankees game on", ['Pressed "Yankees vs Red Sox"', 'Pressed "Play"'])
    assert not brain._looks_finished("put the yankees game on", ['Pressed "Yankees vs Red Sox"'])
    assert brain._looks_finished("look up MLB standings", ['Pressed "MLB Standings"'])
    assert not brain._looks_finished("text Andrew: come back", ['Pressed "Andrew"'])
    assert not brain._looks_finished("put the game on", [])


def test_prompt_files_split_into_fixed_and_changing_parts():
    p = brain.load_profile()
    for name, kw in [("reply.txt", dict(kind_instruction="", learned="")),
                     ("message.txt", dict(app="Messages", field="Message Andrew", goal="g", avoid="")),
                     ("textreply.txt", dict(who="Andrew", text="hi", avoid="")),
                     ("textstart.txt", dict(who="Andrew", relation="", avoid="")),
                     ("intent.txt", dict(path="", rejected="", seen="")),
                     ("task.txt", {}),
                     ("actions.txt", dict(habits="", goal_line="", done="", app="Messages", elements="", json_shape="{}"))]:
        fixed, changing = brain._prompt(name, name=p["name"], profile="PROFILE", style="STYLE",
                                        examples="EX", history="THE CONVERSATION", **kw)
        assert fixed and changing, name
        # what changes every call must never be in the cached part
        assert "THE CONVERSATION" not in fixed and "THE CONVERSATION" in changing, name


def test_history_shows_texts():
    h = [{"who": "friend", "text": "hey"}, {"who": "text", "from": "Andrew", "text": "where are you"},
         {"who": "me_text", "to": "Andrew", "text": "in my room"}, {"who": "me", "text": "yeah"}]
    out = brain._history_text(h, "Theo")
    assert out.splitlines() == ["Friend: hey", '(Andrew texted Theo: "where are you")',
                                '(Theo texted Andrew: "in my room")', "Theo: yeah"]


def test_demo_guide_matches_the_rehearsed_lines_and_nothing_else():
    from hub import config
    old, config.DEMO_GUIDE = config.DEMO_GUIDE, True
    try:
        g = brain.demo_guide
        assert g("talk", "hey, do you want to get lunch?")[0] == "yeah, I'm starving"
        assert g("talk", "do you want thai or pizza, or anything else?") == ["thai sounds good", "pizza, honestly", "either works, you pick"]
        assert g("talk", "want to watch the Yankees game tonight?")[0] == "yeah, I'm in"
        assert g("talk", "can you put the Yankees game on") == []          # a request is not small talk
        assert g("talk", "can you tell Andrew to come back for lunch") == []
        assert g("talk", "my grandma's in the hospital") == []
        assert g("text_in", "are we still getting lunch?")[0] == "yeah, still down"
        assert g("text_in", "on my way back, save me a seat")[0] == "cool, see you soon"   # "seat" is not "eat"
        assert g("text_in", "can I borrow your charger") == []
        assert g("pass_on", "the game's about to start, can you tell Andrew to come back")[0] == "game's starting, come back"
        assert g("pass_on", "can you tell Andrew we're getting thai for lunch")[0] == "we're getting thai for lunch"
        assert g("pass_on", "tell Sam to bring the controller") == []
        assert g("request", "can you put the Yankees game on")[0] == "yeah, one sec"
        for kind in ("talk", "request", "text_in", "pass_on"):       # every scripted line fits the tile and has no "!"
            import json
            for sc in json.loads(config.DEMO_PATH.read_text())[kind]:
                assert len(sc["say"]) == 3 and all(len(x) <= 70 and "!" not in x for x in sc["say"]), sc
        config.DEMO_GUIDE = False
        assert g("talk", "hey, do you want to get lunch?") == []
    finally:
        config.DEMO_GUIDE = old


def test_only_the_newest_request_decides_the_task():
    h = [{"who": "friend", "text": "can you tell Andrew to come to lunch"}, {"who": "me", "text": "sure"},
         {"who": "me_text", "to": "Andrew", "text": "want lunch"}, {"who": "text", "from": "Andrew", "text": "yes"},
         {"who": "friend", "text": "can you put"}, {"who": "friend", "text": "the Yankees game on"}, {"who": "me", "text": "one sec"}]
    assert [t["text"] for t in brain._this_request(h)] == ["can you put", "the Yankees game on", "one sec"]
    assert brain._this_request(h[:2]) == h[:2] and brain._this_request([]) == []
    for line in ("can you tell Andrew to come back", "text Sam we're late", "let mom know I'll call", "ask him if he wants pizza"):
        assert brain.MESSAGING.search(line), line
    for line in ("can you put the Yankees game on", "pull up the game", "look up the score", "open Safari"):
        assert not brain.MESSAGING.search(line), line


def test_step_codes():
    c = brain._step_choices(LIST[:4], ["Safari"])
    assert list(c) == ["press 1", "press 2", "press 3", "type 4", "open Safari"]
    assert c["type 4"] == {"type": "type", "id": 4}


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("ok  ", name)
            except AssertionError as e:
                failed += 1
                print("FAIL", name, e)
    sys.exit(1 if failed else 0)
