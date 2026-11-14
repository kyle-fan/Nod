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


async def suggest_replies(history: list[dict], kind: str | None = None,
                          path: list[str] | None = None,
                          avoid: list[str] | None = None) -> tuple[list[str], int, bool]:
    """Returns (replies, latency_ms, ok). ok=False means Bonsai failed and we used fallbacks.

    kind: a fixed fallback kind (yes/no/question/else). path: narrowing options the
    user picked, most specific last. avoid: replies they already passed on."""
    p = load_profile()
    instr = [KIND_INSTRUCTIONS.get(kind, "")]
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


async def suggest_actions(history: list[dict], screen: dict) -> tuple[list[dict], int, bool]:
    p = load_profile()
    elements = screen.get("elements", [])
    ids = {e["id"] for e in elements}
    el_text = "\n".join(f'{e["id"]}: {e["role"]} "{e["label"]}"' for e in elements) or "(none)"
    prompt = _prompt("actions.txt", name=p["name"], profile=_profile_text(p),
                     history=_history_text(history, p["name"]),
                     app=screen.get("app", "unknown"), elements=el_text)
    t0 = time.perf_counter()
    try:
        out = await _chat(prompt, max_tokens=300)
        acts = []
        for a in _parse_json(out, "actions"):
            if not isinstance(a, dict):
                continue
            t = a.get("type")
            if t in ("press", "type") and a.get("id") in ids:
                acts.append(a)
            elif t == "open_app" and a.get("name"):
                acts.append(a)
        ok = True
    except Exception as e:
        print(f"[brain] Bonsai error: {e}")
        acts, ok = [], False
    ms = int((time.perf_counter() - t0) * 1000)
    for a in acts:
        a.setdefault("label", a.get("text") or a.get("name") or "Do this")
    return acts[:3], ms, ok
