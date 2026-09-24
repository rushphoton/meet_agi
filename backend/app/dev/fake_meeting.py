"""
WHY THIS EXISTS
Turns the scripted fake meeting (fixtures/fake_meeting/script.json) into the
exact webhook bodies Recall.ai would send during a live call, using the
Recall samples in fixtures/recall/ as templates. The replay script and the
tests both use it, so the fake meeting always travels the real receiver path.

FAILURE IT PREVENTS
Testing against a shortcut that skips the receiver, so a bug in how live
webhooks are parsed only shows up in front of an audience.

Nothing here calls a vendor.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = ROOT / "fixtures" / "fake_meeting" / "script.json"
RECALL_DIR = ROOT / "fixtures" / "recall"
SECONDS_PER_WORD = 0.35


def load_script(path: Path = SCRIPT_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _template(name: str) -> dict:
    return json.loads((RECALL_DIR / name).read_text(encoding="utf-8"))


def transcript_payload(line: dict, speakers: dict[int, str], bot_id: str) -> dict:
    body = _template("transcript_data.json")
    words, t = [], float(line["t"])
    for text in line["text"].split():
        words.append({"text": text, "start_timestamp": {"relative": round(t, 2)},
                      "end_timestamp": {"relative": round(t + SECONDS_PER_WORD, 2)}})
        t += SECONDS_PER_WORD
    body["data"]["data"]["words"] = words
    participant = body["data"]["data"]["participant"]
    participant["id"] = line["participant_id"]
    participant["name"] = speakers[line["participant_id"]]
    body["data"]["bot"]["id"] = bot_id
    return body


def status_payload(status: str, bot_id: str) -> dict:
    body = copy.deepcopy(_template(f"bot_status_{status}.json"))
    body["data"]["bot"]["id"] = bot_id
    return body


def timeline(script: dict, bot_id: str) -> list[tuple[float, dict]]:
    """[(seconds_from_start, webhook_body), ...] for the whole fake meeting, in order."""
    speakers = {s["participant_id"]: s["name"] for s in script["speakers"]}
    items = [(0.0, status_payload("joining_call", bot_id)),
             (0.0, status_payload("in_call_recording", bot_id))]
    items += [(float(line["t"]), transcript_payload(line, speakers, bot_id)) for line in script["lines"]]
    end = float(script.get("duration_seconds", script["lines"][-1]["t"] + 5))
    items += [(end, status_payload("call_ended", bot_id))]
    return items
