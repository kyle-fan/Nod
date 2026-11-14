"""Talks to the Swift sidecar (WhisperKit / TTSKit / accessibility).
With MOCK_SIDECAR=1 it fakes everything so you can build on Windows."""
import asyncio

import httpx

from . import config

FAKE_SCREEN = {
    "app": "Discord",
    "elements": [
        {"id": 1, "role": "button", "label": "Andrew"},
        {"id": 2, "role": "button", "label": "Sam"},
        {"id": 3, "role": "textfield", "label": "Message @Andrew"},
        {"id": 4, "role": "button", "label": "Send"},
        {"id": 5, "role": "button", "label": "Settings"},
    ],
}


async def speak(text: str) -> None:
    if config.MOCK_SIDECAR:
        print(f"[speak] {text}")
        await asyncio.sleep(min(4.0, 0.06 * len(text) + 0.5))
        return
    async with httpx.AsyncClient(timeout=60) as c:
        await c.post(f"{config.SIDECAR_URL}/speak", json={"text": text})


async def screen() -> dict:
    if config.MOCK_SIDECAR:
        return FAKE_SCREEN
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(f"{config.SIDECAR_URL}/screen")
        return r.json()


async def act(action: dict) -> dict:
    if config.MOCK_SIDECAR:
        print(f"[act] {action}")
        return {"ok": True}
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(f"{config.SIDECAR_URL}/act", json=action)
        return r.json()
