"""Webcam head/face gestures with MediaPipe Face Landmarker.

Events: nod, left, right, select (eyebrows), cancel (open mouth).

Tune thresholds live:   python -m hub.face --debug
It prints pitch / roll / brow / jaw so you can see your own numbers.
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
TILT_DEG = 12.0      # roll past this = left/right
TILT_RESET = 5.0     # must come back within this before the next tilt
NOD_DEG = 9.0        # pitch dip needed for a nod
NOD_WINDOW = 0.9     # seconds: down and back up within this
BROW_T = 0.30        # eyebrow raise above baseline
BROW_HOLD = 0.15     # seconds held
JAW_T = 0.40         # mouth open above baseline
JAW_HOLD = 0.35
COOLDOWN = 0.35      # seconds between any two events
FLIP_ROLL = False
FLIP_PITCH = False
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

    def start(self):
        threading.Thread(target=self.run, daemon=True).start()

    def stop(self):
        self._stop.set()

    def run(self):
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
        cap = cv2.VideoCapture(self.camera_index)
        if not cap.isOpened():
            print("[face] could not open camera. Check camera permission for your terminal.")
            return

        base = None
        calib = []
        t_start = time.time()
        last_event = 0.0
        tilt_armed = True
        brow_since = jaw_since = None
        brow_armed = jaw_armed = True
        pitch_hist = deque()  # (t, pitch)
        last_ts = 0

        def fire(name):
            nonlocal last_event
            now = time.time()
            if now - last_event < COOLDOWN:
                return
            last_event = now
            print(f"[face] {name}")
            self.on_event(name)

        while not self._stop.is_set():
            ok, frame = cap.read()
            if not ok:
                time.sleep(0.01)
                continue
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            img = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
            last_ts = max(last_ts + 1, int(time.time() * 1000))  # must strictly increase
            res = landmarker.detect_for_video(img, last_ts)
            if not res.face_blendshapes or not res.facial_transformation_matrixes:
                continue

            bs = {c.category_name: c.score for c in res.face_blendshapes[0]}
            brow = max(bs.get("browInnerUp", 0),
                       (bs.get("browOuterUpLeft", 0) + bs.get("browOuterUpRight", 0)) / 2)
            jaw = bs.get("jawOpen", 0)
            pitch, roll = _euler(res.facial_transformation_matrixes[0])
            if FLIP_ROLL:
                roll = -roll
            if FLIP_PITCH:
                pitch = -pitch
            now = time.time()

            # 1) calibrate: sit still, neutral face
            if base is None:
                calib.append((pitch, roll, brow, jaw))
                if now - t_start > CALIBRATE_S:
                    n = len(calib)
                    base = tuple(sum(c[i] for c in calib) / n for i in range(4))
                    print("[face] calibrated. Gestures are live.")
                continue
            p, r, b, j = pitch - base[0], roll - base[1], brow - base[2], jaw - base[3]

            if self.debug:
                print(f"pitch {p:+6.1f}  roll {r:+6.1f}  brow {b:+.2f}  jaw {j:+.2f}", end="\r")

            # 2) tilt left / right (fires once, re-arms at center)
            if tilt_armed and abs(r) > TILT_DEG:
                fire("right" if r > 0 else "left")
                tilt_armed = False
            elif abs(r) < TILT_RESET:
                tilt_armed = True

            # 3) nod: pitch dips down then comes back up, with little tilt
            pitch_hist.append((now, p))
            while pitch_hist and now - pitch_hist[0][0] > NOD_WINDOW:
                pitch_hist.popleft()
            if abs(r) < 8 and p > -3 and any(v < -NOD_DEG for _, v in pitch_hist):
                fire("nod")
                pitch_hist.clear()

            # 4) eyebrows = select (hold briefly)
            if b > BROW_T and brow_armed:
                brow_since = brow_since or now
                if now - brow_since > BROW_HOLD:
                    fire("select")
                    brow_armed = False
            elif b < BROW_T * 0.5:
                brow_since, brow_armed = None, True

            # 5) open mouth = cancel (hold)
            if j > JAW_T and jaw_armed:
                jaw_since = jaw_since or now
                if now - jaw_since > JAW_HOLD:
                    fire("cancel")
                    jaw_armed = False
            elif j < JAW_T * 0.5:
                jaw_since, jaw_armed = None, True

        cap.release()


if __name__ == "__main__":
    debug = "--debug" in sys.argv
    det = GestureDetector(on_event=lambda e: None, debug=debug)
    print("Sit still with a neutral face for 2 seconds... (Ctrl+C to quit)")
    try:
        det.run()
    except KeyboardInterrupt:
        pass
