"""
WHY THIS EXISTS
One command to get Meet AGI ready for a real meeting:
    python scripts/go.py
It starts the three things a live test needs, checks each one really works, then opens the dashboard:
  1. the backend (http://localhost:8000), using the real AI models;
  2. the public tunnel (ngrok), so the meeting bot's vendor can deliver what it hears to this laptop;
  3. the dashboard (http://localhost:3000), installed and built the first time.
Press Ctrl+C in this window to stop all three.
Options: --offline (canned AI, no vendor calls), --no-tunnel (for the fake meeting only).

FAILURE IT PREVENTS
Three windows, three commands and silent half-starts: a dashboard that "can't be reached" because the
backend never started, or a bot that joins but hears nothing because the tunnel was down. Every step here
is checked, and when a step fails it prints the one thing to do about it.

DEPENDENCIES (CLAUDE.md rule 4): none new. It uses Python's standard library plus httpx (already
installed), the ngrok.exe in tools\\ (downloaded at setup) and Node's npm (installed at setup).
Logs go to data/logs/ (git-ignored) so this window stays readable.
"""
from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _bootstrap import ROOT, ensure_venv  # noqa: E402

ensure_venv()
import httpx  # noqa: E402

sys.path.insert(0, str(ROOT))
from backend.app.settings import load_dotenv  # noqa: E402

LOGS = ROOT / "data" / "logs"
BACKEND = "http://127.0.0.1:8000"
DASHBOARD = "http://localhost:3000"          # what Ray opens in Chrome
DASHBOARD_CHECK = "http://127.0.0.1:3000"    # what this script checks (localhost may mean IPv6 ::1)
children: list[subprocess.Popen] = []


def say(ok: bool | None, text: str) -> None:
    mark = {True: "OK  ", False: "FAIL", None: "... "}[ok]
    print(f"[{mark}] {text}", flush=True)


def stop(code: int = 0) -> None:
    for p in reversed(children):
        if p.poll() is None:
            if os.name == "nt":  # npm runs through a .cmd wrapper; kill the whole tree or node keeps port 3000
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True)
            else:  # each child leads its own process group (see start()), so this also stops npm's node
                try:
                    os.killpg(p.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
    for p in children:
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            p.kill()
    sys.exit(code)


def fail(text: str, fix: str, log: Path | None = None) -> None:
    say(False, text)
    if log and log.exists():
        tail = log.read_text(encoding="utf-8", errors="replace").splitlines()[-15:]
        print("      last lines of " + str(log.relative_to(ROOT)) + ":")
        for line in tail:
            print("        " + line)
    print("      WHAT TO DO: " + fix)
    stop(1)


def start(name: str, cmd: list[str], cwd: Path = ROOT, env: dict | None = None) -> tuple[subprocess.Popen, Path]:
    LOGS.mkdir(parents=True, exist_ok=True)
    log = LOGS / f"{name}.log"
    fh = open(log, "w", encoding="utf-8")
    # Own process group: Ctrl+C in this window must reach only go.py, which first ends the meeting
    # (needs the backend still up) and then stops the children itself.
    group = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt"
             else {"start_new_session": True})
    p = subprocess.Popen(cmd, cwd=cwd, env=env or os.environ.copy(), stdout=fh, stderr=subprocess.STDOUT, **group)
    children.append(p)
    return p, log


def wait_until(check, proc: subprocess.Popen, seconds: float) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if proc.poll() is not None:
            return False
        try:
            if check():
                return True
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    return False


def port_in_use(url: str) -> bool:
    try:
        httpx.get(url, timeout=1)
        return True
    except httpx.HTTPError:
        return False


def run_backend(offline: bool) -> None:
    if port_in_use(BACKEND + "/api/health"):
        fail("Something is already running on port 8000 (probably an old backend window).",
             "Close the other PowerShell window that runs the backend (or press Ctrl+C in it), then run this again.")
    env = os.environ.copy()
    env.pop("OFFLINE", None)
    if offline:
        env["OFFLINE"] = "1"
    say(None, "Starting the backend on http://localhost:8000 ...")
    p, log = start("backend", [sys.executable, "scripts/serve.py"], env=env)
    if not wait_until(lambda: httpx.get(BACKEND + "/api/health", timeout=2).status_code == 200, p, 60):
        fail("The backend did not start.", "Copy the lines above into the Claude chat.", log)
    health = httpx.get(BACKEND + "/api/health", timeout=5).json()
    say(True, "Backend running" + (" (OFFLINE: canned AI)" if health["offline"] else " (real AI models)"))


def _tunnel_reaches_backend(public_url: str) -> bool:
    # The tunnel guard answers every non-webhook path from outside with our own JSON 404, so seeing it
    # proves the request went internet -> ngrok -> this backend.
    try:
        r = httpx.get(public_url + "/api/health", timeout=5, headers={"ngrok-skip-browser-warning": "1"})
    except httpx.HTTPError:
        return False
    return r.status_code == 404 and r.headers.get("content-type", "").startswith("application/json")


def _stop_old_ngrok_on_this_laptop() -> None:
    """ERR_NGROK_334 (seen 27 Sep 2026): an ngrok left running from earlier holds the address."""
    if os.name == "nt":
        subprocess.run(["taskkill", "/IM", "ngrok.exe", "/F"], capture_output=True)
    else:
        subprocess.run(["pkill", "-x", "ngrok"], capture_output=True)
    time.sleep(3)  # ngrok's servers take a moment to release the address


def run_tunnel(public_url: str) -> None:
    exe = ROOT / "tools" / ("ngrok.exe" if os.name == "nt" else "ngrok")
    ngrok = str(exe) if exe.exists() else shutil.which("ngrok")
    if not ngrok:
        fail("ngrok was not found (expected tools\\ngrok.exe).", "Tell Claude 'ngrok missing'.")
    for attempt in (1, 2):
        say(None, f"Starting the public tunnel {public_url} ...")
        p, log = start("tunnel", [ngrok, "http", "8000", f"--url={public_url}", "--log=stdout"])
        if wait_until(lambda: _tunnel_reaches_backend(public_url), p, 40):
            say(True, "Public tunnel works (only the meeting webhook is let through; everything else is blocked)")
            return
        text = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
        if "authtoken" in text.lower() or "ERR_NGROK_4018" in text:
            fail("ngrok is not logged in on this laptop.",
                 "Open https://dashboard.ngrok.com/get-started/your-authtoken, copy the token, then run: "
                 "tools\\ngrok.exe config add-authtoken PASTE_TOKEN_HERE   and run this script again.", log)
        if "ERR_NGROK_334" in text or "already online" in text:
            if _tunnel_reaches_backend(public_url):
                say(True, "Public tunnel already running from an earlier ngrok on this laptop - using it")
                return
            if attempt == 1:
                say(None, "An old ngrok is holding the tunnel address - stopping it and retrying ...")
                _stop_old_ngrok_on_this_laptop()
                continue
            fail("The tunnel address is held by an ngrok running somewhere else (another computer or window).",
                 "Open https://dashboard.ngrok.com/endpoints , find among-sanction-browsing.ngrok-free.dev, "
                 "stop it (or shut the other computer's ngrok), then run this again.", log)
        fail("The public tunnel did not reach the backend.",
             "Check the VPN/internet, then copy the lines above into the Claude chat.", log)


def run_dashboard() -> None:
    npm = shutil.which("npm")
    if not npm:
        fail("npm (Node.js) was not found.", "Tell Claude 'npm missing'.")
    front = ROOT / "frontend"
    if port_in_use(DASHBOARD_CHECK):
        fail("Something is already running on port 3000 (probably an old dashboard window).",
             "Close the other PowerShell window that runs the dashboard (or press Ctrl+C in it), then run this again.")
    if not (front / "node_modules").exists():
        say(None, "Installing the dashboard (first time only, about 1 minute) ...")
        p, log = start("dashboard-install", [npm, "install", "--no-audit", "--no-fund"], cwd=front)
        if p.wait() != 0:
            fail("Installing the dashboard failed.", "Check the VPN/internet, then copy the lines above into the Claude chat.", log)
    # The dashboard forwards its /api calls to the backend. Seen on Ray's laptop 27 Sep 2026:
    # "connect ECONNREFUSED ::1:8000" - Windows resolves "localhost" to the IPv6 address ::1 first,
    # but the backend listens on 127.0.0.1 only. So the dashboard is always pointed at 127.0.0.1,
    # whatever .env says, and rebuilt when that address changes (Next.js fixes it at build time).
    env = {**os.environ, "NEXT_PUBLIC_API_BASE": BACKEND}
    build_id = front / ".next" / "BUILD_ID"
    built_for = front / ".next" / "meetagi-api-base.txt"
    newest_source = max(f.stat().st_mtime for f in (front / "src").rglob("*") if f.is_file())
    stale = (not build_id.exists() or build_id.stat().st_mtime < newest_source
             or not built_for.exists() or built_for.read_text(encoding="utf-8").strip() != BACKEND)
    if stale:
        say(None, "Building the dashboard - only needed the first time and after an update (about 1 minute) ...")
        p, log = start("dashboard-build", [npm, "run", "build"], cwd=front, env=env)
        if p.wait() != 0:
            fail("Building the dashboard failed.", "Copy the lines above into the Claude chat.", log)
        built_for.write_text(BACKEND, encoding="utf-8")
    say(None, "Starting the dashboard on http://localhost:3000 ...")
    p, log = start("dashboard", [npm, "run", "start", "--", "-p", "3000"], cwd=front, env=env)
    if not wait_until(lambda: httpx.get(DASHBOARD_CHECK + "/api/health", timeout=3).status_code == 200, p, 60):
        fail("The dashboard did not start.", "Copy the lines above into the Claude chat.", log)
    say(True, "Dashboard running and talking to the backend")


def end_live_meetings(why: str) -> None:
    """Live test 27 Sep 2026: the window was closed with Ctrl+C without pressing "End meeting", so the
    bot stayed in the Google Meet, still listening and billing, and the meeting never got its summary.
    Ending every unfinished meeting (the bot leaves, the summary is written) on stop - and on start, for
    anything a previous run left open - prevents that."""
    try:
        items = httpx.get(BACKEND + "/api/meetings", timeout=5).json()
        for item in items:
            rec = httpx.get(f"{BACKEND}/api/meetings/{item['meeting_id']}", timeout=5).json()
            if rec.get("ended_at") is None:
                say(None, f'{why}: ending "{rec["title"]}" (the bot leaves, the summary is written) ...')
                httpx.post(f"{BACKEND}/api/meetings/{rec['meeting_id']}/end", timeout=30)
                deadline = time.time() + 30
                while time.time() < deadline:
                    if httpx.get(f"{BACKEND}/api/meetings/{rec['meeting_id']}", timeout=5).json().get("summary"):
                        break
                    time.sleep(1)
    except (httpx.HTTPError, ValueError, KeyError):
        say(False, "Could not end the open meeting. If the bot is still in your Meet, remove it there, "
                   "or run: python scripts/end_all_bots.py")


def main() -> None:
    parser = argparse.ArgumentParser(description="Start Meet AGI for a real meeting.")
    parser.add_argument("--offline", action="store_true", help="canned AI, no vendor calls")
    parser.add_argument("--no-tunnel", action="store_true", help="skip ngrok (fake meeting only)")
    args = parser.parse_args()

    load_dotenv()
    public_url = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
    provider = os.environ.get("BOT_PROVIDER", "recall").strip().lower() or "recall"
    key_name = "ATTENDEE_API_KEY" if provider == "attendee" else "RECALL_API_KEY"
    print("Meet AGI - starting everything. Keep this window open; press Ctrl+C here to stop.\n")
    if not args.no_tunnel:
        if not public_url.startswith("https://"):
            fail("PUBLIC_BASE_URL in .env is not an https address.", "Tell Claude 'PUBLIC_BASE_URL missing'.")
        if not os.environ.get(key_name, "").strip():
            fail(f"{key_name} is empty in .env, so no bot can be sent (BOT_PROVIDER={provider}).",
                 f"Put your {provider} API key into .env as {key_name}=..., save, and run this again.")
        say(True, f"Meeting bot vendor: {provider} (key present)")

    try:
        run_backend(args.offline)
        end_live_meetings("Left open by an earlier run")
        if not args.no_tunnel:
            run_tunnel(public_url)
        run_dashboard()
        print(f"\nREADY. Open {DASHBOARD} (opening it for you now).")
        print("Paste your Google Meet link into 'Send Meet AGI to a meeting' and click Send.")
        print("Logs: data/logs/. Press Ctrl+C in this window to stop everything.\n")
        webbrowser.open(DASHBOARD)
        seen: set[str] = set()
        while True:
            dead = [p for p in children if p.poll() is not None and p.returncode not in (0, None)]
            if dead:
                fail("A part stopped unexpectedly.", "Copy data/logs/*.log lines into the Claude chat, then run this again.")
            try:
                for w in httpx.get(BACKEND + "/api/health", timeout=3).json().get("warnings", []):
                    if w not in seen:
                        seen.add(w)
                        say(False, "Warning from the backend: " + w)
            except httpx.HTTPError:
                pass
            time.sleep(10)
    except KeyboardInterrupt:
        print("\nStopping everything ...")
        end_live_meetings("Stopping")
        stop(0)


if __name__ == "__main__":
    main()
