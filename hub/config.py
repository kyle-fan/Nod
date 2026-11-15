"""All settings in one place. Override any of them with environment variables."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

BONSAI_URL = os.getenv("BONSAI_URL", "http://127.0.0.1:8080/v1/chat/completions")
BONSAI_MODEL = os.getenv("BONSAI_MODEL", "bonsai")
# How many parallel slots llama-server was started with (-np N). With more than one, each
# kind of prompt keeps its own slot so its cached prefix survives. start.sh sets this.
BONSAI_SLOTS = int(os.getenv("BONSAI_SLOTS", "1"))

SIDECAR_URL = os.getenv("SIDECAR_URL", "http://127.0.0.1:8790")
# 1 = fake the sidecar (print instead of speaking, fake screen). Use this on Windows.
MOCK_SIDECAR = os.getenv("MOCK_SIDECAR", "1") == "1"

# 1 = run the webcam gesture detector inside the hub. 0 = keyboard only.
USE_CAMERA = os.getenv("USE_CAMERA", "1") == "1"
CAMERA_INDEX = int(os.getenv("CAMERA_INDEX", "0"))
# Which app the fake Mac starts in (MOCK_SIDECAR=1 only): Messages or Safari.
MOCK_APP = os.getenv("MOCK_APP", "Messages")

PREGEN_PAUSE_S = float(os.getenv("PREGEN_PAUSE_S", "0.8"))
HISTORY_TURNS = int(os.getenv("HISTORY_TURNS", "12"))

PROFILE_PATH = ROOT / "hub" / "profile.json"
PROMPTS_DIR = ROOT / "prompts"
UI_DIR = ROOT / "ui"

# The replies he actually picks are remembered here (on this Mac only) and shown to Bonsai
# as examples of how he really talks. LEARN=0 turns it off; delete the file to forget.
LEARNED_PATH = ROOT / "hub" / "learned.json"
LEARN = os.getenv("LEARN", "1") == "1"
LEARNED_SHOWN = int(os.getenv("LEARNED_SHOWN", "6"))

# EXPRESS=1: when the friend asks him to text someone in his profile, skip the step-by-step
# tiles. After he agrees he picks a wording, the Mac opens the chat and types it, and he
# confirms: 3 picks instead of 5. Off by default so every step stays visible.
EXPRESS = os.getenv("EXPRESS", "0") == "1"

# NOD_GESTURES=classic: the older gesture rules (neutral learned once, no drift following).
# Can also be switched while running, with the dev bar's checkbox or POST /settings.
CLASSIC_GESTURES = os.getenv("NOD_GESTURES", "").lower() == "classic"
