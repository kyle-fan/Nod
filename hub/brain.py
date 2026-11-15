"""Bonsai calls: turn context into 3 options. Prompts live in prompts/*.txt."""
import json
import re
import time

import httpx

from . import config

# Fixed fallback kinds, used when Bonsai can't produce narrowing options.
KIND_INSTRUCTIONS = {
    None: "",
    "yes": "This time all 3 options must be different ways of saying yes.",
    "no": "This time all 3 options must be different ways of saying no or not right now.",
    "question": "This time all 3 options must be questions back to the friend.",
    "else": "",
}


def load_profile() -> dict:
    return json.loads(config.PROFILE_PATH.read_text(encoding="utf-8"))


def _profile_text(p: dict) -> str:
    lines = [f"- {x}" for x in p.get("about", [])]
    for who, rel in p.get("people", {}).items():
        lines.append(f"- {who}: {rel}")
    return "\n".join(lines)


def _style_text(p: dict) -> str:
    return "\n".join(f"- {x}" for x in p.get("style", [])) or "- Short and casual."


def _examples_text(p: dict) -> str:
    return "\n".join(
        f'Friend: {ex["friend"]}\n{p["name"]}: ' + json.dumps({"replies": ex["replies"]})
        for ex in p.get("examples", [])
    ) or "(none)"


def load_learned() -> list[dict]:
    try:
        return json.loads(config.LEARNED_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def remember_pick(friend: str, reply: str) -> None:
    """He chose this reply to that line: keep it, so later suggestions sound more like him."""
    if not config.LEARN or not friend or not reply:
        return
    picks = [p for p in load_learned() if p.get("reply", "").lower() != reply.lower()]
    picks.append({"friend": friend, "reply": reply})
    try:
        config.LEARNED_PATH.write_text(json.dumps(picks[-200:], indent=1, ensure_ascii=False), encoding="utf-8")
    except OSError as e:
        print(f"[brain] couldn't save learned replies: {e}")


def _learned_text() -> str:
    picks = load_learned()[-config.LEARNED_SHOWN:] if config.LEARN else []
    if not picks:
        return ""
    lines = "\n".join(f'- to "{p["friend"]}" he chose: {p["reply"]}' for p in picks)
    return ("How he has really been talking lately (replies he picked to other lines). Use this only "
            "for his word choice and tone. Every option you write must answer the friend's newest "
            "line below; never paste one of these:\n" + lines + "\n\n")


def _history_text(history: list[dict], name: str) -> str:
    if not history:
        return "(nothing yet)"
    turns = history[-config.HISTORY_TURNS:]

    def line(t):
        if t["who"] == "text":       # a text message that came in
            return f'({t.get("from", "Someone")} texted {name}: "{t["text"]}")'
        if t["who"] == "me_text":    # a text he sent
            return f'({name} texted {t.get("to", "someone")}: "{t["text"]}")'
        return f"{name if t['who'] == 'me' else 'Friend'}: {t['text']}"
    return "\n".join(line(t) for t in turns)


SPLIT = "\n=====\n"


def _prompt(file: str, **kw) -> tuple[str, str]:
    """A prompt file is two parts split by a line of =====. The part above is the same on
    every call (who he is, the rules) and goes in the system message, where llama-server
    keeps it cached. The part below is what changed (the conversation, the screen)."""
    text = (config.PROMPTS_DIR / file).read_text(encoding="utf-8").format(**kw)
    fixed, _, changing = text.partition(SPLIT)
    return (fixed.strip(), changing.strip()) if changing else ("", fixed.strip())


# llama-server grammar-constrains output to this, so Bonsai can't emit broken JSON.
REPLY_SCHEMA = {
    "type": "object",
    "properties": {
        "replies": {
            "type": "array",
            # One reply per string: no "/" (the model likes to cram options together).
            "items": {"type": "string", "pattern": r"^[^\"\\/]{1,90}$"},
            "minItems": 3,
            "maxItems": 3,
        }
    },
    "required": ["replies"],
}
# Same, but every reply must end in "?" (for "ask a question" picks).
QUESTION_SCHEMA = json.loads(json.dumps(REPLY_SCHEMA))
QUESTION_SCHEMA["properties"]["replies"]["items"]["pattern"] = r"^[^\"\\/]{1,88}\?$"

INTENT_SCHEMA = {
    "type": "object",
    "properties": {
        "question": {"type": "string", "minLength": 1, "maxLength": 60},
        "options": {
            "type": "array",
            "items": {"type": "string", "minLength": 1, "maxLength": 40},
            "minItems": 3,
            "maxItems": 3,
        },
    },
    "required": ["question", "options"],
}


# Each kind of prompt keeps its own llama-server slot when the server has several (-np 4),
# so the long fixed part of that prompt stays cached between calls. With one slot it still works.
SLOTS = {"reply": 0, "task": 1, "actions": 2, "message": 3, "intent": 0, "textreply": 3}


# Choosing a step or classifying a line should be the same every time; wording should vary.
TEMPERATURE = {"actions": 0.2, "task": 0.0}


async def _chat(prompt: tuple[str, str] | str, max_tokens: int = 220, schema: dict | None = None,
                slot: str | None = None, on_text=None) -> str:
    """on_text: async callback given the answer so far, each time more of it arrives
    (the tiles fill in as Bonsai writes instead of all at the end)."""
    fixed, changing = prompt if isinstance(prompt, tuple) else ("", prompt)
    messages = ([{"role": "system", "content": fixed}] if fixed else []) + [{"role": "user", "content": changing}]
    payload = {
        "model": config.BONSAI_MODEL,
        "messages": messages,
        "temperature": TEMPERATURE.get(slot, 0.7),
        "max_tokens": max_tokens,
        # Bonsai 2 reasons by default; turn it off for fast replies.
        "chat_template_kwargs": {"enable_thinking": False},
    }
    payload["cache_prompt"] = True
    if slot and config.BONSAI_SLOTS > 1:
        payload["id_slot"] = SLOTS.get(slot, 0) % config.BONSAI_SLOTS
    if schema:
        payload["response_format"] = {"type": "json_schema",
                                      "json_schema": {"name": "out", "schema": schema}}
    async with httpx.AsyncClient(timeout=30) as client:
        if on_text is None:
            r = await client.post(config.BONSAI_URL, json=payload)
            r.raise_for_status()
            text = r.json()["choices"][0]["message"]["content"] or ""
        else:
            payload["stream"] = True
            text = ""
            async with client.stream("POST", config.BONSAI_URL, json=payload) as r:
                r.raise_for_status()
                async for line in r.aiter_lines():
                    if not line.startswith("data: ") or line[6:].strip() == "[DONE]":
                        continue
                    delta = (json.loads(line[6:])["choices"][0].get("delta") or {}).get("content") or ""
                    if delta:
                        text += delta
                        await on_text(text)
    return re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()


def finished_strings(text: str) -> list[str]:
    """The list items Bonsai has finished writing so far in a half-written {"replies": [..."""
    start = text.find("[")
    if start < 0:
        return []
    out = []
    for m in re.finditer(r'"((?:[^"\\]|\\.)*)"', text[start:]):
        try:
            out.append(json.loads(f'"{m.group(1)}"').strip())
        except ValueError:
            pass
    return [x for x in out if x]


def _stream_to(on_partial):
    """Wrap a tiles callback so it fires once per newly finished reply."""
    if on_partial is None:
        return None
    seen = 0

    async def on_text(text):
        nonlocal seen
        done = finished_strings(text)
        if len(done) > seen:
            seen = len(done)
            await on_partial(done[:3])
    return on_text


def _parse_json(text: str, key: str) -> list:
    m = re.search(r"\{.*\}", text, flags=re.S)
    if m:
        try:
            val = json.loads(m.group(0)).get(key, [])
            if isinstance(val, list):
                return val
        except json.JSONDecodeError:
            pass
    return []


TASK_SCHEMA = {"type": "object", "required": ["task"],
               "properties": {"task": {"enum": ["none", "message", "computer"]}}}


async def computer_task(history: list[dict]) -> bool:
    """Did the friend just ask for something the user can do on the Mac (text someone,
    put something on)? If so the hub offers to do it right after the reply."""
    if not history or history[-1]["who"] != "friend":
        return False
    p = load_profile()
    try:
        out = await _chat(_prompt("task.txt", name=p["name"], history=_history_text(history[-4:], p["name"])),
                          max_tokens=20, schema=TASK_SCHEMA, slot="task")
        return json.loads(out).get("task") in ("message", "computer")
    except Exception as e:
        print(f"[brain] Bonsai error: {e}")
        return False


async def suggest_replies(history: list[dict], kind: str | None = None,
                          path: list[str] | None = None,
                          avoid: list[str] | None = None,
                          offer: bool = False, on_partial=None) -> tuple[list[str], int, bool]:
    """Returns (replies, latency_ms, ok). ok=False means Bonsai failed and we used fallbacks.

    kind: a fixed fallback kind (yes/no/question/else). path: narrowing options the
    user picked, most specific last. avoid: replies they already passed on."""
    p = load_profile()
    instr = [KIND_INSTRUCTIONS.get(kind, "")]
    if offer:   # the friend asked for something he can do on his Mac
        instr.append("The friend asked him to do something he can do from his Mac (text someone, "
                     "put something on). Option 1 just agrees to do it in 2 to 4 casual words, like "
                     '"yeah one sec" or "on it". No reasons, no details about anyone.')
    if path:
        detail = f', specifically "{path[-1]}"' if len(path) > 1 else ""
        instr.append(f'He told you what he wants to say: "{path[0]}"{detail}. '
                     "Write 3 ways to say exactly that, each worded differently. "
                     "None of them may change his answer or say the opposite.")
    if avoid:
        instr.append("He already passed on these, so don't repeat them: " + "; ".join(avoid))
    instr = "\n".join(f"- {x}" for x in instr if x)
    wants_question = kind == "question" or (path and path[-1].lower().startswith("ask"))
    prompt = _prompt("reply.txt", name=p["name"], profile=_profile_text(p),
                     style=_style_text(p), examples=_examples_text(p),
                     history=_history_text(history, p["name"]), kind_instruction=instr,
                     learned=_learned_text())
    t0 = time.perf_counter()
    try:
        out = await _chat(prompt, schema=QUESTION_SCHEMA if wants_question else REPLY_SCHEMA, slot="reply",
                          on_text=_stream_to(on_partial))
        replies = [str(r).strip() for r in _parse_json(out, "replies") if str(r).strip()]
        if not replies:  # malformed JSON: salvage the quoted strings, never raw lines
            quoted = re.findall(r'"((?:[^"\\]|\\.)+)"', out)
            replies = [q.strip() for q in quoted if q.strip() and q != "replies"]
        ok = True
    except Exception as e:  # Bonsai down: keep the demo moving
        print(f"[brain] Bonsai error: {e}")
        replies, ok = ["Yes", "No", "Give me a second"], False
    ms = int((time.perf_counter() - t0) * 1000)
    return (replies + ["Yes", "No", "Hmm"])[:3], ms, ok


async def suggest_text_replies(history: list[dict], who: str, text: str,
                               avoid: list[str] | None = None, on_partial=None) -> tuple[list[str], int, bool]:
    """3 answers to a text message that just came in. Returns (replies, ms, ok)."""
    p = load_profile()
    spoken = [t for t in history if t["who"] in ("friend", "me")][-4:]
    prompt = _prompt("textreply.txt", name=p["name"], profile=_profile_text(p), style=_style_text(p),
                     history=_history_text(spoken, p["name"]), who=who, text=text.replace('"', "'"),
                     avoid=("You already passed on these, so write different ones: "
                            + "; ".join(avoid) + "\n") if avoid else "")
    t0 = time.perf_counter()
    try:
        out = await _chat(prompt, max_tokens=120, schema=REPLY_SCHEMA, slot="textreply",
                          on_text=_stream_to(on_partial))
        low = {a.lower() for a in avoid or []}
        replies = [r for r in dict.fromkeys(str(x).strip() for x in _parse_json(out, "replies"))
                   if r and r.lower() not in low]
        ok = bool(replies)
    except Exception as e:
        print(f"[brain] Bonsai error: {e}")
        replies, ok = [], False
    ms = int((time.perf_counter() - t0) * 1000)
    return (replies + ["yeah", "not right now", "give me a minute"])[:3], ms, ok


async def suggest_new_texts(history: list[dict], who: str,
                            avoid: list[str] | None = None) -> tuple[list[str], int, bool]:
    """3 messages he might want to send to someone, unprompted. Returns (messages, ms, ok)."""
    p = load_profile()
    relation = p.get("people", {}).get(who, "")
    # The friend just asked him to tell or ask this person something: the messages are
    # wordings of that request (the same prompt the step-by-step route uses).
    asked = next((t["text"] for t in reversed(history[-3:]) if t["who"] == "friend"), "")
    if who.lower() in asked.lower() and re.search(r"\b(tell|ask|text|let|remind|message)\b", asked.lower()):
        t0 = time.perf_counter()
        try:
            drafts = await _draft_messages(p, [{"who": "friend", "text": asked}], "Messages",
                                           f"Message {who}", f"text {who}: what the friend asked", avoid)
            if drafts:
                return (drafts + ["you around"] * 3)[:3], int((time.perf_counter() - t0) * 1000), True
        except Exception as e:
            print(f"[brain] Bonsai error: {e}")
    prompt = _prompt("textstart.txt", name=p["name"], profile=_profile_text(p), style=_style_text(p),
                     history=_history_text(history[-6:], p["name"]), who=who,
                     relation=f" ({relation})" if relation else "",
                     avoid=("You already passed on these, so write different ones: "
                            + "; ".join(avoid) + "\n") if avoid else "")
    t0 = time.perf_counter()
    try:
        out = await _chat(prompt, max_tokens=120, schema=REPLY_SCHEMA, slot="textreply")
        low = {a.lower() for a in avoid or []}
        texts = [r for r in dict.fromkeys(str(x).strip() for x in _parse_json(out, "replies"))
                 if r and r.lower() not in low]
        ok = bool(texts)
    except Exception as e:
        print(f"[brain] Bonsai error: {e}")
        texts, ok = [], False
    ms = int((time.perf_counter() - t0) * 1000)
    return (texts + ["hey, you around", "what are you up to", "call me when you can"])[:3], ms, ok


async def suggest_intents(history: list[dict], path: list[str], rejected: list[str],
                          seen: list[str]) -> tuple[str, list[str], int, bool]:
    """Clarifying question + 3 directions for the reply. Returns (question, options, ms, ok).
    ok=False (or no options) means the caller should fall back to fixed kind tiles."""
    p = load_profile()
    path_text = (
        f'He already picked: {" > ".join(path)}. Every option must still mean "{path[-1]}", '
        "just more specific: a reason, a condition, or what he'd rather do. "
        'The question asks him to narrow that down, like "Why not?" or "When works?"\n\n'
    ) if path else ""
    prompt = _prompt("intent.txt", name=p["name"], profile=_profile_text(p),
                     history=_history_text(history, p["name"]), path=path_text,
                     rejected="\n".join(f"- {r}" for r in rejected) or "(none)",
                     seen="; ".join(seen) or "(none)")
    t0 = time.perf_counter()
    try:
        data = json.loads(await _chat(prompt, max_tokens=120, schema=INTENT_SCHEMA, slot="intent"))
        question = str(data.get("question", "")).strip()
        seen_l = {s.lower() for s in seen}
        options = [o.strip() for o in map(str, data.get("options", []))
                   if o.strip() and o.strip().lower() not in seen_l]
        ok = True
    except Exception as e:
        print(f"[brain] Bonsai error: {e}")
        question, options, ok = "", [], False
    ms = int((time.perf_counter() - t0) * 1000)
    return question, options[:3], ms, ok


TYPEABLE = ("textfield", "textarea", "searchfield", "combobox")


SEND_WORDS = ("send", "post", "submit")


def _step_choices(elements: list[dict], apps: list[str]) -> dict[str, dict]:
    """Every step that is possible right now, keyed by the short line Bonsai copies.
    Short lines keep the answer small: Bonsai writes about 30 tokens instead of 100,
    which is most of the wait on a computer step."""
    choices: dict[str, dict] = {}
    for e in elements:
        if e["role"] in TYPEABLE:
            choices[f'type {e["id"]}'] = {"type": "type", "id": e["id"]}
        else:
            choices[f'press {e["id"]}'] = {"type": "press", "id": e["id"]}
    for a in apps:
        choices[f"open {a}"] = {"type": "open_app", "name": a}
    return choices


def _action_schema(choices: list[str], all_apps: list[str], with_goal: bool) -> dict:
    props = {"finished": {"type": "boolean"},
             "steps": {"type": "array", "items": {"enum": choices}, "minItems": 3, "maxItems": 3}}
    required = ["finished", "steps"]
    if with_goal:   # first step of a task: commit to the goal and app before choosing steps
        props = {"goal": {"type": "string", "pattern": r"^[^\"\\]{3,70}$"},
                 "app": {"enum": all_apps}, **props}
        required = ["goal", "app"] + required
    return {"type": "object", "required": required, "properties": props}


async def suggest_actions(history: list[dict], screen: dict, done: list[str] | None = None,
                          goal: dict | None = None) -> tuple[list[dict], int, bool, dict]:
    """3 likely next steps on the computer. done = steps already taken this session.
    goal = {"goal", "app"} from an earlier step of the same task (saves deciding it again).
    Returns (actions, ms, ok, info) where info = {"goal", "app", "finished"}.
    Each action gets a "label" built from the real element, so a tile says what will happen."""
    p = load_profile()
    elements = screen.get("elements", [])
    by_id = {e["id"]: e for e in elements}
    app = screen.get("app", "unknown")
    apps = [a for a in p.get("apps", []) if a != app]
    done = done or []
    info = {"goal": (goal or {}).get("goal", ""), "app": (goal or {}).get("app", ""), "finished": False}

    def line(e):
        if e["role"] in TYPEABLE:
            extra = f' (already contains: "{e["value"]}")' if e.get("value") else " (empty)"
            return f'type {e["id"]} = write in the text box "{e["label"]}"{extra}'
        return f'press {e["id"]} = press the {e["role"]} "{e["label"]}"'

    choices = _step_choices(elements, apps)
    with_goal = not info["goal"]
    shape = ('{"goal": "...", "app": "...", "finished": false, "steps": ["...", "...", "..."]}' if with_goal
             else '{"finished": false, "steps": ["...", "...", "..."]}')
    prompt = _prompt("actions.txt", name=p["name"], profile=_profile_text(p),
                     habits="\n".join(f"- {x}" for x in p.get("computer", [])) or "(none)",
                     history=_history_text(history, p["name"]),
                     goal_line="" if with_goal else f'Goal: {info["goal"]} (needs {info["app"]})\n\n',
                     app=app,
                     elements="\n".join([line(e) for e in elements]
                                        + [f"open {a} = switch to the {a} app" for a in apps]) or "(nothing)",
                     done="\n".join(f"- {d}" for d in done) or "(nothing yet)",
                     json_shape=shape)
    t0 = time.perf_counter()
    acts, seen, ok = [], set(), True
    try:
        if not choices:
            raise ValueError("nothing on screen to act on")
        all_apps = list(dict.fromkeys([app] + apps))
        out = await _chat(prompt, max_tokens=140, slot="actions",
                          schema=_action_schema(list(choices), all_apps, with_goal))
        m = re.search(r"\{.*\}", out, flags=re.S)
        data = json.loads(m.group(0)) if m else {}
        if with_goal:
            info["goal"], info["app"] = str(data.get("goal", "")).strip(), data.get("app") or app
        info["finished"] = bool(data.get("finished")) and bool(done)
        need = info["app"]
        print(f'[brain] goal: {info["goal"]}  app: {need}  finished: {info["finished"]}')
        if need in apps:   # the task lives in another app: opening it is the first step
            seen.add(("open_app", None, need))
            acts.append({"type": "open_app", "name": need, "label": f"Open {need}"})
        nothing_typed = not any(e.get("value") for e in elements if e["role"] in TYPEABLE)
        has_field = any(e["role"] in TYPEABLE for e in elements)
        pressed = {d for d in done if d.startswith("Pressed ")}
        steps = list(data.get("steps", []))
        send = next((k for k, c in choices.items() if c["type"] == "press"
                     and any(w in by_id[c["id"]]["label"].lower() for w in SEND_WORDS)), None)
        if send and has_field and not nothing_typed:
            # The message is written and there is a Send control: sending is always the
            # first tile, whatever order Bonsai put things in.
            steps = [send] + [x for x in steps if x != send]
        # Who the message is for comes from the friend's own words when they name someone on
        # screen. Bonsai leans toward the best friend in the profile; a text to the wrong
        # person is the one mistake that must not happen.
        named = _named_on_screen(history, elements)
        if named and need == "Messages":
            info["goal"] = _goal_for(info["goal"], named["label"])
            code = f'press {named["id"]}'
            if f'Pressed "{named["label"]}"' not in pressed:
                steps = [code] + [x for x in steps if x != code]
        if _looks_finished(info["goal"], done):
            info["finished"] = True
        for step in steps:
            a = dict(choices.get(step) or {})
            t, el = a.get("type"), by_id.get(a.get("id"))
            if t == "press" and el:
                if has_field and nothing_typed and any(w in el["label"].lower() for w in SEND_WORDS):
                    continue   # nothing to send yet
                if f'Pressed "{el["label"]}"' in pressed:
                    continue   # already done in this task: offering it again only costs a gesture
                a["label"] = el["label"]
            elif t == "type" and el:
                if need != "Messages" or "search" in el["label"].lower():
                    continue   # only messages get written: a search box can't be submitted by gesture yet
                a["label"] = "Write a different message" if el.get("value") else "Write a message"
                a["draft"] = True   # the words come from the message prompt, not from this call
            elif t == "open_app" and a.get("name") in apps:
                a["label"] = f'Open {a["name"]}'
            else:
                continue
            key = (a["type"], a.get("id"), a.get("name"))   # one tile per element
            if key not in seen:
                seen.add(key)
                acts.append(a)
        if acts and acts[0].get("draft") and not by_id[acts[0]["id"]].get("value"):
            # An empty box and writing is the next step: show 3 wordings right away.
            acts = await draft_actions(history, screen, acts[0]["id"], info["goal"]) or acts
    except Exception as e:
        print(f"[brain] Bonsai error: {e}")
        ok = False
    ms = int((time.perf_counter() - t0) * 1000)
    return acts[:3], ms, ok, info


NOT_A_PERSON = ("send", "post", "submit", "back", "search", "new message", "compose", "call", "play",
                "reload", "full screen", "done")


def _named_on_screen(history: list[dict], elements: list[dict]) -> dict | None:
    """The one pressable thing on screen whose label the friend said out loud ("tell mom ..."
    with a "Mom" row showing). None if they named nobody on screen, or more than one."""
    said = next((t["text"].lower() for t in reversed(history) if t["who"] == "friend"), "")
    hits = []
    for e in elements:
        label = str(e["label"]).strip()
        low = label.lower()
        if e["role"] in TYPEABLE or len(low) < 3 or any(low.startswith(w) for w in NOT_A_PERSON):
            continue
        first = low.split()[0]
        if re.search(rf"\b{re.escape(low)}\b", said) or (len(first) > 2 and re.search(rf"\b{re.escape(first)}\b", said)):
            hits.append(e)
    exact = [e for e in hits if re.search(rf"\b{re.escape(str(e['label']).lower())}\b", said)]
    hits = exact or hits
    return hits[0] if len(hits) == 1 else None


def _goal_for(goal: str, person: str) -> str:
    """Make the goal name the person the friend named."""
    if person.lower() in goal.lower().split(":")[0]:
        return goal
    m = re.match(r"^(text|tell|ask|message|reply to)\s+[^:]+:(.*)$", goal, flags=re.I)
    if m:
        return f"{m.group(1)} {person}:{m.group(2)}"
    return f"text {person}: {goal}"


def _looks_finished(goal: str, done: list[str]) -> bool:
    """Obvious endings Bonsai sometimes misses: the thing he asked to put on is playing,
    or the page he asked for was opened."""
    g = goal.lower()
    last = done[-1].lower() if done else ""
    if not last.startswith("pressed "):
        return False
    if g.startswith(("put", "play", "watch")) and any(w in last for w in ('"play', "watch now", "resume")):
        return True
    if g.startswith(("look up", "open", "show", "find", "check")):
        words = [w for w in re.findall(r"[a-z0-9]+", g) if len(w) > 3 and w not in ("look", "open", "show", "find", "check")]
        return bool(words) and any(w in last for w in words)
    return False


async def draft_actions(history: list[dict], screen: dict, field_id, goal: str,
                        avoid: list[str] | None = None) -> list[dict]:
    """3 wordings of the message for one text box, as ready-to-run type actions.
    Writing the message is the hard part, so it gets its own focused prompt."""
    p = load_profile()
    field = next((e for e in screen.get("elements", []) if e["id"] == field_id), None)
    if not field:
        return []
    avoid = [x for x in (avoid or []) + [field.get("value", "")] if x]
    # His own "yeah one sec" to the friend is not part of the message: leave it out,
    # or Bonsai offers it as a wording.
    spoken = list(history)
    while spoken and spoken[-1]["who"] == "me":
        spoken.pop()
    drafts = await _draft_messages(p, spoken or history, screen.get("app", ""), field["label"], goal, avoid)
    return [{"type": "type", "id": field["id"], "text": d, "label": d} for d in drafts]


async def _draft_messages(p: dict, history: list[dict], app: str, field: str, goal: str,
                          avoid: list[str] | None = None) -> list[str]:
    prompt = _prompt("message.txt", name=p["name"], profile=_profile_text(p), style=_style_text(p),
                     examples="\n".join(
                         f'Friend said: "{t["friend"]}"\nYou texted {t["to"]}: ' + " / ".join(t["texts"])
                         for t in p.get("texts", [])) or "(none yet)",
                     history=_history_text(history, p["name"]), app=app, field=field,
                     goal=goal or "(see the conversation)",
                     avoid=("He already passed on these wordings, so write different ones: "
                            + "; ".join(avoid) + "\n") if avoid else "")
    out = await _chat(prompt, max_tokens=120, schema=REPLY_SCHEMA, slot="message")
    drafts = [str(r).strip() for r in _parse_json(out, "replies") if str(r).strip()]
    low = {a.lower() for a in avoid or []}
    return [d for d in dict.fromkeys(drafts) if d.lower() not in low][:3]
