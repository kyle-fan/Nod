#!/bin/bash
# Check that everything Nod needs is in place, and say plainly what is wrong.
# Run it after ./start.sh, or on its own to check what is installed.
ROOT="$(cd "$(dirname "$0")" && pwd)"
PY="$ROOT/.venv/bin/python"
ok()   { printf "  \033[32mOK\033[0m    %s\n" "$1"; }
warn() { printf "  \033[33mCHECK\033[0m %s\n" "$1"; WARN=$((WARN+1)); }
bad()  { printf "  \033[31mFAIL\033[0m  %s\n" "$1"; FAIL=$((FAIL+1)); }
WARN=0; FAIL=0
jget() { "$PY" -c "import json,sys
d=json.load(sys.stdin)
for k in sys.argv[1].split('.'):
    d=d.get(k) if isinstance(d,dict) else None
print('' if d is None else d)" "$1" 2>/dev/null; }

echo "Files on this Mac (needed with Wi-Fi off)"
M="$HOME/Documents/huggingface/models"
[ -d "$M/argmaxinc/ttskit-coreml/qwen3_tts" ] && ok "voice model (TTSKit)" || bad "voice model missing: run the sidecar once with Wi-Fi on"
[ -d "$M/argmaxinc/whisperkit-coreml/openai_whisper-large-v3-v20240930_626MB" ] && ok "speech model (Whisper large-v3 turbo)" || bad "Whisper large model missing: run the sidecar once with Wi-Fi on"
[ -d "$M/argmaxinc/whisperkit-coreml/openai_whisper-small.en" ] && ok "caption model (Whisper small.en)" || warn "Whisper small.en missing: captions will use the large model (slower)"
[ -f "$ROOT/hub/models/face_landmarker.task" ] && ok "face model (MediaPipe)" || warn "face model missing: python -m hub.face --measure downloads it (needs Wi-Fi once)"
ls "${BONSAI_DIR:-$HOME/Bonsai-demo}"/models/bonsai2-gguf/*/*.gguf >/dev/null 2>&1 && ok "Bonsai 2 model" || bad "Bonsai model not found in ${BONSAI_DIR:-$HOME/Bonsai-demo}"
[ -x "$PY" ] && ok "Python environment (.venv)" || bad "no .venv: python3 -m venv .venv && .venv/bin/pip install -r hub/requirements.txt"
[ -x "$ROOT/sidecar/.build/release/NodSidecar" ] && ok "sidecar is built" || warn "sidecar not built yet (start.sh builds it)"

echo "Sound and camera"
AUDIO=$(system_profiler SPAudioDataType 2>/dev/null)
IN=$(echo "$AUDIO" | grep -B6 "Default Input Device: Yes" | grep -E "^ {8}[^ ].*:$" | tail -1 | sed 's/^ *//; s/:$//')
OUT=$(echo "$AUDIO" | grep -B6 "Default Output Device: Yes" | grep -E "^ {8}[^ ].*:$" | tail -1 | sed 's/^ *//; s/:$//')
case "$IN" in *MacBook*|*USB*|*Yeti*|*Microphone*) ok "microphone: $IN";; "") warn "no default microphone found";; *) warn "microphone is \"$IN\": for the demo use the MacBook microphone (headphones hear the wrong thing)";; esac
case "$OUT" in *MacBook*|*Speaker*|*Display*) ok "speakers: $OUT";; "") warn "no default speaker found";; *) warn "sound goes to \"$OUT\": the friend won't hear replies unless that is a speaker";; esac
CAMS=$(system_profiler SPCameraDataType 2>/dev/null | grep -E "^ {4}[^ ].*:$" | sed 's/^ *//; s/:$//')
if echo "$CAMS" | grep -qi "iphone\|^I Camera"; then warn "an iPhone is offering itself as a camera (Continuity Camera): turn it off on the phone, or start with CAMERA_INDEX=1"; else ok "camera: $(echo "$CAMS" | head -1)"; fi

echo "Running now"
if curl -s -m 2 http://127.0.0.1:8080/health | grep -q ok; then
    T0=$("$PY" -c "import time;print(time.time())")
    curl -s -m 20 http://127.0.0.1:8080/v1/chat/completions -H 'content-type: application/json' \
        -d '{"messages":[{"role":"user","content":"Say ok."}],"max_tokens":4,"chat_template_kwargs":{"enable_thinking":false}}' >/dev/null
    ok "Bonsai answers on :8080 ($("$PY" -c "import time;print(round(time.time()-$T0,1))") s for a tiny reply)"
else bad "Bonsai is not running on :8080 (./start.sh)"; fi

SC=$(curl -s -m 3 http://127.0.0.1:8790/status)
if [ -n "$SC" ]; then
    ok "sidecar answers on :8790"
    [ "$(echo "$SC" | jget accessibility)" = "True" ] && ok "Accessibility permission (reads and controls apps)" || bad "no Accessibility permission: System Settings > Privacy & Security > Accessibility > allow the terminal you started Nod from, then restart"
    B1=$(echo "$SC" | jget ears.buffers_from_mic); sleep 1.2; B2=$(curl -s -m 3 http://127.0.0.1:8790/status | jget ears.buffers_from_mic)
    if [ -n "$B1" ] && [ -n "$B2" ] && [ "$B2" -gt "$B1" ] 2>/dev/null; then ok "microphone is delivering sound"; else bad "no sound from the microphone: allow Microphone for the terminal (System Settings > Privacy & Security)"; fi
    [ "$(echo "$SC" | jget silent)" = "True" ] && warn "the voice is in silent test mode: curl -X POST 'localhost:8790/quiet?on=0'"
    [ "$(echo "$SC" | jget overlay)" = "True" ] && ok "overlay window is showing" || echo "        (overlay is off: NOD_OVERLAY=1 ./start.sh, or curl -X POST 'localhost:8790/overlay?on=1')"
else bad "sidecar is not running on :8790 (./start.sh)"; fi

# Messages: are the people in the profile reachable? (read-only: nothing is opened or typed)
if [ -n "$SC" ] && [ "$(echo "$SC" | jget accessibility)" = "True" ] && pgrep -x Messages >/dev/null; then
    curl -s -m 8 "http://127.0.0.1:8790/screen?app=Messages" | "$PY" -c "
import json, sys
try:
    screen = json.load(sys.stdin)
    people = list(json.load(open('$ROOT/hub/profile.json')).get('people', {}))
except Exception as e:
    print('  \033[33mCHECK\033[0m could not read Messages:', e); sys.exit()
names = [e['label'] for e in screen.get('elements', []) if e['role'] != 'textfield' and e['label'] not in ('Send', 'Back to conversations')]
box = [e['label'] for e in screen.get('elements', []) if e['role'] == 'textfield']
if box:
    print('  \033[32mOK\033[0m    Messages: a chat is open (' + box[0].split(':')[0] + ')')
elif names:
    print('  \033[32mOK\033[0m    Messages: %d chats visible (%s%s)' % (len(names), ', '.join(names[:5]), ', ...' if len(names) > 5 else ''))
    for p in people:
        hit = [n for n in names if n.lower() == p.lower() or n.lower().split()[0] == p.lower().split()[0]]
        if hit: print('  \033[32mOK\033[0m    "%s" from the profile has a chat (%s)' % (p, hit[0]))
        else: print('  \033[33mCHECK\033[0m no chat called "%s" among the first 12: "tell %s ..." will say it can\'t see the chat. Rename a contact or bring that chat to the top' % (p, p))
else:
    print('  \033[33mCHECK\033[0m Messages is open but no chats were found: is its window on screen?')
"
elif [ -n "$SC" ] && [ "$(echo "$SC" | jget accessibility)" = "True" ]; then
    warn "Messages is not open: open it for the texting part of the demo"
fi

HB=$(curl -s -m 5 http://127.0.0.1:8000/status)
if [ -n "$HB" ]; then
    ok "hub answers on :8000"
    [ "$(echo "$HB" | jget mock_sidecar)" = "True" ] && warn "hub is using the pretend Mac (MOCK_SIDECAR=1): fine for rehearsal, not for the demo"
    if [ "$(echo "$HB" | jget camera.on)" = "True" ]; then
        ERR=$(echo "$HB" | jget camera.error)
        if [ -n "$ERR" ]; then bad "camera: $ERR"
        elif [ "$(echo "$HB" | jget camera.face)" = "True" ]; then ok "camera sees a face"
        else warn "camera is on but sees no face right now"; fi
    else echo "        (camera gestures are off: USE_CAMERA=1 ./start.sh)"; fi
else bad "hub is not running on :8000 (./start.sh)"; fi

echo
if [ $FAIL -gt 0 ]; then echo "$FAIL thing(s) need fixing, $WARN to check."; exit 1
elif [ $WARN -gt 0 ]; then echo "Working, with $WARN thing(s) worth a look."
else echo "Everything looks ready."; fi
