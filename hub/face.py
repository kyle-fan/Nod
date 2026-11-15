"""Webcam head/face gestures with MediaPipe Face Landmarker.

Events: nod, left, right, select (smile, or eyebrows via SELECT_SIGNAL), cancel (open mouth).

Measure your own ranges: python -m hub.face --measure   (70 s guided, prints suggested thresholds)
Watch numbers live:      python -m hub.face --debug
If tilting right prints "left", set FLIP_ROLL = True. Same for nod with FLIP_PITCH.
"""
import math
import os
import sys
import threading
import time
import urllib.request
from collections import deque
from pathlib import Path

MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/face_landmarker/"
             "face_landmarker/float16/1/face_landmarker.task")
MODEL_PATH = Path(__file__).resolve().parent / "models" / "face_landmarker.task"

# ---- thresholds (tune these with --debug) ----
TILT_DEG = 7.0       # roll past this = left/right
TILT_RESET = 3.0     # must come back within this before the next tilt
NOD_DEG = 9.0        # pitch dip needed for a nod
NOD_WINDOW = 1.2     # seconds: down and back up within this
# Select gesture. "smile" separates cleanly (0.78 vs 0.03 for anything else) and works
# with glasses. "brow" (eyebrow raise) reads weak or not at all behind glasses.
SELECT_SIGNAL = "smile"   # "smile" or "brow"
SELECT_T = 0.42      # above baseline. For "brow" use about 0.25
SELECT_HOLD = 0.25   # seconds held
BROW_QUIET = 0.4     # "brow" only: head must be level this long first (nodding lifts eyebrows)
JAW_T = 0.45         # mouth open above baseline
JAW_HOLD = 0.35
COOLDOWN = 0.35      # seconds between any two events
TILT_REPEAT = 1.3    # keep the head tilted: move again every this many seconds (0 = off)
FLIP_ROLL = False
FLIP_PITCH = False   # MacBook Pro camera (demo Mac), from --measure. Kyle's webcam needs True
CALIBRATE_S = 1.5


def ensure_model() -> Path:
    if not MODEL_PATH.exists():
        MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
        print("[face] downloading face_landmarker.task (once, needs internet)...")
        urllib.request.urlretrieve(MODEL_URL, MODEL_PATH)
    return MODEL_PATH


def _euler(m) -> tuple[float, float]:
    """pitch (nod), roll (tilt) in degrees from the 4x4 face transform."""
    r = [[m[i][j] for j in range(3)] for i in range(3)]
    pitch = math.degrees(math.atan2(r[2][1], r[2][2]))
    roll = math.degrees(math.atan2(r[1][0], r[0][0]))
    return pitch, roll


class GestureDetector:
    def __init__(self, on_event, camera_index: int = 0, debug: bool = False):
        self.on_event = on_event
        self.camera_index = camera_index
        self.debug = debug
        self._stop = threading.Event()
        self._recal = threading.Event()
        self.face_ok = False        # a face is in view right now
        self.calibrating = True     # still learning the neutral pose
        self.error = ""             # set when the camera can't be opened or stops
        self.reading = None         # latest (pitch, roll, select, jaw) relative to neutral, for /debug
        self.fps = 0.0

    def start(self):
        threading.Thread(target=self.run, daemon=True).start()

    def stop(self):
        self._stop.set()

    def recalibrate(self):
        """Re-learn the neutral pose (user shifted in their seat, lighting changed)."""
        self._recal.set()

    def tuning(self):
        from .gestures import Tuning
        return Tuning(tilt_deg=TILT_DEG, tilt_reset=TILT_RESET, nod_deg=NOD_DEG, nod_window=NOD_WINDOW,
                      select_t=SELECT_T, select_hold=SELECT_HOLD, brow_quiet=BROW_QUIET, jaw_t=JAW_T,
                      jaw_hold=JAW_HOLD, cooldown=COOLDOWN, tilt_repeat=TILT_REPEAT,
                      select_signal=SELECT_SIGNAL, calibrate_s=CALIBRATE_S)

    def status(self) -> dict:
        """For the screen: is the camera delivering a face, and is it still learning neutral?"""
        return {"on": True, "face": self.face_ok, "calibrating": self.calibrating, "error": self.error}

    def run(self):
        """The gesture rules live in hub/gestures.py (testable without a camera)."""
        from .gestures import make_logic
        logic = make_logic(self.tuning())   # NOD_GESTURES=classic switches to the older rules
        was_calibrating = True
        last_face = time.time()
        last_frame_t = 0.0
        for reading in signals(self.camera_index, self._stop, misses=True):
            if reading is None:                      # a frame with no face in it
                if time.time() - last_face > 1.0:
                    self.face_ok = False
                continue
            last_face, self.face_ok = time.time(), True
            now, pitch, roll, brow, jaw, smile, _blink = reading
            if FLIP_ROLL:
                roll = -roll
            if FLIP_PITCH:
                pitch = -pitch
            if self._recal.is_set():
                self._recal.clear()
                logic.recalibrate()
                print("[face] recalibrating: sit still, neutral face...")
            sel = brow if SELECT_SIGNAL == "brow" else smile
            events = logic.feed(now, pitch, roll, sel, jaw)
            self.calibrating = logic.calibrating
            if not logic.calibrating:
                self.reading = logic.last
            self.fps = 0.9 * self.fps + 0.1 / max(1e-3, now - last_frame_t) if last_frame_t else 0.0
            last_frame_t = now
            if was_calibrating and not logic.calibrating:
                print("[face] calibrated. Gestures are live.")
            was_calibrating = logic.calibrating
            if self.debug and not logic.calibrating:
                p, r, s2, j = logic.last
                print(f"pitch {p:+6.1f}  roll {r:+6.1f}  {SELECT_SIGNAL} {s2:+.2f}  jaw {j:+.2f}   ", end="\r")
            for name in events:
                print(f"[face] {name}")
                self.on_event(name)
        self.face_ok = False
        if not self._stop.is_set():   # the camera loop ended on its own
            self.error = "Camera not available. Check camera permission, or CAMERA_INDEX."


def signals(camera_index: int = 0, stop: threading.Event | None = None, misses: bool = False):
    """Yield (time, pitch, roll, brow, jaw, smile, blink) raw for every frame that has a face.
    With misses=True, also yield None for frames where no face was found."""
    import cv2
    import mediapipe as mp
    from mediapipe.tasks import python as mp_python
    from mediapipe.tasks.python import vision

    opts = vision.FaceLandmarkerOptions(
        base_options=mp_python.BaseOptions(model_asset_path=str(ensure_model())),
        running_mode=vision.RunningMode.VIDEO,
        num_faces=1,
        output_face_blendshapes=True,
        output_facial_transformation_matrixes=True,
    )
    landmarker = vision.FaceLandmarker.create_from_options(opts)
    cap = cv2.VideoCapture(camera_index)
    if not cap.isOpened():
        print("[face] could not open camera. Check camera permission for your terminal.")
        return
    last_ts = 0
    last_frame = time.time()
    try:
        while not (stop and stop.is_set()):
            ok, frame = cap.read()
            if not ok:
                if time.time() - last_frame > 3:
                    print("[face] no frames from the camera for 3 s. Is another app using it?")
                    return
                time.sleep(0.01)
                continue
            last_frame = time.time()
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            img = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
            last_ts = max(last_ts + 1, int(time.time() * 1000))  # must strictly increase
            res = landmarker.detect_for_video(img, last_ts)
            if not res.face_blendshapes or not res.facial_transformation_matrixes:
                if misses:
                    yield None
                continue
            bs = {c.category_name: c.score for c in res.face_blendshapes[0]}
            brow = max(bs.get("browInnerUp", 0),
                       (bs.get("browOuterUpLeft", 0) + bs.get("browOuterUpRight", 0)) / 2)
            pitch, roll = _euler(res.facial_transformation_matrixes[0])
            smile = (bs.get("mouthSmileLeft", 0) + bs.get("mouthSmileRight", 0)) / 2
            blink = min(bs.get("eyeBlinkLeft", 0), bs.get("eyeBlinkRight", 0))   # both eyes shut
            yield time.time(), pitch, roll, brow, bs.get("jawOpen", 0), smile, blink
    finally:
        cap.release()


# ---- guided measuring: python -m hub.face --measure ----
MEASURE_STEPS = [   # (key, what to do, seconds recorded)
    ("still", "Sit STILL: neutral face, mouth closed, look at the screen", 5),
    ("idle", "LOOK AROUND the screen like you're reading tiles. No gestures", 6),
    ("nod", "NOD: down and back up, 3 times", 8),
    ("left", "TILT your head toward your LEFT shoulder and back, 2 times", 8),
    ("right", "TILT your head toward your RIGHT shoulder and back, 2 times", 8),
    ("brow", "RAISE YOUR EYEBROWS and relax, 3 times. Keep your head still", 8),
    ("jaw", "OPEN YOUR MOUTH wide, hold a beat, close, 3 times", 8),
    ("smile", "SMILE wide and relax, 3 times", 8),
    ("blink", "CLOSE BOTH EYES for a full second, then open, 3 times", 9),
]
SKIP_S = 0.7   # ignore the start of each step (tail of the previous gesture)


def _pct(xs, q):
    xs = sorted(xs)
    return xs[round(q * (len(xs) - 1))]


def measure(camera_index: int = 0):
    """Walk through each gesture, record your real ranges, print suggested thresholds."""
    data = {k: [] for k, _, _ in MEASURE_STEPS}
    step, t_step = -1, 0.0
    print("Do each thing as soon as its line appears. Finish before the next line shows up.")
    for now, *vals in signals(camera_index):
        if step < 0 or now - t_step > MEASURE_STEPS[step][2]:
            step, t_step = step + 1, now
            if step == len(MEASURE_STEPS):
                break
            _, text, secs = MEASURE_STEPS[step]
            print(f"\n[{step + 1}/{len(MEASURE_STEPS)}] NOW, for {secs} s: {text}", flush=True)
        elif now - t_step > SKIP_S:
            data[MEASURE_STEPS[step][0]].append((*vals, now))
    if any(len(d) < 20 for d in data.values()):
        print("[face] not enough frames with a face. Check lighting and camera.")
        return

    # median baseline and 2nd/98th percentiles, so one glitchy frame can't set a range
    base = [_pct([v[i] for v in data["still"]], 0.5) for i in range(6)]
    lo = {k: [_pct([v[i] for v in d], 0.02) - base[i] for i in range(6)] for k, d in data.items()}
    hi = {k: [_pct([v[i] for v in d], 0.98) - base[i] for i in range(6)] for k, d in data.items()}
    peak = {k: [max(lo[k][i], hi[k][i], key=abs) for i in range(6)] for k in data}  # signed

    print("\nRange per step, relative to your neutral face (min .. max):")
    print(f"{'step':6s} {'pitch (deg)':>16s} {'roll (deg)':>16s} {'brow':>14s} {'jaw':>14s} {'smile':>14s}")
    for k in data:
        print(f"{k:6s} {lo[k][0]:+7.1f} ..{hi[k][0]:+6.1f} {lo[k][1]:+7.1f} ..{hi[k][1]:+6.1f} "
              f"{lo[k][2]:+6.2f} ..{hi[k][2]:+5.2f} {lo[k][3]:+6.2f} ..{hi[k][3]:+5.2f} "
              f"{lo[k][4]:+6.2f} ..{hi[k][4]:+5.2f}")

    def closed(d):   # longest stretch with both eyes shut, in seconds
        best, start = 0.0, None
        for v in d:
            if v[5] > 0.5:
                start = start or v[-1]
                best = max(best, v[-1] - start)
            else:
                start = None
        return best

    print("\nLongest eye closure per step (s): "
          + "  ".join(f"{k} {closed(d):.2f}" for k, d in data.items()))
    print("Select-gesture candidates (own step vs worst other step, bigger gap is better):")
    for name, i in (("brow", 2), ("smile", 4)):
        other = max(hi[k][i] for k in data if k != name)
        print(f"  {name:6s} {hi[name][i]:.2f} vs {other:.2f}")
    print(f"  blink  {closed(data['blink']):.2f} s vs "
          f"{max(closed(d) for k, d in data.items() if k != 'blink'):.2f} s")

    flip_roll = peak["left"][1] > 0            # unflipped code treats roll > 0 as "right"
    flip_pitch = peak["nod"][0] > 0            # code expects a nod to dip negative
    idle = [max(abs(lo["idle"][i]), abs(hi["idle"][i])) for i in range(4)]
    tilt = max(0.6 * min(abs(peak["left"][1]), abs(peak["right"][1])), idle[1] + 3)
    nod = max(0.5 * abs(peak["nod"][0]), idle[0] + 2)
    brow_t, jaw_t = 0.5 * hi["brow"][2], 0.5 * hi["jaw"][3]
    print("\nSuggested settings for the top of hub/face.py:")
    print(f"TILT_DEG = {tilt:.1f}\nTILT_RESET = {tilt * 0.4:.1f}\nNOD_DEG = {nod:.1f}")
    smile_t = 0.5 * hi["smile"][4]
    print(f"SELECT_T = {smile_t:.2f}   # for smile; for brow use {brow_t:.2f}")
    print(f"JAW_T = {jaw_t:.2f}\nFLIP_ROLL = {flip_roll}\nFLIP_PITCH = {flip_pitch}")

    if peak["left"][1] * peak["right"][1] > 0:
        print("WARNING: left and right tilts moved roll the same way. Redo those two steps.")
    for k in data:
        if k != "smile" and hi[k][4] > smile_t:
            print(f"WARNING: smile read {hi[k][4]:.2f} during '{k}', above SELECT_T. Select may misfire.")
    for k in ("idle", "nod", "left", "right", "brow", "smile"):
        if hi[k][3] > jaw_t:
            print(f"WARNING: mouth read {hi[k][3]:.2f} during '{k}', above JAW_T. Cancel may misfire.")
    for k in ("left", "right", "brow", "jaw"):   # only the nod direction can misfire a nod
        dip = hi[k][0] if flip_pitch else -lo[k][0]
        if dip > nod:
            print(f"WARNING: head dipped {dip:.1f} deg during '{k}', past NOD_DEG. Nod may misfire.")


def record(path: str, seconds: float = 40, camera_index: int = 0):
    """Save raw face readings to a file, to look at later or replay through the rules:
    python -m hub.face --record trace.json   (then do some nods, tilts, smiles)."""
    import json
    rows, t0 = [], None
    print(f"Recording {seconds:.0f} s. Sit neutral for 2 s, then do your gestures...")
    for reading in signals(camera_index, misses=True):
        now = time.time()
        t0 = t0 or now
        if now - t0 > seconds:
            break
        rows.append(None if reading is None else [round(reading[0] - t0, 3)] + [round(v, 4) for v in reading[1:]])
    Path(path).write_text(json.dumps({"flip_pitch": FLIP_PITCH, "flip_roll": FLIP_ROLL, "rows": rows}))
    faces = sum(1 for r in rows if r)
    print(f"Saved {len(rows)} frames ({faces} with a face) to {path}")


def replay(path: str):
    """Run a recording through the current rules and the classic ones, and list what each fires."""
    import json
    from .gestures import ClassicLogic, GestureLogic
    data = json.loads(Path(path).read_text())
    det = GestureDetector(on_event=lambda e: None)
    for name, logic in (("current rules", GestureLogic(det.tuning())), ("classic rules", ClassicLogic(det.tuning()))):
        events = []
        for row in data["rows"]:
            if not row:
                continue
            t, pitch, roll, brow, jaw, smile, _blink = row
            if data.get("flip_roll", FLIP_ROLL):
                roll = -roll
            if data.get("flip_pitch", FLIP_PITCH):
                pitch = -pitch
            for e in logic.feed(t, pitch, roll, brow if SELECT_SIGNAL == "brow" else smile, jaw):
                events.append(f"{t:5.1f}s {e}")
        print(f"{name}: {len(events)} events")
        for e in events:
            print("   ", e)


if __name__ == "__main__":
    if "--record" in sys.argv:
        record(sys.argv[sys.argv.index("--record") + 1], camera_index=int(os.getenv("CAMERA_INDEX", "0")))
        sys.exit()
    if "--replay" in sys.argv:
        replay(sys.argv[sys.argv.index("--replay") + 1])
        sys.exit()
    if "--measure" in sys.argv:
        try:
            measure()
        except KeyboardInterrupt:
            pass
        sys.exit()
    debug = "--debug" in sys.argv
    det = GestureDetector(on_event=lambda e: None, debug=debug)
    print("Sit still with a neutral face for 2 seconds... (Ctrl+C to quit)")
    try:
        det.run()
    except KeyboardInterrupt:
        pass
