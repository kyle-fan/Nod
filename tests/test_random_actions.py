"""Hundreds of random gestures, friend lines, incoming texts, clicks and settings changes
against the hub, with the pretend Mac and the stand-in model, and with Bonsai, the screen
reader, the clicker and the voice failing at random for part of the run.

What must hold after every single action: nothing raised, the hub is back in a resting
state with sane tiles, and no text was sent unless "Yes, do it" was the thing chosen.

Run: .venv/bin/python tests/test_random_actions.py [actions] [seed]
"""
import asyncio
import contextlib
import io
import json
import random
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_hub_flow as T   # noqa: E402  (sets up the pretend Mac and the stand-in model)
from hub import brain, main, sidecar   # noqa: E402

S = main.S
FRIEND = ["want to grab food", "can you tell Andrew to come back", "can you put the yankees game on",
          "thai or pizza", "tell Coach we're late", "text Sam that we're running late", "", "   ", "ok",
          "can you tell Andrew", "to come back", "let mom know I'll call her tonight", "x" * 900]


async def storm(actions: int, seed: int):
    rng = random.Random(seed)
    problems, tracebacks, flaky, sent_total = [], [], {"on": False}, 0
    real = (brain._chat, sidecar.screen, sidecar.act, sidecar.speak, traceback.print_exc)
    real_sleep = asyncio.sleep

    async def no_wait(_seconds=0, *a, **k):      # the hub's pauses for the Mac to catch up are not needed here
        await real_sleep(0)

    def sometimes_fails(fn, chance, what):
        async def wrapped(*a, **k):
            if flaky["on"] and rng.random() < chance:
                raise RuntimeError(what)
            return await fn(*a, **k)
        return wrapped

    brain._chat = sometimes_fails(real[0], 0.25, "injected: bonsai down")
    sidecar.screen = sometimes_fails(real[1], 0.15, "injected: no screen")
    sidecar.act = sometimes_fails(real[2], 0.15, "injected: act failed")
    sidecar.speak = sometimes_fails(real[3], 0.15, "injected: speak failed")
    traceback.print_exc = lambda *a, **k: tracebacks.append(traceback.format_exc()[-400:])
    asyncio.sleep = no_wait
    try:
        await T.fresh()
        for i in range(actions):
            flaky["on"] = (i // 100) % 3 == 2          # every third block of 100 has failures injected
            r = rng.random()
            sent_before = len(sidecar.FAKE.sent)
            yes = next((k for k, t in enumerate(S.tiles) if t["label"].startswith("Yes")), None)
            chose_yes = bool(S.pending) and yes is not None and S.index == yes
            try:
                if S.pending and yes is not None and rng.random() < 0.3:
                    what, chose_yes = "pick", True           # sometimes he does mean it
                    await main.on_pick(yes)
                elif r < 0.12:
                    what = "friend"
                    await T.friend(rng.choice(FRIEND))
                elif r < 0.17:
                    what = "incoming"
                    await main.incoming({"from": rng.choice(["Andrew", "Sam", "Mom", "Zed", ""]),
                                         "text": rng.choice(["where are you", "", "call me", "y" * 3000])})
                elif r < 0.20:
                    what, idx = "pick", rng.randrange(-1, 7)
                    chose_yes = bool(S.pending) and yes is not None and idx == yes
                    await main.on_pick(idx)
                elif r < 0.22:
                    what = "settings"
                    await main.set_settings({rng.choice(["express", "learn", "classic_gestures"]): rng.random() < 0.5})
                elif r < 0.23:
                    what = "partial"
                    await main.heard(main.Heard(text="so I was", final=False))
                elif r < 0.235:
                    what = "reset"
                    await main.ws_message({"type": "reset"})
                else:
                    what = rng.choices(["nod", "left", "right", "select", "cancel", "bogus"], [18, 14, 14, 40, 10, 1])[0]
                    await main.on_gesture(what)
                for tick in range(400):                     # let anything it started in the background finish
                    await real_sleep(0)
                    if tick >= 5 and not main.lock.locked():
                        break
            except Exception as e:   # noqa: BLE001
                problems.append((i, what, "raised " + repr(e)))
                continue
            sent_now = len(sidecar.FAKE.sent) - sent_before
            sent_total += max(0, sent_now)
            if main.lock.locked():
                problems.append((i, what, "still busy"))
            if S.mode not in ("listen", "choose", "computer"):
                problems.append((i, what, f"left in mode {S.mode!r}, status {S.status!r}"))
            if S.mode in ("choose", "computer") and not (S.tiles and 0 <= S.index < len(S.tiles)):
                problems.append((i, what, f"{len(S.tiles)} tiles, highlight {S.index}"))
            if S.mode == "listen" and S.tiles:
                problems.append((i, what, "tiles left up while listening"))
            if sent_now > 0 and not (what in ("select", "pick") and chose_yes):
                problems.append((i, what, f"SENT WITHOUT A YES: {sidecar.FAKE.sent[-1]}"))
            if sent_now > 1:
                problems.append((i, what, "sent twice"))
            json.dumps(S.view())
    finally:
        brain._chat, sidecar.screen, sidecar.act, sidecar.speak, traceback.print_exc = real
        asyncio.sleep = real_sleep
        await main.set_settings({"express": False, "learn": True, "classic_gestures": False})
    unexpected = [t for t in tracebacks if "injected:" not in t]
    return problems, unexpected, sent_total


def test_random_actions_never_break_it_or_send_without_a_yes(actions=1500, seeds=(1, 6)):
    sent = 0
    for seed in seeds:
        with contextlib.redirect_stdout(io.StringIO()):
            problems, unexpected, n = asyncio.run(storm(actions, seed))
        assert not problems, problems[:5]
        assert not unexpected, unexpected[:2]
        sent += n
    assert sent > 0, "the run never got as far as sending a text, so it proved nothing about sending"


if __name__ == "__main__":
    if len(sys.argv) > 1:
        n, seed = int(sys.argv[1]), int(sys.argv[2]) if len(sys.argv) > 2 else 1
        with contextlib.redirect_stdout(io.StringIO()):
            problems, unexpected, sent = asyncio.run(storm(n, seed))
        print(f"{n} actions, seed {seed}: {len(problems)} problems, {len(unexpected)} unexpected errors, {sent} texts sent")
        for p in problems[:10] + unexpected[:3]:
            print("  ", p)
        sys.exit(1 if problems or unexpected else 0)
    test_random_actions_never_break_it_or_send_without_a_yes()
    print("ok   random actions")
