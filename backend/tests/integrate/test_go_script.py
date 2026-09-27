"""
WHY THIS EXISTS
scripts/go.py is Ray's one command for a real meeting. These tests check that it refuses to start,
with a plain instruction, when .env can't work - instead of half-starting and leaving a dashboard
that "can't be reached" or a bot that hears nothing.
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def run_go(**env_overrides):
    env = {**os.environ, **env_overrides}  # values set here win over .env
    return subprocess.run([sys.executable, "scripts/go.py"], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=60)


def test_go_with_a_non_https_public_url_says_so_instead_of_starting_a_dead_tunnel():
    r = run_go(PUBLIC_BASE_URL="http://example.com", BOT_PROVIDER="attendee", ATTENDEE_API_KEY="x")
    assert r.returncode == 1 and "not an https address" in r.stdout


def test_go_without_the_bot_vendor_key_names_the_missing_key():
    r = run_go(PUBLIC_BASE_URL="https://example.com", BOT_PROVIDER="attendee", ATTENDEE_API_KEY="")
    assert r.returncode == 1 and "ATTENDEE_API_KEY is empty" in r.stdout


def test_go_when_an_old_ngrok_holds_the_tunnel_address_stops_it_retries_then_says_where_to_look(tmp_path):
    # Seen on Ray's laptop 27 Sep 2026: "The endpoint ... is already online ... ERR_NGROK_334".
    fake = tmp_path / "ngrok"
    fake.write_text("#!/bin/sh\necho 'ERROR:  The endpoint is already online. ERR_NGROK_334'\nexit 1\n")
    fake.chmod(0o755)
    r = run_go(PUBLIC_BASE_URL="https://127.0.0.1:9", BOT_PROVIDER="attendee", ATTENDEE_API_KEY="x",
               PATH=f"{tmp_path}{os.pathsep}{os.environ['PATH']}", OFFLINE="1")
    assert r.returncode == 1, r.stdout
    assert "stopping it and retrying" in r.stdout
    assert "dashboard.ngrok.com/endpoints" in r.stdout


def test_dashboard_forwarding_to_localhost_is_refused_on_windows_because_localhost_means_ipv6():
    # Seen on Ray's laptop 27 Sep 2026: "connect ECONNREFUSED ::1:8000". The backend listens on
    # 127.0.0.1 only, so go.py must point the dashboard at 127.0.0.1 (never "localhost") for both the
    # build and the start, and check the dashboard at 127.0.0.1 too.
    src = (ROOT / "scripts" / "go.py").read_text(encoding="utf-8")
    assert 'BACKEND = "http://127.0.0.1:8000"' in src
    assert '"NEXT_PUBLIC_API_BASE": BACKEND' in src
    assert src.count("cwd=front, env=env") == 2          # build and start both get it
    assert 'DASHBOARD_CHECK = "http://127.0.0.1:3000"' in src
    assert "meetagi-api-base.txt" in src                  # rebuilt when the address changes


def test_ctrl_c_without_pressing_end_meeting_left_the_bot_in_the_call(tmp_path):
    # Live test 27 Sep 2026: the window was stopped with Ctrl+C, the bot stayed in the Google Meet
    # (still listening and billing) and the meeting never got its summary. Ctrl+C must end it first.
    import json
    import signal
    import time

    import httpx
    import pytest
    try:
        httpx.get("http://127.0.0.1:8000/api/health", timeout=1)
        pytest.skip("port 8000 is busy (a backend is already running)")
    except httpx.HTTPError:
        pass
    out = tmp_path / "go.out"
    env = {**os.environ, "OFFLINE": "1"}
    p = subprocess.Popen([sys.executable, "scripts/go.py", "--no-tunnel", "--offline"], cwd=ROOT, env=env,
                         stdout=open(out, "w"), stderr=subprocess.STDOUT,
                         preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_DFL))
    try:
        for _ in range(240):
            if "READY" in out.read_text() or p.poll() is not None:
                break
            time.sleep(1)
        assert "READY" in out.read_text(), out.read_text()[-2000:]
        mid = httpx.post("http://127.0.0.1:8000/api/dev/meetings", json={"title": "left open"}).json()["meeting_id"]
        p.send_signal(signal.SIGINT)                       # what Ctrl+C does
        p.wait(timeout=90)
    finally:
        if p.poll() is None:
            p.kill()
    record = json.loads((ROOT / "data" / "meetings" / f"{mid}.json").read_text(encoding="utf-8"))["record"]
    assert record["ended_at"] is not None and record["summary"] is not None
    assert "ending \"left open\"" in out.read_text()
