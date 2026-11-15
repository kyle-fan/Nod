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
    assert f("waiting for him") == "waiting for you"
    assert f("game's starting, come back") == "game's starting, come back"
    assert f("do you want pizza") == "do you want pizza"
    assert f("the weather is nice") == "the weather is nice"


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
