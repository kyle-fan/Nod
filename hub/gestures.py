"""Turns head pose and face readings into gesture events. No camera code here, so the
logic can be tested with made-up traces (tests/test_gestures.py).

Compared with reading everything against the pose learned at start-up, this:
- follows slow drift (slumping, leaning), so gestures keep working minutes later;
- sees a nod as a dip and a return relative to where the head just was, not to zero;
- ignores the dip that comes with a head tilt, which used to register as a nod.
"""
from collections import deque
from dataclasses import dataclass


@dataclass
class Tuning:
    tilt_deg: float = 7.0       # roll past this = left/right
    tilt_reset: float = 3.0     # must come back within this before the next tilt
    nod_deg: float = 9.0        # pitch dip needed for a nod
    nod_window: float = 1.2     # seconds: down and back up within this
    select_t: float = 0.42      # smile (or brow) above baseline
    select_hold: float = 0.25   # seconds held
    brow_quiet: float = 0.4     # "brow" select only: head level this long first
    jaw_t: float = 0.45         # mouth open above baseline
    jaw_hold: float = 0.35
    cooldown: float = 0.35      # seconds between any two events
    tilt_repeat: float = 1.3    # held tilt moves again every this many seconds (0 = off)
    select_signal: str = "smile"
    calibrate_s: float = 1.5
    drift_s: float = 6.0        # how slowly the neutral pose follows the head when it is still
    nod_after_tilt: float = 0.9  # no nod this soon after a tilt (tilting dips the head)


class GestureLogic:
    """Feed one reading per camera frame; get back the gestures that just happened."""

    def __init__(self, tuning: Tuning | None = None):
        self.t = tuning or Tuning()
        self.recalibrate()

    def recalibrate(self):
        self.base = None            # (pitch, roll, sel, jaw) of the neutral face
        self._calib = []
        self._t_start = None
        self._last_event = 0.0
        self._tilt_at = 0.0
        self._tilt_armed = True
        self._last_tilt_active = 0.0
        self._last_jaw_active = 0.0
        self._frame_at = None
        self.last = (0.0, 0.0, 0.0, 0.0)   # latest (pitch, roll, select, jaw) relative to neutral
        self._sel_since = self._jaw_since = None
        self._sel_armed = self._jaw_armed = True
        self._hist = deque()        # (t, pitch, |roll|) for the nod window
        self._recent = deque(maxlen=3)
        self._head_moved = 0.0
        self._quiet_since = None
        self._last_t = None

    @property
    def calibrating(self) -> bool:
        return self.base is None

    def _fire(self, name, now, out):
        """False while cooling down; callers stay armed so the gesture fires a moment later."""
        if now - self._last_event < self.t.cooldown:
            return False
        self._last_event = now
        out.append(name)
        return True

    def feed(self, now: float, pitch: float, roll: float, sel: float, jaw: float) -> list[str]:
        t, out = self.t, []
        # median of 3 frames drops single-frame tracking glitches. On a slow camera (under about
        # 16 frames a second) three frames span too much of a nod, so readings are used as they come.
        slow = self._frame_at is not None and now - self._frame_at > 0.06
        self._frame_at = now
        self._recent.append((pitch, roll, sel, jaw))
        if len(self._recent) == 3 and not slow:
            pitch, roll, sel, jaw = (sorted(c)[1] for c in zip(*self._recent))

        # 1) calibrate: sit still, neutral face
        if self.base is None:
            self._t_start = self._t_start or now
            self._calib.append((pitch, roll, sel, jaw))
            if now - self._t_start > t.calibrate_s:
                self.base = [sorted(c)[len(c) // 2] for c in zip(*self._calib)]   # median
                self._last_t = now
            return out
        dt = min(0.2, max(0.0, now - (self._last_t or now)))
        self._last_t = now
        p, r, s, j = (pitch - self.base[0], roll - self.base[1], sel - self.base[2], jaw - self.base[3])

        # 2) slow drift: while the head and face are at rest, let "neutral" follow them.
        #    A slump of a few degrees no longer breaks nods ten minutes into a conversation.
        resting = (abs(p) < t.nod_deg * 0.6 and abs(r) < t.tilt_deg * 0.6
                   and s < t.select_t * 0.5 and j < t.jaw_t * 0.5 and now - self._last_event > 1.0)
        if resting:
            self._quiet_since = self._quiet_since or now
            if now - self._quiet_since > 1.0:
                k = dt / t.drift_s
                self.base[0] += (pitch - self.base[0]) * k
                self.base[1] += (roll - self.base[1]) * k
        else:
            self._quiet_since = None

        # 3) tilt left / right: fires once, repeats while held, re-arms at center
        if abs(r) > t.tilt_deg:
            self._last_tilt_active = now
            if self._tilt_armed or (t.tilt_repeat and now - self._tilt_at > t.tilt_repeat):
                if self._fire("right" if r > 0 else "left", now, out):
                    self._tilt_armed, self._tilt_at = False, now
        elif abs(r) < t.tilt_reset:
            self._tilt_armed = True
        if abs(r) > t.tilt_reset * 1.5:
            self._last_tilt_active = now

        # 4) nod: the head dips and comes back up, measured against where it was just before
        #    the dip (not against zero), with the head level the whole time.
        self._hist.append((now, p, abs(r)))
        while self._hist and now - self._hist[0][0] > t.nod_window:
            self._hist.popleft()
        if j > t.jaw_t * 0.5:
            self._last_jaw_active = now        # opening the mouth tips the head down a little
        if (now - self._last_tilt_active > t.nod_after_tilt and now - self._last_jaw_active > 0.6
                and len(self._hist) >= 4):
            low_i = min(range(len(self._hist)), key=lambda i: self._hist[i][1])
            low = self._hist[low_i][1]
            before = max((h[1] for h in list(self._hist)[:low_i + 1]), default=low)
            level = max(h[2] for h in self._hist) < t.tilt_deg * 0.8
            # Deep enough both ways of measuring: from where the head just was, and from
            # neutral. The second stops a look up followed by a small look down counting.
            dipped = before - low > t.nod_deg and low < -t.nod_deg * 0.8
            back_up = p - low > t.nod_deg * 0.6
            if level and dipped and back_up and low_i < len(self._hist) - 1:
                if self._fire("nod", now, out):
                    self._hist.clear()

        # 5) select: smile (or eyebrows) held briefly. Eyebrows only count with a level head,
        #    because nods and tilts push the eyebrow reading up on their own.
        if abs(p) > t.nod_deg * 0.5 or abs(r) > t.tilt_reset:
            self._head_moved = now
        steady = t.select_signal != "brow" or now - self._head_moved > t.brow_quiet
        if s > t.select_t and steady and self._sel_armed:
            self._sel_since = self._sel_since or now
            if now - self._sel_since > t.select_hold and self._fire("select", now, out):
                self._sel_armed = False
        else:
            if not steady:
                self._sel_since = None
            if s < t.select_t * 0.5:
                self._sel_since, self._sel_armed = None, True

        # 6) open mouth = cancel (hold)
        if j > t.jaw_t and self._jaw_armed:
            self._jaw_since = self._jaw_since or now
            if now - self._jaw_since > t.jaw_hold and self._fire("cancel", now, out):
                self._jaw_armed = False
        elif j < t.jaw_t * 0.5:
            self._jaw_since, self._jaw_armed = None, True

        self.last = (p, r, s, j)
        return out


class ClassicLogic(GestureLogic):
    """The rules as they were before 15 Nov: everything measured against the pose learned
    at start-up, no drift correction. Kept as a fallback: NOD_GESTURES=classic."""

    def feed(self, now, pitch, roll, sel, jaw):
        t, out = self.t, []
        self._recent.append((pitch, roll, sel, jaw))
        if len(self._recent) == 3:
            pitch, roll, sel, jaw = (sorted(c)[1] for c in zip(*self._recent))
        if self.base is None:
            self._t_start = self._t_start or now
            self._calib.append((pitch, roll, sel, jaw))
            if now - self._t_start > t.calibrate_s:
                self.base = [sorted(c)[len(c) // 2] for c in zip(*self._calib)]
            return out
        p, r, s, j = (pitch - self.base[0], roll - self.base[1], sel - self.base[2], jaw - self.base[3])
        if abs(r) > t.tilt_deg:
            if self._tilt_armed or (t.tilt_repeat and now - self._tilt_at > t.tilt_repeat):
                if self._fire("right" if r > 0 else "left", now, out):
                    self._tilt_armed, self._tilt_at = False, now
        elif abs(r) < t.tilt_reset:
            self._tilt_armed = True
        self._hist.append((now, p, abs(r)))
        while self._hist and now - self._hist[0][0] > t.nod_window:
            self._hist.popleft()
        if abs(r) < 8 and p > -3 and any(v < -t.nod_deg for _, v, _ in self._hist):
            if self._fire("nod", now, out):
                self._hist.clear()
        if abs(p) > t.nod_deg * 0.5 or abs(r) > t.tilt_reset:
            self._head_moved = now
        steady = t.select_signal != "brow" or now - self._head_moved > t.brow_quiet
        if s > t.select_t and steady and self._sel_armed:
            self._sel_since = self._sel_since or now
            if now - self._sel_since > t.select_hold and self._fire("select", now, out):
                self._sel_armed = False
        else:
            if not steady:
                self._sel_since = None
            if s < t.select_t * 0.5:
                self._sel_since, self._sel_armed = None, True
        if j > t.jaw_t and self._jaw_armed:
            self._jaw_since = self._jaw_since or now
            if now - self._jaw_since > t.jaw_hold and self._fire("cancel", now, out):
                self._jaw_armed = False
        elif j < t.jaw_t * 0.5:
            self._jaw_since, self._jaw_armed = None, True
        self.last = (p, r, s, j)
        return out


def make_logic(tuning: Tuning | None = None, classic: bool | None = None) -> GestureLogic:
    if classic is None:
        import os
        classic = os.getenv("NOD_GESTURES", "").lower() == "classic"
    return (ClassicLogic if classic else GestureLogic)(tuning)
