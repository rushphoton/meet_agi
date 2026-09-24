"""
WHY THIS EXISTS
Makes every script runnable with a plain `python scripts/<name>.py` on a fresh
laptop: it creates the project's private Python environment (.venv) the first
time, installs backend/requirements.txt into it (again only if that file
changed), then re-runs the script inside it.

FAILURE IT PREVENTS
"ModuleNotFoundError" on Ray's machine, and installing packages into the
system Python by accident.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQS = ROOT / "backend" / "requirements.txt"


def venv_dir() -> Path:
    return Path(os.environ.get("MEETAGI_VENV") or ROOT / ".venv")


def venv_python() -> Path:
    v = venv_dir()
    return v / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def ensure_venv() -> None:
    """Call at the top of every script. Returns only when running inside the ready venv."""
    if sys.version_info < (3, 10):
        sys.exit(f"Python 3.10+ needed, found {sys.version.split()[0]}")
    py = venv_python()
    inside = Path(sys.prefix).resolve() == venv_dir().resolve()
    if not py.exists():
        print("[setup] creating .venv (first run only)...", flush=True)
        subprocess.run([sys.executable, "-m", "venv", str(venv_dir())], check=True)
    marker = venv_dir() / ".requirements.sha256"
    digest = hashlib.sha256(REQS.read_bytes()).hexdigest()
    if not marker.exists() or marker.read_text().strip() != digest:
        print("[setup] installing backend requirements...", flush=True)
        subprocess.run([str(py), "-m", "pip", "install", "-q", "--disable-pip-version-check",
                        "-r", str(REQS)], check=True)
        marker.write_text(digest)
    if not inside:
        result = subprocess.run([str(py), *sys.argv], cwd=os.getcwd())
        sys.exit(result.returncode)
