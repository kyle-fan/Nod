"""Gesture logic against made-up head traces. Numbers come from a real --measure run on
the demo MacBook: nods dip about 20 degrees, a left tilt rolls 15 and also dips 20,
looking around the screen dips up to 6, a smile reads 0.83.

Run: .venv/bin/python -m pytest tests -q   (or: .venv/bin/python tests/test_gestures.py)
"""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hub.gestures import GestureLogic, Tuning   # noqa: E402

FPS = 30


class Trace:
    """Builds a pose timeline frame by frame and runs it through the logic."""

    def __init__(self, tuning=None, base=(0.0, 0.0)):
        self.g = GestureLogic(tuning)
        self.t = 0.0
        self.pose = [base[0], base[1], 0.0, 0.0]   # pitch, roll, smile, jaw
        self.events = []
        self.hold(2.0)                              # calibration + settle

    def _step(self):
        self.t += 1 / FPS
        for e in self.g.feed(self.t, *self.pose):
            self.events.append((round(self.t, 2), e))

    def hold(self, seconds):
        for _ in range(int(seconds * FPS)):
            self._step()
        return self

    def move(self, seconds, pitch=None, roll=None, smile=None, jaw=None):
        """Ease from the current pose to the target over `seconds`."""
        start = list(self.pose)
        target = [start[0] if pitch is None else pitch, start[1] if roll is None else roll,
                  start[2] if smile is None else smile, start[3] if jaw is None else jaw]
        n = max(1, int(seconds * FPS))
        for i in range(1, n + 1):
            k = (1 - math.cos(math.pi * i / n)) / 2
            self.pose = [a + (b - a) * k for a, b in zip(start, target)]
            self._step()
        return self

    def nod(self, depth=20.0, down=0.25, up=0.3, rest=None):
        rest = self.pose[0] if rest is None else rest
        return self.move(down, pitch=rest - depth).move(up, pitch=rest + depth * 0.3).move(0.2, pitch=rest)

    def names(self):
        return [e for _, e in self.events]


def test_plain_nod():
    assert Trace().nod().hold(1).names() == ["nod"]


def test_slow_nod():
    assert Trace().nod(down=0.45, up=0.5).hold(1).names() == ["nod"]


def test_shallow_nod_counts_but_reading_the_screen_does_not():
    assert Trace().nod(depth=12).hold(1).names() == ["nod"]
    # eyes and head wander while reading tiles: slow dips of up to 6 degrees
    t = Trace()
    for _ in range(4):
        t.move(0.8, pitch=-6).move(0.8, pitch=1)
    assert t.hold(1).names() == []


def test_nod_still_works_after_slumping():
    """The old logic needed the head back within 3 degrees of the start-up pose, so a
    slump of 6 degrees killed every nod. Neutral now follows slow drift."""
    t = Trace().move(20, pitch=-6).hold(3)        # slowly slump over 20 s
    assert t.names() == [], t.events
    t.nod().hold(1).nod().hold(1)
    assert t.names() == ["nod", "nod"], t.events


def test_nod_works_when_calibrated_while_looking_down():
    assert Trace(base=(-8.0, 2.0)).nod().hold(1).names() == ["nod"]


def test_three_nods_in_a_row():
    t = Trace()
    for _ in range(3):
        t.nod().hold(0.6)
    assert t.names() == ["nod", "nod", "nod"]


def test_left_tilt_that_dips_the_head_is_not_a_nod():
    """Measured: tilting left rolled 15 degrees and dipped the head 20, as deep as a nod."""
    t = Trace().move(0.4, roll=-15, pitch=-20).hold(0.2).move(0.4, roll=0, pitch=0).hold(1.2)
    assert t.names() == ["left"], t.events


def test_right_tilt_and_back():
    t = Trace().move(0.35, roll=9.3).hold(0.2).move(0.35, roll=0).hold(1)
    assert t.names() == ["right"]


def test_held_tilt_repeats():
    t = Trace().move(0.3, roll=12).hold(3.0).move(0.3, roll=0).hold(0.5)
    assert t.names() == ["right", "right", "right"], t.events


def test_tilt_left_then_right():
    t = Trace().move(0.3, roll=-12).move(0.3, roll=0).hold(0.4).move(0.3, roll=12).move(0.3, roll=0).hold(0.5)
    assert t.names() == ["left", "right"]


def test_smile_selects_once():
    t = Trace().move(0.2, smile=0.83).hold(0.8).move(0.2, smile=0.0).hold(0.5)
    assert t.names() == ["select"]


def test_quick_smirk_is_ignored():
    t = Trace().move(0.08, smile=0.6).move(0.08, smile=0.0).hold(0.5)
    assert t.names() == []


def test_open_mouth_cancels():
    t = Trace().move(0.2, jaw=0.89).hold(0.6).move(0.2, jaw=0.0).hold(0.5)
    assert t.names() == ["cancel"]


def test_nod_then_tilt_then_smile_sequence():
    t = (Trace().nod().hold(0.8)
         .move(0.3, roll=12).move(0.3, roll=0).hold(0.6)
         .move(0.2, smile=0.8).hold(0.5).move(0.2, smile=0).hold(0.5))
    assert t.names() == ["nod", "right", "select"], t.events


def test_talking_jaw_does_not_cancel():
    t = Trace()
    for _ in range(6):                      # chewing / mumbling: small quick jaw moves
        t.move(0.12, jaw=0.3).move(0.12, jaw=0.0)
    assert t.names() == []


def test_looking_up_is_not_a_nod():
    t = Trace().move(0.3, pitch=12).move(0.3, pitch=0).hold(1)
    assert t.names() == []


def test_opening_the_mouth_dips_the_head_but_is_not_a_nod():
    """Measured: opening the mouth wide moved pitch between -6.9 and +2.2 degrees."""
    t = Trace()
    for _ in range(3):
        t.move(0.25, jaw=0.89, pitch=-7).hold(0.5).move(0.25, jaw=0.0, pitch=2).move(0.2, pitch=0).hold(0.8)
    assert t.names() == ["cancel", "cancel", "cancel"], t.events


def test_raising_eyebrows_is_nothing_when_select_is_smile():
    t = Trace()
    for _ in range(3):
        t.move(0.2, pitch=-3.4).move(0.2, pitch=1.9).move(0.2, pitch=0).hold(0.5)
    assert t.names() == []


def test_look_up_then_small_look_down_is_not_a_nod():
    t = Trace().move(0.3, pitch=6).move(0.3, pitch=-5).move(0.3, pitch=0).hold(1)
    assert t.names() == []


def test_jittery_camera_does_not_fire_anything():
    import random
    rnd = random.Random(7)
    t = Trace()
    for _ in range(FPS * 20):                      # 20 s of sitting still with tracking noise
        t.pose = [rnd.uniform(-1.5, 1.0), rnd.uniform(-0.7, 1.7), rnd.uniform(0, 0.02), rnd.uniform(0, 0.03)]
        t._step()
    assert t.names() == [], t.events


def test_classic_rules_are_still_available_and_show_the_old_weaknesses():
    from hub.gestures import ClassicLogic
    class Old(Trace):
        def __init__(self):
            self.g = ClassicLogic()
            self.t = 0.0
            self.pose = [0.0, 0.0, 0.0, 0.0]
            self.events = []
            self.hold(2.0)
    assert Old().nod().hold(1).names() == ["nod"]
    slumped = Old().move(20, pitch=-10).hold(3).nod(depth=15).hold(1)
    assert slumped.names() == [], "the old rules lose nods after a slump"
    assert Trace().move(20, pitch=-10).hold(3).nod(depth=15).hold(1).names() == ["nod"], "the new ones keep them"
    tilt = Old().move(0.4, roll=-15, pitch=-20).hold(0.2).move(0.4, roll=0, pitch=0).hold(1.2)
    assert "nod" in tilt.names(), "the old rules read a dipping tilt as a nod"


def test_slow_and_fast_cameras():
    """The same gestures must read the same at 10 frames a second as at 60."""
    global FPS
    old = FPS
    try:
        for FPS in (10, 15, 60):
            assert Trace().nod(depth=12).hold(1).names() == ["nod"], FPS
            assert Trace().move(0.4, roll=-15, pitch=-20).hold(0.2).move(0.4, roll=0, pitch=0).hold(1.2).names() == ["left"], FPS
            assert Trace().move(0.2, smile=0.83).hold(0.8).move(0.2, smile=0.0).hold(0.5).names() == ["select"], FPS
    finally:
        FPS = old


def test_gestures_survive_camera_jitter():
    """Every scenario above again, with the kind of frame-to-frame noise a face tracker has."""
    import random
    scenarios = [fn for name, fn in sorted(globals().items())
                 if name.startswith("test_") and callable(fn)
                 and name not in ("test_gestures_survive_camera_jitter", "test_slow_and_fast_cameras",
                                  "test_classic_rules_are_still_available_and_show_the_old_weaknesses",
                                  "test_jittery_camera_does_not_fire_anything")]
    clean = Trace._step
    try:
        for seed in range(6):
            rnd = random.Random(seed)

            def noisy(self):
                self.t += 1 / FPS
                p, r, s, j = self.pose
                vals = (p + rnd.gauss(0, 0.6), r + rnd.gauss(0, 0.5), max(0, s + rnd.gauss(0, 0.01)), max(0, j + rnd.gauss(0, 0.01)))
                for e in self.g.feed(self.t, *vals):
                    self.events.append((round(self.t, 2), e))
            Trace._step = noisy
            for fn in scenarios:
                fn()
    finally:
        Trace._step = clean


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
