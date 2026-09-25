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
