"""Bonsai calls: turn context into 3 options. Prompts live in prompts/*.txt."""
import json
import re
import time

import httpx

from . import config

KIND_INSTRUCTIONS = {
    None: "",
    "yes": "Every reply must be a different way of saying yes.\n",
    "no": "Every reply must be a different way of saying no or not right now.\n",
    "question": "Every reply must be a question back to the other person.\n",
    "else": "The earlier suggestions did not fit. Suggest something different from these: {prev}\n",
}


def load_profile() -> dict:
    return json.loads(config.PROFILE_PATH.read_text(encoding="utf-8"))


def _profile_text(p: dict) -> str:
    lines = [f"- {x}" for x in p.get("about", [])]
    for who, rel in p.get("people", {}).items():
        lines.append(f"- {who}: {rel}")
    return "\n".join(lines)


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
            "items": {"type": "string", "minLength": 1, "maxLength": 90},
            "minItems": 3,
            "maxItems": 3,
        }
    },
    "required": ["replies"],
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
                          prev: list[str] | None = None) -> tuple[list[str], int, bool]:
    """Returns (replies, latency_ms, ok). ok=False means Bonsai failed and we used fallbacks."""
    p = load_profile()
    instr = KIND_INSTRUCTIONS.get(kind, "").format(prev="; ".join(prev or []))
    prompt = _prompt("reply.txt", name=p["name"], profile=_profile_text(p),
                     history=_history_text(history, p["name"]), kind_instruction=instr)
    t0 = time.perf_counter()
    try:
        out = await _chat(prompt, schema=REPLY_SCHEMA)
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
