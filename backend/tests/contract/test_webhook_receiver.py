"""Failure paths of the Recall webhook receiver first (CLAUDE.md rule 5)."""
import json
from pathlib import Path

from backend.tests.conftest import TOKEN, wait_for

RECALL = Path(__file__).resolve().parents[3] / "fixtures" / "recall"


def _fixture(name, bot_id):
    body = json.loads((RECALL / name).read_text(encoding="utf-8"))
    body["data"]["bot"]["id"] = bot_id
    return body


def _meeting(client):
    return client.post("/api/dev/meetings", json={"title": "t"}).json()


def test_wrong_token_is_rejected_with_401(client):
    m = _meeting(client)
    r = client.post("/webhooks/recall/wrong", json=_fixture("transcript_data.json", m["recall_bot_id"]))
    assert r.status_code == 401
    assert client.get(f"/api/meetings/{m['meeting_id']}").json()["segments"] == []


def test_unset_token_rejects_every_webhook(make_client):
    with make_client(token="") as client:
        m = _meeting(client)
        r = client.post("/webhooks/recall/", json={})
        assert r.status_code in (401, 404, 405)
        r = client.post("/webhooks/recall/anything", json=_fixture("transcript_data.json", m["recall_bot_id"]))
        assert r.status_code == 401


def test_non_json_body_is_rejected_with_400(client):
    r = client.post(f"/webhooks/recall/{TOKEN}", content=b"not json", headers={"content-type": "application/json"})
    assert r.status_code == 400


def test_webhook_for_unknown_bot_is_acknowledged_but_ignored(client):
    r = client.post(f"/webhooks/recall/{TOKEN}", json=_fixture("transcript_data.json", "bot_nobody"))
    assert r.status_code == 200
    assert all(item["alert_count"] == 0 for item in client.get("/api/meetings").json())


def test_partial_transcripts_are_never_acted_on(client):
    m = _meeting(client)
    r = client.post(f"/webhooks/recall/{TOKEN}", json=_fixture("transcript_partial_data.json", m["recall_bot_id"]))
    assert r.status_code == 200
    client.post(f"/webhooks/recall/{TOKEN}", json=_fixture("bot_status_in_call_recording.json", m["recall_bot_id"]))
    wait_for(lambda: client.get(f"/api/meetings/{m['meeting_id']}").json()["bot_status"] == "in_call")
    assert client.get(f"/api/meetings/{m['meeting_id']}").json()["segments"] == []


def test_duplicate_transcript_webhook_is_counted_once(client):
    m = _meeting(client)
    body = _fixture("transcript_data.json", m["recall_bot_id"])
    for _ in range(3):  # Recall retries every second when we are slow
        assert client.post(f"/webhooks/recall/{TOKEN}", json=body).status_code == 200
    record = wait_for(lambda: (r := client.get(f"/api/meetings/{m['meeting_id']}").json())["segments"] and r)
    assert len(record["segments"]) == 1
    assert record["segments"][0]["speaker_name"] == "Marcus Chen"


def test_call_ended_status_ends_meeting_with_exactly_one_summary(client):
    m = _meeting(client)
    for name in ("bot_status_call_ended.json", "bot_status_done.json"):
        client.post(f"/webhooks/recall/{TOKEN}", json=_fixture(name, m["recall_bot_id"]))
    record = wait_for(lambda: (r := client.get(f"/api/meetings/{m['meeting_id']}").json())["summary"] and r)
    assert record["ended_at"] is not None and record["bot_status"] == "left"
    assert record["summary"]["canned"] is True
