"""
WHY THIS EXISTS
Tests for the integrate step's fixes after review B (reviews/review-B.md): the public
tunnel, a stale fake meeting blocking the real bot, and disk-save failures on Windows.
Each test is named after the symptom it reproduces (CLAUDE.md rule 5).
"""
import asyncio
import os

import pytest

from backend.app.contract.events import MeetingEnded
from backend.app.core.bus import EventBus
from backend.app.core.store import Store

TUNNEL = {"host": "among-sanction-browsing.ngrok-free.dev"}
TOKEN = "t" * 64


def test_public_tunnel_can_read_transcripts_settings_and_dev_endpoints(client):
    assert client.get("/api/meetings", headers=TUNNEL).status_code == 404
    assert client.get("/api/settings", headers=TUNNEL).status_code == 404
    assert client.put("/api/settings", headers=TUNNEL, json={}).status_code == 404
    assert client.post("/api/dev/meetings", headers=TUNNEL, json={"title": "x"}).status_code == 404
    assert client.get("/docs", headers=TUNNEL).status_code == 404
    assert client.get("/openapi.json", headers=TUNNEL).status_code == 404
    # a forwarded host counts too, even if Host itself looks local
    fwd = {"host": "localhost:8000", "x-forwarded-host": "among-sanction-browsing.ngrok-free.dev"}
    assert client.get("/api/meetings", headers=fwd).status_code == 404


def test_tunnel_guard_still_lets_recall_webhooks_and_local_dashboard_through(client):
    r = client.post(f"/webhooks/recall/{TOKEN}", headers=TUNNEL, json={"event": "participant_events.join", "data": {}})
    assert r.status_code == 200
    assert client.post(f"/webhooks/recall/{'x' * 64}", headers=TUNNEL, json={"event": "x", "data": {}}).status_code != 200
    for host in ("localhost:8000", "127.0.0.1:8000", "[::1]:8000", "localhost:3000"):
        assert client.get("/api/meetings", headers={"host": host}).status_code == 200, host


def test_sending_the_bot_while_a_meeting_is_live_says_which_meeting(client):
    m = client.post("/api/dev/meetings", json={"title": "Stuck replay"}).json()
    r = client.post("/api/meetings", json={"meeting_url": "https://meet.google.com/abc-defg-hij"})
    assert r.status_code == 409
    assert m["meeting_id"] in r.json()["detail"] and "Stuck replay" in r.json()["detail"]


def test_fake_meeting_stopped_with_ctrl_c_blocks_the_real_bot_after_restart(make_client):
    with make_client() as c1:
        stuck = c1.post("/api/dev/meetings", json={"title": "Stopped midway"}).json()
    with make_client() as c2:  # backend restarted, same data folder
        assert c2.get(f"/api/meetings/{stuck['meeting_id']}").json()["ended_at"] is not None
        r = c2.post("/api/meetings", json={"meeting_url": "https://meet.google.com/abc-defg-hij"})
        assert r.status_code == 503  # no Recall key in tests - but no longer 409


def test_replaying_mid_demo_ends_the_real_meeting_but_leaves_the_bot_in_the_call(client):
    rt = client.app.state.runtime
    real = rt.store.create_meeting(title="Board call", source="recall", meeting_url="https://meet.google.com/x",
                                   recall_bot_id="bot_real")
    r = client.post("/api/dev/meetings", json={"title": "replay"})
    assert r.status_code == 409 and "Board call" in r.json()["detail"]
    assert rt.store.get(real.meeting_id).ended_at is None


def test_windows_file_lock_during_save_silently_drops_chat_posts(tmp_path, monkeypatch):
    store = Store(tmp_path)
    bus = EventBus(store)
    record = store.create_meeting(title="t", source="replay")
    seen = []

    async def subscriber(event):
        seen.append(event.type)
    bus.subscribe("meeting.ended", subscriber)

    def locked(*a, **k):
        raise PermissionError("file in use by another process")
    monkeypatch.setattr(os, "replace", locked)
    monkeypatch.setattr("backend.app.core.store.SAVE_RETRY_SECONDS", 0)
    asyncio.run(bus.publish(record.meeting_id, "meeting.ended", MeetingEnded(reason="dashboard")))
    assert seen == ["meeting.ended"]  # subscribers still ran
    assert store.get(record.meeting_id).ended_at is not None


def test_brief_file_lock_during_save_is_retried_until_it_lands(tmp_path, monkeypatch):
    store = Store(tmp_path)
    record = store.create_meeting(title="t", source="replay")
    real_replace, calls = os.replace, {"n": 0}

    def flaky(src, dst):
        calls["n"] += 1
        if calls["n"] < 3:
            raise PermissionError("locked")
        return real_replace(src, dst)
    monkeypatch.setattr(os, "replace", flaky)
    monkeypatch.setattr("backend.app.core.store.SAVE_RETRY_SECONDS", 0)
    record.title = "renamed"
    store._save(record.meeting_id)
    assert calls["n"] == 3
    assert '"renamed"' in store.path_for(record.meeting_id).read_text(encoding="utf-8")
