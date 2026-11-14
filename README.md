# Blink

Talk and use your computer with a nod, a head tilt, and a smile. Fully on-device, works with Wi-Fi off.

Built for the Local AI Hackathon (Recreate theme). Uses Bonsai 2 for prediction and Argmax (WhisperKit + TTSKit) for speech.

## What we recreated

- **Synchron's BCI chat (powered by OpenAI):** a brain implant where AI predicts replies and the user picks one with a single "click." We replace the implant with webcam face gestures and the cloud with Bonsai 2 on the laptop.
- **AAC apps like Lingraphica Conversations and Predictable:** listen to the conversation partner and suggest replies. Ours never sends a word off the device.
- **Google Project Gameface:** face gestures as a mouse. We add an AI that predicts what you want, so one gesture does a whole step.

## Quick start (any OS, no camera needed)

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    Mac: source .venv/bin/activate
pip install -r hub/requirements.txt

# start Bonsai 2 with llama-server on port 8080 (see PrismML's Bonsai-demo repo)

# keyboard only, fake sidecar:
#   Windows PowerShell:  $env:USE_CAMERA="0"; $env:MOCK_SIDECAR="1"
#   Mac:                 export USE_CAMERA=0 MOCK_SIDECAR=1
uvicorn hub.main:app --port 8000
```

Open http://127.0.0.1:8000. Type what the friend says in the dev box, then use the keys: `N` nod, `←` `→` move, `Space` pick, `Esc` back, `D` hide the dev bar.

If Bonsai isn't running, the hub still works with fallback replies and shows "Bonsai offline."

## Turn on the camera

```bash
python -m hub.face --measure    # 70 s guided: do each gesture, get suggested thresholds for your face and camera
python -m hub.face --debug      # sit still 2 s, then try each gesture and watch which events fire
```
Put the suggested values at the top of `hub/face.py` (thresholds differ per person and per camera, so redo this on the demo Mac). Select is a smile; eyebrows don't read behind glasses, but `SELECT_SIGNAL = "brow"` switches back. Then run the hub with `USE_CAMERA=1`.

## Demo setup (Mac)

1. Bonsai: llama-server on port 8080.
2. Sidecar: `sidecar/` (see its README) on port 8790.
3. Hub: `USE_CAMERA=1 MOCK_SIDECAR=0 uvicorn hub.main:app --port 8000`
4. Browser full screen at http://127.0.0.1:8000. Turn Wi-Fi off.

Or run steps 1 to 3 in one go with `./start.sh` (and `./stop.sh` to shut down). Logs go to `/tmp/nod/`.

## Workflow

- `SPEC.md` is the source of truth for every interface. Change it first, then the code.
- Kyle owns `hub/`, `ui/`, `prompts/`. Partner owns `sidecar/`. Separate folders means no merge conflicts.
- Commit to `main` small and often: `git pull --rebase && git push`.
- Tune prompts in `prompts/*.txt` and the character in `hub/profile.json` without touching code.
