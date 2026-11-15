"""The camera thread end to end, with made-up frames instead of a webcam."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import hub.face as face   # noqa: E402


def frames(flip):
    """2 s neutral, a nod, 1 s neutral, no face for 2 s, face back and a smile."""
    t, out = 100.0, []
    def add(seconds, pitch=0.0, smile=0.0, face_in_view=True):
        nonlocal t
        for _ in range(int(seconds * 30)):
            t += 1 / 30
            out.append((t, pitch * flip, 1.0, 0.0, 0.0, smile, 0.0) if face_in_view else None)
    add(2.0)
    for p in (-5, -12, -20, -20, -12, -4, 3, 5, 2, 0):
        add(0.07, pitch=p)
    add(1.0)
    add(2.0, face_in_view=False)
    add(0.5)
    add(0.8, smile=0.85)
    add(0.5)
    return out


def run(flip_pitch):
    events, statuses = [], []
    seq = frames(-1 if flip_pitch else 1)
    old_signals, old_flip, old_time = face.signals, face.FLIP_PITCH, face.time.time
    clock = {"now": 100.0}

    def fake_signals(camera_index=0, stop=None, misses=False):
        for f in seq:
            clock["now"] = f[0] if f else clock["now"] + 1 / 30
            statuses.append(det.status()["face"])
            yield f

    face.signals, face.FLIP_PITCH = fake_signals, flip_pitch
    face.time.time = lambda: clock["now"]
    try:
        det = face.GestureDetector(on_event=events.append)
        det._stop.set()          # so the end of the fake stream is not reported as a camera error
        det.run()
    finally:
        face.signals, face.FLIP_PITCH, face.time.time = old_signals, old_flip, old_time
    return events, statuses, det


def test_nod_and_smile_reach_the_hub():
    events, statuses, det = run(flip_pitch=False)
    assert events == ["nod", "select"], events
    assert True in statuses and False in statuses[150:]   # face seen, then reported lost
    assert det.calibrating is False


def test_flipped_camera_pitch():
    events, _, _ = run(flip_pitch=True)
    assert events == ["nod", "select"], events


if __name__ == "__main__":
    test_nod_and_smile_reach_the_hub()
    test_flipped_camera_pitch()
    print("ok   detector")
