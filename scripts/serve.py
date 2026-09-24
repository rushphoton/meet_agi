"""
WHY THIS EXISTS
Command 1 of 3: starts the backend on http://localhost:8000 (dev endpoints on,
so the fake meeting can run against it). Stop it with Ctrl+C.

FAILURE IT PREVENTS
Remembering uvicorn flags and module paths.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _bootstrap import ROOT, ensure_venv  # noqa: E402

ensure_venv()
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))  # so "backend.app.main" is importable
os.environ.setdefault("DEV_MODE", "1")
port = int(os.environ.get("PORT", "8000"))

import uvicorn  # noqa: E402

print(f"Meet AGI backend on http://localhost:{port}  (health: http://localhost:{port}/api/health)")
# timeout_keep_alive: uvicorn's default (5 s) equals the dashboard's 5 s health poll, so the dashboard's
# proxy sometimes reused a connection uvicorn had just closed -> "read ECONNRESET" and a 500 on the
# live screen (seen at integrate, 24 Sep 2026). 65 s outlasts every poll interval.
uvicorn.run("backend.app.main:app", host="127.0.0.1", port=port, log_level="warning", timeout_keep_alive=65)
