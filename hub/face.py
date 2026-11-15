"""Webcam head/face gestures with MediaPipe Face Landmarker.

Events: nod, left, right, select (smile, or eyebrows via SELECT_SIGNAL), cancel (open mouth).

Measure your own ranges: python -m hub.face --measure   (70 s guided, prints suggested thresholds)
Watch numbers live:      python -m hub.face --debug
If tilting right prints "left", set FLIP_ROLL = True. Same for nod with FLIP_PITCH.
"""
import math
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

    def start(self):
        threading.Thread(target=self.run, daemon=True).start()

    def stop(self):
        self._stop.set()

    def recalibrate(self):
        """Re-learn the neutral pose (user shifted in their seat, lighting changed)."""
        self._recal.set()

    def run(self):
        base = None
        calib = []
        t_start = None
        last_event = tilt_at = 0.0
        tilt_armed = True
        sel_since = jaw_since = None
        sel_armed = jaw_armed = True
        pitch_hist = deque()  # (t, pitch)
        recent = deque(maxlen=3)   # median of 3 frames drops single-frame tracking glitches
        head_moved = 0.0

        def fire(name, now):
            """False if still cooling down. Callers stay armed then, so the gesture
            fires a moment later instead of being lost."""
            nonlocal last_event
            if now - last_event < COOLDOWN:
                return False
            last_event = now
            print(f"[face] {name}")
            self.on_event(name)
            return True

        for now, pitch, roll, brow, jaw, smile, _blink in signals(self.camera_index, self._stop):
            if FLIP_ROLL:
                roll = -roll
            if FLIP_PITCH:
                pitch = -pitch
            sel = brow if SELECT_SIGNAL == "brow" else smile
            recent.append((pitch, roll, sel, jaw))
            if len(recent) == 3:
                pitch, roll, sel, jaw = (sorted(c)[1] for c in zip(*recent))

            # 1) calibrate: sit still, neutral face
            if self._recal.is_set():
                self._recal.clear()
                base, calib, t_start = None, [], None
                print("[face] recalibrating: sit still, neutral face...")
            if base is None:
                t_start = t_start or now
                calib.append((pitch, roll, sel, jaw))
                if now - t_start > CALIBRATE_S:
                    base = tuple(sorted(c)[len(c) // 2] for c in zip(*calib))   # median
                    print("[face] calibrated. Gestures are live.")
                continue
            p, r, s, j = pitch - base[0], roll - base[1], sel - base[2], jaw - base[3]

            if self.debug:
                print(f"pitch {p:+6.1f}  roll {r:+6.1f}  {SELECT_SIGNAL} {s:+.2f}  jaw {j:+.2f}   ", end="\r")

            # 2) tilt left / right: fires once, repeats while held, re-arms at center
            if abs(r) > TILT_DEG:
                if tilt_armed or (TILT_REPEAT and now - tilt_at > TILT_REPEAT):
                    if fire("right" if r > 0 else "left", now):
                        tilt_armed, tilt_at = False, now
            elif abs(r) < TILT_RESET:
                tilt_armed = True

            # 3) nod: pitch dips down then comes back up, with little tilt
            pitch_hist.append((now, p))
            while pitch_hist and now - pitch_hist[0][0] > NOD_WINDOW:
                pitch_hist.popleft()
            if abs(r) < 8 and p > -3 and any(v < -NOD_DEG for _, v in pitch_hist):
                if fire("nod", now):
                    pitch_hist.clear()

            # 4) select: smile (or eyebrows) held briefly. Eyebrows only count with a level
            #    head, because nods and tilts push the eyebrow reading up on their own.
            if abs(p) > NOD_DEG * 0.5 or abs(r) > TILT_RESET:
                head_moved = now
            steady = SELECT_SIGNAL != "brow" or now - head_moved > BROW_QUIET
            if s > SELECT_T and steady and sel_armed:
                sel_since = sel_since or now
                if now - sel_since > SELECT_HOLD and fire("select", now):
                    sel_armed = False
            else:
                if not steady:
                    sel_since = None
                if s < SELECT_T * 0.5:
                    sel_since, sel_armed = None, True

            # 5) open mouth = cancel (hold)
            if j > JAW_T and jaw_armed:
                jaw_since = jaw_since or now
                if now - jaw_since > JAW_HOLD and fire("cancel", now):
                    jaw_armed = False
            elif j < JAW_T * 0.5:
                jaw_since, jaw_armed = None, True


def signals(camera_index: int = 0, stop: threading.Event | None = None):
    """Yield (time, pitch, roll, brow, jaw, smile, blink) raw for every frame that has a face."""
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


if __name__ == "__main__":
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
