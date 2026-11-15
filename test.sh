#!/bin/bash
# Run every automated check that needs no camera, microphone, model or network.
# ./test.sh --all also runs the real Swift sidecar against a recording (needs the models on disk).
cd "$(dirname "$0")" || exit 1
PY=.venv/bin/python
[ -x "$PY" ] || { echo "no Python env: python3 -m venv .venv && .venv/bin/pip install -r hub/requirements.txt"; exit 1; }
"$PY" tests/run_all.py
fail=$?
if [ "${1:-}" = "--all" ]; then   # also the real sidecar with a recording (about a minute, silent)
    echo "== tests/sidecar_smoke.py"
    "$PY" tests/sidecar_smoke.py || fail=1
fi
exit $fail
