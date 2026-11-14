"""All settings in one place. Override any of them with environment variables."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

BONSAI_URL = os.getenv("BONSAI_URL", "http://127.0.0.1:8080/v1/chat/completions")
BONSAI_MODEL = os.getenv("BONSAI_MODEL", "bonsai")

SIDECAR_URL = os.getenv("SIDECAR_URL", "http://127.0.0.1:8790")
# 1 = fake the sidecar (print instead of speaking, fake screen). Use this on Windows.
MOCK_SIDECAR = os.getenv("MOCK_SIDECAR", "1") == "1"

# 1 = run the webcam gesture detector inside the hub. 0 = keyboard only.
USE_CAMERA = os.getenv("USE_CAMERA", "1") == "1"
CAMERA_INDEX = int(os.getenv("CAMERA_INDEX", "0"))

PREGEN_PAUSE_S = float(os.getenv("PREGEN_PAUSE_S", "0.8"))
HISTORY_TURNS = int(os.getenv("HISTORY_TURNS", "12"))

PROFILE_PATH = ROOT / "hub" / "profile.json"
PROMPTS_DIR = ROOT / "prompts"
UI_DIR = ROOT / "ui"
