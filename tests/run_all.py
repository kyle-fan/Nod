"""Runs every fast test file and prints one line per check. Works on any OS:
    python tests/run_all.py          (on the Mac, ./test.sh does the same)
"""
import subprocess
import sys
from pathlib import Path

here = Path(__file__).resolve().parent
failed = False
total = 0
for test in sorted(here.glob("test_*.py")):
    print(f"== {test.name}")
    out = subprocess.run([sys.executable, str(test)], capture_output=True, text=True, cwd=here.parent)
    lines = [line for line in out.stdout.splitlines() if line.startswith(("ok", "FAIL"))]
    total += sum(1 for line in lines if line.startswith("ok"))
    print("\n".join(lines))
    if out.returncode != 0:
        failed = True
        if not any(line.startswith("FAIL") for line in lines):
            print(out.stderr[-1500:])
print(f"{total} checks passed")
print("ALL TESTS PASSED" if not failed else "SOME TESTS FAILED")
sys.exit(1 if failed else 0)
