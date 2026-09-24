"""
The real-time transcript receiver, through the real web route, against the
Recall samples in fixtures/recall/. Failure paths first (CLAUDE.md rule 5).
"""
import time

from backend.app.contract.records import Settings, SpeakerMapping
from backend.app.pipeline import entry as engine_entry
from backend.tests.conftest import wait_for
from backend.tests.meeting.conftest import TOKEN, fixture_body, transcript_body


def _meeting(client):
    return client.post("/api/dev/meetings", json={"title": "t"}).json()


def _record(client, m):
    return client.get(f"/api/meetings/{m['meeting_id']}").json()


def _hook(client, body, token=TOKEN, headers=None):
    return client.post(f"/webhooks/recall/{token}", json=body, headers=headers or {})


def test_wrong_token_is_rejected_and_nothing_is_recorded(lane_app):
    client, _ = lane_app()
    with client:
        m = _meeting(client)
        assert _hook(client, fixture_body("transcript_data.json", m["recall_bot_id"]), token="x" * 64).status_code == 401
        assert _hook(client, fixture_body("transcript_data.json", m["recall_bot_id"]), token=TOKEN[:-1]).status_code == 401
        time.sleep(0.2)
        assert _record(client, m)["segments"] == []


def test_unset_token_locks_the_receiver(lane_app, lane_env):
    lane_env.setenv("RECALL_WEBHOOK_TOKEN", "")
    client, _ = lane_app()
    with client:
        m = _meeting(client)
        assert _hook(client, fixture_body("transcript_data.json", m["recall_bot_id"]), token="anything").status_code == 401


def test_partial_and_participant_events_are_acknowledged_but_ignored(lane_app):
    client, _ = lane_app()
    with client:
        m = _meeting(client)
        for name in ("transcript_partial_data.json", "participant_events_join.json"):
            assert _hook(client, fixture_body(name, m["recall_bot_id"])).status_code == 200
        _hook(client, fixture_body("bot_status_in_call_recording.json", m["recall_bot_id"]))
        wait_for(lambda: _record(client, m)["bot_status"] == "in_call")
        assert _record(client, m)["segments"] == []


def test_malformed_payloads_are_acknowledged_and_do_not_crash(lane_app):
    client, _ = lane_app()
    with client:
        m = _meeting(client)
        bot = m["recall_bot_id"]
        bodies = [
            {"event": "transcript.data", "data": "not an object"},
            {"event": "transcript.data", "data": {"bot": {"id": bot}, "data": {"words": [], "participant": {"id": 1}}}},
            {"event": "transcript.data", "data": {"bot": {"id": bot}, "data": {"words": ["x"], "participant": {}}}},
            {"event": "transcript.data", "data": {"bot": "not an object"}},
            {"event": "something.new", "data": {"bot": {"id": bot}}},
            [],
        ]
        for body in bodies:
            assert _hook(client, body).status_code == 200
        assert _hook(client, transcript_body(bot, 7, "Dana Lee", "Still alive.", 1.0)).status_code == 200
        record = wait_for(lambda: (r := _record(client, m))["segments"] and r)
        assert [s["text"] for s in record["segments"]] == ["Still alive."]


def test_duplicate_transcript_webhook_is_counted_once(lane_app):
    client, _ = lane_app()
    with client:
        m = _meeting(client)
        body = fixture_body("transcript_data.json", m["recall_bot_id"])
        for _ in range(3):
            assert _hook(client, body).status_code == 200
        record = wait_for(lambda: (r := _record(client, m))["segments"] and r)
        time.sleep(0.2)
        assert len(_record(client, m)["segments"]) == 1
        assert record["segments"][0]["speaker_name"] == "Marcus Chen"


def test_webhook_is_answered_fast_even_when_the_engine_is_slow(lane_app, monkeypatch):
    import asyncio

    async def slow_engine(segment, ctx):
        await asyncio.sleep(1.5)
    monkeypatch.setattr(engine_entry, "process_segment", slow_engine)
    client, _ = lane_app()
    with client:
        m = _meeting(client)
        started = time.monotonic()
        for i in range(3):
            assert _hook(client, transcript_body(m["recall_bot_id"], 1, "Dana", f"Line number {i}.", i * 5.0)).status_code == 200
        assert time.monotonic() - started < 1.0


def test_signature_mismatch_is_logged_but_valid_token_still_accepted(lane_app, lane_env):
    lane_env.setenv("RECALL_WORKSPACE_SECRET", "whsec_dGVzdA==")
    client, lane = lane_app()
    with client:
        m = _meeting(client)
        r = _hook(client, transcript_body(m["recall_bot_id"], 1, "Dana", "Hello there.", 0),
                  headers={"webhook-id": "x", "webhook-timestamp": "1", "webhook-signature": "v1,bad"})
        assert r.status_code == 200
        assert lane.receiver.signature_mismatches == 1
        health = client.get("/api/health").json()
        assert any("signature" in p.lower() for p in health["placeholders"])


def test_fragments_are_joined_and_every_sentence_reaches_the_engine_once(lane_app, monkeypatch):
    seen = []

    async def recording_engine(segment, ctx):
        seen.append((segment.speaker_name, segment.text, ctx.meeting_id))
    monkeypatch.setattr(engine_entry, "process_segment", recording_engine)
    client, _ = lane_app()
    with client:
        m = _meeting(client)
        bot = m["recall_bot_id"]
        _hook(client, transcript_body(bot, 1, "Dana Lee", "Q3 revenue was", 10.0))
        _hook(client, transcript_body(bot, 1, "Dana Lee", "forty one million.", 11.0))
        _hook(client, transcript_body(bot, 2, "Marcus Chen", "Are you sure?", 13.0))
        wait_for(lambda: len(seen) == 2)
        assert seen == [("Dana Lee", "Q3 revenue was forty one million.", m["meeting_id"]),
                        ("Marcus Chen", "Are you sure?", m["meeting_id"])]
        segs = _record(client, m)["segments"]
        assert [s["t_start"] for s in segs] == [10.0, 13.0] and all(s["source"] == "replay" for s in segs)


def test_unfinished_sentence_is_handed_on_after_silence(lane_app):
    client, lane = lane_app()
    with client:
        m = _meeting(client)
        _hook(client, transcript_body(m["recall_bot_id"], 1, "Dana Lee", "and then we", 10.0))
        record = wait_for(lambda: (r := _record(client, m))["segments"] and r, timeout=5)
        assert record["segments"][0]["text"] == "and then we"


def test_call_ended_hands_on_the_last_half_sentence_before_the_meeting_ends(lane_app):
    client, lane = lane_app()
    lane_flush_never = 3600
    with client:
        lane.receiver.flush_after = lane_flush_never
        m = _meeting(client)
        _hook(client, transcript_body(m["recall_bot_id"], 1, "Dana Lee", "so to wrap up", 10.0))
        _hook(client, fixture_body("bot_status_call_ended.json", m["recall_bot_id"]))
        record = wait_for(lambda: (r := _record(client, m))["summary"] and r)
        assert [s["text"] for s in record["segments"]] == ["so to wrap up"]
        events = client.get(f"/api/meetings/{m['meeting_id']}")
        assert events.status_code == 200 and record["ended_at"] is not None


def test_speaker_names_come_from_settings_then_recall_then_unknown(lane_app):
    client, _ = lane_app()
    with client:
        s = Settings(speakers=[SpeakerMapping(match_name="marcus chen", display_name="Marcus (Sales)")])
        assert client.put("/api/settings", json=s.model_dump(mode="json")).status_code == 200
        m = _meeting(client)
        bot = m["recall_bot_id"]
        _hook(client, transcript_body(bot, 102, "Marcus Chen", "Mapped name.", 1.0))
        _hook(client, transcript_body(bot, 101, "Dana Lee", "Recall name.", 3.0))
        _hook(client, transcript_body(bot, 999, "", "No name.", 5.0))
        record = wait_for(lambda: len((r := _record(client, m))["segments"]) == 3 and r)
        assert [s["speaker_name"] for s in record["segments"]] == ["Marcus (Sales)", "Dana Lee", "Unknown speaker"]
