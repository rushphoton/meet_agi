"""
WHY THIS EXISTS
Command 3 of 3: plays the scripted fake meeting (a 3-minute Q3 revenue
review) through the REAL backend, exactly as Recall.ai would deliver a live
call - bot status webhooks, then one finished transcript line at a time -
while listening to the backend's live event stream and printing every event.
No vendor is called. If no backend is running it starts its own and stops it
at the end.

It exits 0 only if the meeting produced exactly what the script plants:
1 alert, 1 spoken answer, 1 wake, 1 meeting summary. Otherwise exit 1.

Usage:
  python scripts/replay.py                 # 10x speed (about 18 seconds)
  python scripts/replay.py --speed 1       # real time (3 minutes)
  python scripts/replay.py --loop          # repeat until Ctrl+C
  OFFLINE=1 python scripts/replay.py       # canned providers only (PowerShell: $env:OFFLINE=1)

FAILURE IT PREVENTS
"It worked in the tests" while the live path (webhook -> bus -> live stream)
is broken.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _bootstrap import ROOT, ensure_venv  # noqa: E402

ensure_venv()
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from backend.app.dev.fake_meeting import load_script, timeline  # noqa: E402
from backend.app.settings import load_dotenv  # noqa: E402


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _healthy(base: str) -> dict | None:
    try:
        r = httpx.get(f"{base}/api/health", timeout=2)
        return r.json() if r.status_code == 200 else None
    except httpx.HTTPError:
        return None


def _start_backend(token: str) -> tuple[subprocess.Popen, str]:
    port = _free_port()
    env = {**os.environ, "DEV_MODE": "1", "RECALL_WEBHOOK_TOKEN": token}
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.app.main:app", "--host", "127.0.0.1",
         "--port", str(port), "--log-level", "warning"], cwd=ROOT, env=env)
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        if _healthy(base):
            return proc, base
        if proc.poll() is not None:
            sys.exit("Backend failed to start - see the error above.")
        time.sleep(0.2)
    proc.terminate()
    sys.exit("Backend did not become healthy within 20 seconds.")


def _describe(event: dict) -> str:
    p, t = event["payload"], event["type"]
    if t == "transcript.segment":
        return f'{p["speaker_name"]}: {p["text"]}'
    if t == "alert":
        return f'{"[CANNED] " if p["canned"] else ""}{p["kind"]} - {p["topic"]} (confidence {p["confidence"]})'
    if t == "spoken.answer":
        return f'{"[CANNED] " if p["canned"] else ""}{p["status"]}: {p["text"]}'
    if t == "chat.post":
        return f'{p["status"]}: {p["text"]}'
    if t == "meeting.summary":
        return f'{"[CANNED] " if p["canned"] else ""}topics={p["key_topics"]}'
    return json.dumps(p)


def _listen(base: str, meeting_id: str, seen: list, done: threading.Event) -> None:
    with httpx.Client(timeout=None) as client:
        with client.stream("GET", f"{base}/api/meetings/{meeting_id}/events") as resp:
            for line in resp.iter_lines():
                if not line.startswith("data: "):
                    continue
                event = json.loads(line[6:])
                seen.append(event)
                print(f'  [{event["seq"]:>3}] {event["type"]:<19} {_describe(event)}'.encode(
                    "ascii", "replace").decode(), flush=True)
                if event["type"] == "meeting.summary" or done.is_set():
                    return


def run_once(base: str, token: str, speed: float) -> bool:
    script = load_script()
    meeting = httpx.post(f"{base}/api/dev/meetings", json={"title": script["title"]}, timeout=10)
    if meeting.status_code == 404:
        sys.exit("The running backend has dev endpoints off. Stop it, or start it with scripts/serve.py.")
    meeting.raise_for_status()
    meeting = meeting.json()
    print(f'Fake meeting {meeting["meeting_id"]} "{script["title"]}" at {speed}x speed')

    seen: list = []
    done = threading.Event()
    listener = threading.Thread(target=_listen, args=(base, meeting["meeting_id"], seen, done), daemon=True)
    listener.start()
    time.sleep(0.5)  # let the stream connect (it would replay history anyway)

    previous = 0.0
    with httpx.Client(timeout=10) as client:
        for at, body in timeline(script, meeting["recall_bot_id"]):
            time.sleep(max(0.0, (at - previous) / speed))
            previous = at
            r = client.post(f"{base}/webhooks/recall/{token}", json=body)
            if r.status_code != 200:
                sys.exit(f"Webhook rejected with HTTP {r.status_code}: {r.text}")

    listener.join(timeout=15)
    done.set()
    counts = {t: sum(e["type"] == t for e in seen) for t in ("wake", "meeting.summary", "transcript.segment")}
    # The meeting lane publishes progress (playing/played, sent) as new events with the same id,
    # so answers are counted by distinct answer_id, and alerts only if they passed the gate
    # (held-back alerts are dashboard-only by design).
    counts["spoken.answer"] = len({e["payload"]["answer_id"] for e in seen if e["type"] == "spoken.answer"})
    counts["alert"] = len({e["payload"]["alert_id"] for e in seen if e["type"] == "alert" and not e["payload"]["gated"]})
    expected = script["expected"]
    ok = (counts["alert"] == expected["alerts"] and counts["spoken.answer"] == expected["spoken_answers"]
          and counts["meeting.summary"] == expected["meeting_summaries"] and counts["wake"] == expected["wake_events"]
          and counts["transcript.segment"] == len(script["lines"]))
    print(f'Result: {counts["transcript.segment"]} transcript lines, {counts["alert"]} alert, '
          f'{counts["spoken.answer"]} spoken answer, {counts["wake"]} wake, {counts["meeting.summary"]} summary '
          f'-> {"REPLAY OK" if ok else "REPLAY MISMATCH (expected " + json.dumps(expected) + ")"}')
    print(f'Saved: data/meetings/{meeting["meeting_id"]}.json')
    return ok


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay the fake meeting through the real backend.")
    parser.add_argument("--speed", type=float, default=10.0, help="1 = real time; default 10")
    parser.add_argument("--loop", action="store_true", help="repeat until Ctrl+C")
    parser.add_argument("--base-url", default=None, help="use an already-running backend")
    args = parser.parse_args()

    load_dotenv()
    if os.environ.get("OFFLINE", "") in {"1", "true", "yes"}:
        print("OFFLINE=1: canned providers only (milestone 0 uses canned providers in every mode).")
    base = args.base_url or os.environ.get("NEXT_PUBLIC_API_BASE", "http://localhost:8000")
    token = os.environ.get("RECALL_WEBHOOK_TOKEN", "")
    proc = None
    if not _healthy(base) or not token:
        token = token or secrets.token_hex(32)
        print("No backend running - starting a private one for this replay...")
        proc, base = _start_backend(token)
    try:
        while True:
            ok = run_once(base, token, args.speed)
            if not args.loop:
                sys.exit(0 if ok else 1)
    except KeyboardInterrupt:
        print("Stopped.")
    finally:
        if proc:
            proc.terminate()
            proc.wait(timeout=10)


if __name__ == "__main__":
    main()
