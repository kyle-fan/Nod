#!/bin/bash
# Start everything Nod needs on the demo Mac: Bonsai (:8080), the Swift sidecar (:8790), the hub (:8000).
# Run it from the terminal app that has Microphone, Camera and Accessibility permission.
# Stop with ./stop.sh. Logs: /tmp/nod/*.log
#
# Settings (optional):
#   BONSAI_DIR=~/Bonsai-demo   where PrismML's Bonsai-demo is cloned
#   BONSAI_MODEL=9B            Bonsai 2 size
#   USE_CAMERA=1               0 = keyboard only (no webcam gestures)
#   NOD_OVERLAY=1              float the tiles over the screen instead of using a browser tab
#   NOD_WATCH_MESSAGES=1       tell the hub when a text message arrives, so a nod can answer it
#   CAMERA_INDEX=1             use the second camera (when an iPhone grabs the first slot)
#   NOD_SILENT=1               rehearsal: speech is generated but not played
set -u
ROOT="$(cd "$(dirname "$0")" && pwd)"
RUN=/tmp/nod
mkdir -p "$RUN"
BONSAI_DIR="${BONSAI_DIR:-$HOME/Bonsai-demo}"
export BONSAI_MODEL="${BONSAI_MODEL:-9B}"
USE_CAMERA="${USE_CAMERA:-1}"

up() { curl -s -m 2 -o /dev/null "$1"; }

wait_for() { # name url seconds logfile
    printf "  waiting for %s " "$1"
    for _ in $(seq 1 "$3"); do
        if up "$2"; then echo " ready"; return 0; fi
        printf "."; sleep 1
    done
    echo " FAILED (see $4)"; tail -5 "$4" 2>/dev/null; return 1
}

echo "1/3 Bonsai (llama-server :8080)"
if up http://127.0.0.1:8080/health; then
    echo "  already running"
else
    if [ ! -x "$BONSAI_DIR/scripts/start_llama_server.sh" ]; then
        echo "  Bonsai-demo not found at $BONSAI_DIR (set BONSAI_DIR=...)"; exit 1
    fi
    # Flags from HANDOFF.md: thinking off, 8k context, one slot (the hub sends one request at a time).
    (cd "$BONSAI_DIR" || exit 1; BONSAI_THINKING=0 BONSAI_CTX=8192 nohup ./scripts/start_llama_server.sh -np 1 > "$RUN/bonsai.log" 2>&1 < /dev/null &)
    wait_for Bonsai http://127.0.0.1:8080/health 120 "$RUN/bonsai.log" || exit 1
fi

echo "2/3 Sidecar (:8790)"
if up http://127.0.0.1:8790/health; then
    echo "  already running"
else
    (cd "$ROOT/sidecar" && swift build -c release 2>&1 | grep -E "error|Build complete" ) || { echo "  sidecar build failed"; exit 1; }
    (cd "$ROOT/sidecar" || exit 1; nohup .build/release/NodSidecar > "$RUN/sidecar.log" 2>&1 < /dev/null & echo $! > "$RUN/sidecar.pid")
    # Loading the speech models usually takes 10 s, but can take a few minutes after a reboot.
    wait_for sidecar http://127.0.0.1:8790/health 400 "$RUN/sidecar.log" || exit 1
    grep -E "WARNING|MIC ERROR" "$RUN/sidecar.log" | sed 's/^/  /'
fi

echo "3/3 Hub (:8000)"
if up http://127.0.0.1:8000/; then
    echo "  already running"
else
    if [ ! -x "$ROOT/.venv/bin/uvicorn" ]; then
        echo "  no Python env yet: python3 -m venv .venv && source .venv/bin/activate && pip install -r hub/requirements.txt"
        exit 1
    fi
    (cd "$ROOT" || exit 1; USE_CAMERA="$USE_CAMERA" MOCK_SIDECAR=0 nohup .venv/bin/uvicorn hub.main:app --port 8000 > "$RUN/hub.log" 2>&1 < /dev/null & echo $! > "$RUN/hub.pid")
    wait_for hub http://127.0.0.1:8000/ 30 "$RUN/hub.log" || exit 1
fi

echo
echo "Nod is up. Open http://127.0.0.1:8000   (logs: $RUN, stop: ./stop.sh, check: ./doctor.sh)"
