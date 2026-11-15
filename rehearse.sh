#!/bin/bash
# Rehearse the whole thing with a pretend Mac: no microphone, camera, speech or permissions.
# Starts Bonsai if needed, then the hub on :8000 with the keyboard and the dev box.
#   ./rehearse.sh            Messages in front        ./rehearse.sh Safari     Safari in front
ROOT="$(cd "$(dirname "$0")" && pwd)"
BONSAI_DIR="${BONSAI_DIR:-$HOME/Bonsai-demo}"
mkdir -p /tmp/nod
if ! curl -s -m 2 http://127.0.0.1:8080/health | grep -q ok; then
    if [ -x "$BONSAI_DIR/scripts/start_llama_server.sh" ]; then
        echo "Starting Bonsai..."
        (cd "$BONSAI_DIR" || exit 1; BONSAI_MODEL="${BONSAI_MODEL:-9B}" BONSAI_THINKING=0 BONSAI_CTX=8192 nohup ./scripts/start_llama_server.sh -np 1 > /tmp/nod/bonsai.log 2>&1 < /dev/null &)
        for _ in $(seq 1 90); do curl -s -m 2 http://127.0.0.1:8080/health | grep -q ok && break; sleep 1; done
    else
        echo "Bonsai isn't running and wasn't found in $BONSAI_DIR: the hub will show basic replies only."
    fi
fi
echo "Open http://127.0.0.1:8000"
echo "  Type the friend's line in the dev box, then N (nod), arrows, Space (pick), Esc (back), 1-5 (pick a tile)."
echo "  /text Andrew: where are you     pretends a text message arrived"
echo "  Ctrl+C to stop."
cd "$ROOT" && USE_CAMERA=0 MOCK_SIDECAR=1 MOCK_APP="${1:-Messages}" LEARN=0 exec .venv/bin/uvicorn hub.main:app --port 8000
