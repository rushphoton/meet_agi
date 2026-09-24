"""
WHY THIS EXISTS
Claude Code runs this script automatically at two moments:

1. When a session starts ("session-start"): it prints STATE.md so the new
   session begins already knowing where the project stands, and it records
   which tests pass right now (the "baseline").
2. When Claude tries to finish a turn ("stop"): it re-runs the tests. If a
   test that was passing at the start is now failing, it refuses to let the
   turn end and tells Claude which tests it broke - up to 8 times in a row,
   after which it lets the turn end with a warning so it can never loop
   forever.

FAILURE IT PREVENTS
An AI session "finishing" while quietly leaving previously working tests
broken - so the next person (or session) inherits a red build without
knowing why. If there is no test suite yet, it does nothing.

DEPENDENCIES (CLAUDE.md rule 4)
None beyond Python's standard library. Cost: zero. It runs the project's own
test commands (pytest for backend/, `npm test` for frontend/) only when those
suites exist. Re-running the full suite on every turn costs time; if the
suite grows past a few minutes, that future condition justifies switching to
running only the tests touched by the turn.
"""
import json
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

MAX_PUSHBACKS = 8
ROOT = Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path(__file__).resolve().parents[2])
STATE_DIR = ROOT / ".claude" / "hook_state"
BASELINE = STATE_DIR / "baseline.json"
COUNTER = STATE_DIR / "pushbacks.txt"
SKIP_DIRS = {".git", ".claude", "node_modules", ".venv", "venv", "frontend", ".next"}


def has_python_tests():
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        if any(f.startswith("test_") and f.endswith(".py") or f.endswith("_test.py") for f in filenames):
            return True
    return False


def python_exe():
    for rel in ("backend/.venv/Scripts/python.exe", "backend/.venv/bin/python", ".venv/Scripts/python.exe", ".venv/bin/python"):
        if (ROOT / rel).exists():
            return str(ROOT / rel)
    return sys.executable


def run_python_tests():
    """Returns {test_id: True/False}. A suite that cannot run at all counts as one failing test."""
    with tempfile.TemporaryDirectory() as tmp:
        xml_path = Path(tmp) / "junit.xml"
        cmd = [python_exe(), "-m", "pytest", "-q", "-p", "no:cacheprovider", f"--junitxml={xml_path}"]
        cmd += [f"--ignore={d}" for d in (".claude", "frontend", "node_modules")]
        try:
            subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=540)
        except subprocess.TimeoutExpired:
            return {"python-suite::completes-in-time": False}
        if not xml_path.exists():
            return {"python-suite::runs": False}
        results = {"python-suite::runs": True}
        for case in ET.parse(xml_path).iter("testcase"):
            test_id = f"{case.get('classname')}::{case.get('name')}"
            failed = any(child.tag in ("failure", "error") for child in case)
            skipped = any(child.tag == "skipped" for child in case)
            if not skipped:
                results[test_id] = not failed
        return results


def frontend_test_script():
    pkg = ROOT / "frontend" / "package.json"
    if not pkg.exists():
        return None
    try:
        script = json.loads(pkg.read_text(encoding="utf-8")).get("scripts", {}).get("test")
    except ValueError:
        return None
    if not script or "no test specified" in script:
        return None
    return script


def run_frontend_tests():
    try:
        proc = subprocess.run("npm test --silent", cwd=ROOT / "frontend", shell=True,
                              capture_output=True, text=True, timeout=540,
                              env={**os.environ, "CI": "true"})
        return {"frontend::npm test": proc.returncode == 0}
    except subprocess.TimeoutExpired:
        return {"frontend::npm test completes-in-time": False}


def run_suites():
    """None means: no test suite exists yet."""
    results, found = {}, False
    if has_python_tests():
        found = True
        results.update(run_python_tests())
    if frontend_test_script():
        found = True
        results.update(run_frontend_tests())
    return results if found else None


def save(path, text):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def session_start():
    state = ROOT / "STATE.md"
    if state.exists():
        print("=== STATE.md (loaded automatically at session start) ===")
        print(state.read_text(encoding="utf-8"))
    results = run_suites()
    if results is not None:
        save(BASELINE, json.dumps(results, indent=1))
    save(COUNTER, "0")


def stop():
    try:
        json.load(sys.stdin)  # hook input; not needed, read so the pipe drains
    except Exception:
        pass
    results = run_suites()
    if results is None:
        return  # no suite yet: pass
    if not BASELINE.exists():
        save(BASELINE, json.dumps(results, indent=1))
        return
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    broken = sorted(t for t, ok in baseline.items() if ok and results.get(t) is False)
    if not broken:
        save(BASELINE, json.dumps(results, indent=1))
        save(COUNTER, "0")
        return
    count = int(COUNTER.read_text() or 0) + 1 if COUNTER.exists() else 1
    if count > MAX_PUSHBACKS:
        save(COUNTER, "0")
        save(BASELINE, json.dumps(results, indent=1))  # the turn ends red, so red is the next turn's starting point
        print(json.dumps({"systemMessage": f"test_guard: gave up after {MAX_PUSHBACKS} pushbacks; "
                                           f"{len(broken)} previously green test(s) are still red: "
                                           + ", ".join(broken[:10])}))
        return
    save(COUNTER, str(count))
    print(json.dumps({"decision": "block", "reason":
        f"test_guard ({count}/{MAX_PUSHBACKS}): these tests were green at the start of the turn and are now red - "
        "fix them before finishing: " + ", ".join(broken[:20])}))


if __name__ == "__main__":
    {"session-start": session_start, "stop": stop}[sys.argv[1]]()
