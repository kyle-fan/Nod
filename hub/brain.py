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


def _history_text(history: list[dict], name: str) -> str:
    if not history:
        return "(nothing yet)"
    turns = history[-config.HISTORY_TURNS:]
    return "\n".join(
        f"{name if t['who'] == 'me' else 'Friend'}: {t['text']}" for t in turns
    )


def _prompt(file: str, **kw) -> str:
    return (config.PROMPTS_DIR / file).read_text(encoding="utf-8").format(**kw)


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


async def _chat(prompt: str, max_tokens: int = 220, schema: dict | None = None) -> str:
    payload = {
        "model": config.BONSAI_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7,
        "max_tokens": max_tokens,
        # Bonsai 2 reasons by default; turn it off for fast replies.
        "chat_template_kwargs": {"enable_thinking": False},
    }
    if schema:
        payload["response_format"] = {"type": "json_schema",
                                      "json_schema": {"name": "out", "schema": schema}}
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(config.BONSAI_URL, json=payload)
        r.raise_for_status()
        text = r.json()["choices"][0]["message"]["content"] or ""
    return re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()


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
                          max_tokens=20, schema=TASK_SCHEMA)
        return json.loads(out).get("task") in ("message", "computer")
    except Exception as e:
        print(f"[brain] Bonsai error: {e}")
        return False


async def suggest_replies(history: list[dict], kind: str | None = None,
                          path: list[str] | None = None,
                          avoid: list[str] | None = None,
                          offer: bool = False) -> tuple[list[str], int, bool]:
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
                     history=_history_text(history, p["name"]), kind_instruction=instr)
    t0 = time.perf_counter()
    try:
        out = await _chat(prompt, schema=QUESTION_SCHEMA if wants_question else REPLY_SCHEMA)
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
        data = json.loads(await _chat(prompt, max_tokens=120, schema=INTENT_SCHEMA))
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


def _action_schema(elements: list[dict], apps: list[str], all_apps: list[str]) -> dict:
    """Only steps that are possible right now: press a real pressable id, type into a
    real text field id, or open one of the user's apps."""
    press = [e["id"] for e in elements if e["role"] not in TYPEABLE]
    fields = [e["id"] for e in elements if e["role"] in TYPEABLE]
    kinds = []
    if press:
        kinds.append({"type": "object", "required": ["type", "id"],
                      "properties": {"type": {"const": "press"}, "id": {"enum": press}}})
    if fields:
        kinds.append({"type": "object", "required": ["type", "id", "text"],
                      "properties": {"type": {"const": "type"}, "id": {"enum": fields},
                                     "text": {"type": "string", "pattern": r"^[^\"\\]{1,90}$"}}})
    if apps:
        kinds.append({"type": "object", "required": ["type", "name"],
                      "properties": {"type": {"const": "open_app"}, "name": {"enum": apps}}})
    # goal and app come first so the model commits to them before choosing steps
    return {"type": "object", "required": ["goal", "app", "actions"], "properties": {
        "goal": {"type": "string", "pattern": r"^[^\"\\]{3,70}$"},
        "app": {"enum": all_apps},
        "actions": {"type": "array", "items": {"anyOf": kinds}, "minItems": 3, "maxItems": 3}}}


async def suggest_actions(history: list[dict], screen: dict,
                          done: list[str] | None = None) -> tuple[list[dict], int, bool]:
    """3 likely next steps on the computer. done = steps already taken this session.
    Each action gets a "label" built from the real element, so a tile says what will happen."""
    p = load_profile()
    elements = screen.get("elements", [])
    by_id = {e["id"]: e for e in elements}
    app = screen.get("app", "unknown")
    apps = [a for a in p.get("apps", []) if a != app]

    def line(e):
        extra = ""
        if e["role"] in TYPEABLE:
            extra = f'  (already contains: "{e["value"]}")' if e.get("value") else "  (empty)"
        return f'{e["id"]}: {e["role"]} "{e["label"]}"{extra}'

    prompt = _prompt("actions.txt", name=p["name"], profile=_profile_text(p),
                     habits="\n".join(f"- {x}" for x in p.get("computer", [])) or "(none)",
                     style=_style_text(p), history=_history_text(history, p["name"]),
                     app=app, apps=", ".join(apps) or "(none)",
                     elements="\n".join(line(e) for e in elements) or "(nothing)",
                     done="\n".join(f"- {d}" for d in done or []) or "(nothing yet)")
    t0 = time.perf_counter()
    acts, seen, ok = [], set(), True
    try:
        all_apps = list(dict.fromkeys([app] + apps))
        out = await _chat(prompt, max_tokens=240, schema=_action_schema(elements, apps, all_apps))
        m = re.search(r"\{.*\}", out, flags=re.S)
        data = json.loads(m.group(0)) if m else {}
        need = data.get("app")
        print(f'[brain] goal: {data.get("goal")}  app: {need}')
        if need in apps:   # the task lives in another app: opening it is the first step
            seen.add(("open_app", None, need))
            acts.append({"type": "open_app", "name": need, "label": f"Open {need}"})
        nothing_typed = not any(e.get("value") for e in elements if e["role"] in TYPEABLE)
        has_field = any(e["role"] in TYPEABLE for e in elements)
        for a in data.get("actions", []):
            if not isinstance(a, dict):
                continue
            t, el = a.get("type"), by_id.get(a.get("id"))
            if t == "press" and el and el["role"] not in TYPEABLE:
                if has_field and nothing_typed and any(w in el["label"].lower() for w in SEND_WORDS):
                    continue   # nothing to send yet
                a = {"type": "press", "id": el["id"], "label": el["label"]}
            elif t == "type" and el and el["role"] in TYPEABLE and str(a.get("text", "")).strip():
                if "search" in el["label"].lower() and need == "Messages":
                    continue   # a message doesn't go in the search box
                text = str(a["text"]).strip()
                if text == el.get("value"):
                    continue   # already typed
                a = {"type": "type", "id": el["id"], "text": text, "label": text}
            elif t == "open_app" and a.get("name") in apps:
                a = {"type": "open_app", "name": a["name"], "label": f'Open {a["name"]}'}
            else:
                continue
            key = (a["type"], a.get("id"), a.get("name"))   # one tile per element
            if key not in seen:
                seen.add(key)
                acts.append(a)
        if acts and acts[0]["type"] == "type":
            # Writing the message is the hard part, so it gets its own focused prompt.
            # The 3 tiles become 3 wordings for the same field.
            field = by_id[acts[0]["id"]]
            drafts = await _draft_messages(p, history, app, field["label"], str(data.get("goal", "")))
            if drafts:
                acts = [{"type": "type", "id": field["id"], "text": d, "label": d} for d in drafts]
    except Exception as e:
        print(f"[brain] Bonsai error: {e}")
        ok = False
    ms = int((time.perf_counter() - t0) * 1000)
    return acts[:3], ms, ok


async def _draft_messages(p: dict, history: list[dict], app: str, field: str, goal: str) -> list[str]:
    prompt = _prompt("message.txt", name=p["name"], profile=_profile_text(p), style=_style_text(p),
                     examples="\n".join(
                         f'Friend said: "{t["friend"]}"\nYou texted {t["to"]}: ' + " / ".join(t["texts"])
                         for t in p.get("texts", [])) or "(none yet)",
                     history=_history_text(history, p["name"]), app=app, field=field,
                     goal=goal or "(see the conversation)")
    out = await _chat(prompt, max_tokens=120, schema=REPLY_SCHEMA)
    drafts = [str(r).strip() for r in _parse_json(out, "replies") if str(r).strip()]
    return list(dict.fromkeys(drafts))[:3]
