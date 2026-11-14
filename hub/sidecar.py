"""Talks to the Swift sidecar (WhisperKit / TTSKit / accessibility).
With MOCK_SIDECAR=1 it fakes everything so you can build on Windows."""
import asyncio

import httpx

from . import config

TYPEABLE = ("textfield", "textarea", "searchfield", "combobox")


class FakeMac:
    """A tiny pretend Mac (Messages + Safari) whose screen changes when you act on it,
    so a whole computer-mode chain can be rehearsed without the real sidecar."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.app = config.MOCK_APP
        self.chat = None      # who the open Messages chat is with
        self.draft = ""
        self.sent = []        # (person, text)
        self.page = "home"    # Safari page

    def screen(self) -> dict:
        if self.app == "Messages" and self.chat:
            els = [("button", "Back to conversations"),
                   ("textfield", f"Message {self.chat}", self.draft),
                   ("button", "Send"),
                   ("button", f"Call {self.chat}")]
        elif self.app == "Messages":
            els = [("button", "Andrew"), ("button", "Sam"), ("button", "Mom"),
                   ("textfield", "Search", ""), ("button", "New Message")]
        elif self.page == "game":
            els = [("button", "Play"), ("button", "Full Screen"), ("button", "Back")]
        else:
            els = [("textfield", "Search or enter website name", ""),
                   ("link", "Yankees vs Red Sox - Watch Live"), ("link", "MLB Standings"),
                   ("button", "Reload")]
        out = []
        for i, e in enumerate(els, 1):
            d = {"id": i, "role": e[0], "label": e[1]}
            if e[0] in TYPEABLE:
                d["value"] = e[2]
            out.append(d)
        return {"app": self.app, "elements": out}

    def act(self, action: dict) -> dict:
        t = action.get("type")
        if t == "open_app":
            if action.get("name") not in ("Messages", "Safari"):
                return {"ok": False, "error": f"no app named {action.get('name')}"}
            self.app, self.chat, self.draft, self.page = action["name"], None, "", "home"
            return {"ok": True}
        el = next((e for e in self.screen()["elements"] if e["id"] == action.get("id")), None)
        if not el:
            return {"ok": False, "error": "no element with that id"}
        label = el["label"]
        if t == "type":
            if el["role"] not in TYPEABLE:
                return {"ok": False, "error": "not a text field"}
            if label.startswith("Message "):
                self.draft = action.get("text", "")
            return {"ok": True}
        if t != "press":
            return {"ok": False, "error": f"unknown action type {t}"}
        if self.app == "Messages" and not self.chat and label in ("Andrew", "Sam", "Mom"):
            self.chat = label
        elif label == "Back to conversations":
            self.chat, self.draft = None, ""
        elif label == "Send":
            if not self.draft:
                return {"ok": False, "error": "nothing to send"}
            self.sent.append((self.chat, self.draft))
            print(f"[fake mac] SENT to {self.chat}: {self.draft}")
            self.draft = ""
        elif label.startswith("Yankees vs"):
            self.page = "game"
        elif label == "Back":
            self.page = "home"
        return {"ok": True}


FAKE = FakeMac()


async def speak(text: str) -> None:
    if config.MOCK_SIDECAR:
        print(f"[speak] {text}")
        await asyncio.sleep(min(4.0, 0.06 * len(text) + 0.5))
        return
    async with httpx.AsyncClient(timeout=60) as c:
        await c.post(f"{config.SIDECAR_URL}/speak", json={"text": text})


async def screen() -> dict:
    if config.MOCK_SIDECAR:
        return FAKE.screen()
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(f"{config.SIDECAR_URL}/screen")
        return r.json()


async def act(action: dict) -> dict:
    if config.MOCK_SIDECAR:
        print(f"[act] {action}")
        return FAKE.act(action)
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(f"{config.SIDECAR_URL}/act", json=action)
        return r.json()
