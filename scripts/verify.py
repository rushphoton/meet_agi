"""
WHY THIS EXISTS
Command 2 of 3: the one command that says whether the project is healthy.
It runs, in order:
  1. every backend test (contract, store, bus, receiver, placeholder engine);
  2. the contract drift check (API description + TypeScript types up to date);
  3. the start-backend command (scripts/serve.py) comes up and answers;
  4. the fake meeting end to end through a real backend and its live stream.
Prints ALL CHECKS PASSED and exits 0 only if all four pass.

FAILURE IT PREVENTS
Declaring something done on the strength of a partial check (CLAUDE.md rule 1).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _bootstrap import ROOT, ensure_venv  # noqa: E402

ensure_venv()

SERVE_SMOKE = (
    "import os, socket, subprocess, sys, time, httpx\n"
    "s = socket.socket(); s.bind(('127.0.0.1', 0)); port = s.getsockname()[1]; s.close()\n"
    "p = subprocess.Popen([sys.executable, 'scripts/serve.py'], env={**os.environ, 'PORT': str(port)})\n"
    "try:\n"
    "    for _ in range(100):\n"
    "        try:\n"
    "            r = httpx.get(f'http://127.0.0.1:{port}/api/health', timeout=1)\n"
    "            if r.status_code == 200: print('scripts/serve.py answered /api/health:', r.json()); sys.exit(0)\n"
    "        except httpx.HTTPError: pass\n"
    "        if p.poll() is not None: sys.exit('scripts/serve.py exited early')\n"
    "        time.sleep(0.2)\n"
    "    sys.exit('scripts/serve.py did not answer within 20 s')\n"
    "finally:\n"
    "    p.terminate(); p.wait(timeout=10)\n"
)

steps = [
    ("Backend tests", [sys.executable, "-m", "pytest"]),
    ("Contract drift check", [sys.executable, "scripts/export_openapi.py", "--check"]),
    ("Start-backend command answers", [sys.executable, "-c", SERVE_SMOKE]),
    ("Fake meeting end to end", [sys.executable, "scripts/replay.py", "--speed", "50"]),
]
env = {**os.environ, "OFFLINE": "1"}
failed = []
for name, cmd in steps:
    print(f"\n=== {name} ===", flush=True)
    if subprocess.run(cmd, cwd=ROOT, env=env).returncode != 0:
        failed.append(name)

print()
if failed:
    print("FAILED: " + ", ".join(failed))
    sys.exit(1)
print("ALL CHECKS PASSED")
