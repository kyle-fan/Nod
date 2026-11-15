"""Plays the whole demo by itself on a running hub, at a pace a viewer can follow: the
friend's lines, the nods, tilts and smiles, a text arriving, the menu. Use it with the
pretend Mac to rehearse the story or to capture clean footage of the screen:

    ./rehearse.sh                         # terminal 1 (pretend Mac, no speech)
    .venv/bin/python tools/autoplay.py    # terminal 2, then watch http://127.0.0.1:8000

It only sends what a person would: friend lines and gestures. With the pretend Mac nothing
real is sent anywhere. Don't point it at the real hub unless you mean to text for real.
"""
import asyncio
import json
import sys

import websockets

URL = sys.argv[1] if len(sys.argv) > 1 else "ws://127.0.0.1:8000/ws"
PACE = float(sys.argv[2]) if len(sys.argv) > 2 else 1.0     # 2.0 = twice as slow

state = {}


async def main():
    async with websockets.connect(URL) as ws:
        async def pump():
            async for raw in ws:
                state.update(json.loads(raw))
        reader = asyncio.create_task(pump())

        async def pause(s):
            await asyncio.sleep(s * PACE)

        async def send(**m):
            await ws.send(json.dumps(m))

        async def gesture(name, after=0.9):
            await send(type="gesture", name=name)
            await pause(after)

        async def friend(text):
            print(f"friend: {text}")
            await send(type="heard", text=text)
            await pause(2.6)                      # the friend finishes; replies get written

        async def tiles(mode=None, timeout=40):
            """Wait until tiles are on screen (and the hub is done thinking or speaking)."""
            for _ in range(int(timeout * 10)):
                if state.get("tiles") and state.get("mode") not in ("thinking", "speaking") \
                        and (mode is None or state.get("mode") == mode):
                    await pause(1.6)              # time to read them
                    return [t["label"] for t in state["tiles"]]
                await asyncio.sleep(0.1)
            raise TimeoutError(f"no tiles (mode {state.get('mode')}, status {state.get('status')})")

        async def listening(timeout=40):
            for _ in range(int(timeout * 10)):
                if state.get("mode") == "listen":
                    await pause(1.2)
                    return
                await asyncio.sleep(0.1)
            raise TimeoutError("never got back to listening")

        async def pick(index=0):
            """Tilt to a tile and smile."""
            move = index - state.get("index", 0)
            for _ in range(abs(move)):
                await gesture("right" if move > 0 else "left", 0.7)
            print(f"   picks: {state['tiles'][index]['label']}")
            await gesture("select", 0.4)

        await send(type="reset")
        await pause(2.5)

        print("\n1. Talking")
        await friend("hey, want to watch the Yankees game tonight?")
        await gesture("nod")
        await tiles("choose")
        await pick(0)
        await listening()
        await friend("nice. should I order thai or pizza?")
        await gesture("nod")
        await tiles("choose")
        await pick(1)
        await listening()

        print("\n2. The friend asks him to text someone")
        await friend("the game's about to start, can you tell Andrew to come back?")
        await gesture("nod")
        await tiles("choose")
        await pick(0)                             # "yeah one sec", then on the Mac
        for _ in range(6):
            labels = await tiles("computer")
            kinds = [t["kind"] for t in state["tiles"]]
            if "confirm" in kinds:
                await pause(1.5)
                await pick(0)                     # Yes, do it
                break
            await pick(0)
        await listening()

        print("\n3. A text arrives")
        await send(type="incoming", **{"from": "Andrew", "text": "on my way. want anything from the store?"})
        await pause(3.5)
        await gesture("nod")
        await tiles("choose")
        await pick(0)
        await tiles("computer")
        await pause(1.5)
        await pick(0)                             # Yes
        await listening()

        print("\n4. The menu: something that shouldn't wait for a model")
        await gesture("cancel", 1.6)
        await pick(1)                             # Quick phrases
        await tiles("choose")
        await pick(2)                             # Thank you
        await listening()
        print("\nDone.")
        reader.cancel()

asyncio.run(main())
