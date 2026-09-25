"""
The Attendee fallback (DESIGN risk R1): BOT_PROVIDER=attendee sends, hears,
voices and removes the bot through Attendee instead of Recall - against the
fake Attendee (backend/app/integrations/fake_attendee.py) and the synthesized
samples in attendee_fixtures/ (labelled synthesized_from_docs). Never the live
vendor. Failure paths first (CLAUDE.md rule 5), each named after its symptom.
"""
import asyncio
import copy
import json
import time
from pathlib import Path

import pytest

from backend.app.integrations import register as reg
from backend.app.integrations.attendee_client import (
    AUDIO_WARNING, CHAT_WARNING, AttendeeClient, AttendeeVendor, is_attendee_payload, sign,
)
from backend.app.integrations.fake_attendee import FakeAttendee
from backend.tests.conftest import wait_for
from backend.tests.meeting.conftest import FAKE_ATTENDEE_KEY, TOKEN, fixture_body, make_rt

HERE = Path(__file__).resolve().parent / "attendee_fixtures"
MEET_URL = "https://meet.google.com/abc-defg-hij"
SECRET = "c2VjcmV0LWZvci10ZXN0cy1vbmx5LW5vdC1hLXJlYWwtb25l"   # base64 of a test-only string
_n = [0]


def sample(name: str, bot_id: str, **data) -> dict:
    body = json.loads((HERE / name).read_text(encoding="utf-8"))
    assert body["_source"].startswith("synthesized_from_docs")
    body["bot_id"] = bot_id
    body["data"].update(data)
    _n[0] += 1
    body["idempotency_key"] = f"00000000-0000-4000-9000-{_n[0]:012d}"
    return body


def caption(bot_id: str, text: str, speaker="spaces/x/devices/104", name="Tom Walsh", offset_s=0.0, duration_ms=None):
    words = len(text.split())
    return sample("transcript_update.json", bot_id, speaker_uuid=speaker, speaker_name=name,
                  timestamp_ms=int(time.time() * 1000 + offset_s * 1000),
                  duration_ms=duration_ms if duration_ms is not None else 300 * words,
                  transcription={"transcript": text})


@pytest.fixture
def attendee_app(lane_app, lane_env):
    lane_env.setenv("BOT_PROVIDER", "attendee")
    return lane_app


def _launch(client):
    return client.post("/api/meetings", json={"meeting_url": MEET_URL, "title": "Board prep"})


def _hook(client, body, token=TOKEN, headers=None):
    return client.post(f"/webhooks/recall/{token}", json=body, headers=headers or {})


def _record(client, m):
    return client.get(f"/api/meetings/{m['meeting_id']}").json()


def _events(client, meeting_id, event_type):
    rt = client.app.state.runtime
    return [e.payload for e in rt.store.events_since(meeting_id) if e.type == event_type]


def _in_call(client, fake, m):
    fake.set_state(m["recall_bot_id"], "joined_recording", "joined_meeting")
    assert _hook(client, sample("bot_state_change_joined_recording.json", m["recall_bot_id"])).status_code == 200
    wait_for(lambda: _record(client, m)["bot_status"] == "in_call")


# ======================= failures first: the receiver =======================
def test_attendee_webhook_with_wrong_token_is_rejected_and_nothing_is_heard(attendee_app, fake_attendee):
    client, _ = attendee_app()
    with client:
        m = _launch(client).json()
        assert _hook(client, caption(m["recall_bot_id"], "Q3 revenue was rising."), token="x" * 64).status_code == 401
        time.sleep(0.2)
        assert _record(client, m)["segments"] == []
        assert client.get("/api/health").json()["last_webhook_at"] is None


def test_attendee_retrying_the_same_delivery_does_not_double_a_transcript_line(attendee_app):
    client, _ = attendee_app()
    with client:
        m = _launch(client).json()
        body = caption(m["recall_bot_id"], "Q3 revenue was rising.")
        for _ in range(3):
            assert _hook(client, body).status_code == 200
        wait_for(lambda: _record(client, m)["segments"])
        time.sleep(0.3)
        assert [s["text"] for s in _record(client, m)["segments"]] == ["Q3 revenue was rising."]


def test_attendee_caption_resent_after_an_edit_is_not_heard_twice(attendee_app):
    """Attendee saves a final caption again if Meet edits it: same speaker and start, new delivery."""
    client, _ = attendee_app()
    with client:
        m = _launch(client).json()
        first = caption(m["recall_bot_id"], "Q3 revenue was rising.")
        edited = copy.deepcopy(first)
        edited["idempotency_key"] = "00000000-0000-4000-9999-000000000001"
        edited["data"]["transcription"]["transcript"] = "Q3 revenue was rising, I think."
        assert _hook(client, first).status_code == 200 and _hook(client, edited).status_code == 200
        wait_for(lambda: _record(client, m)["segments"])
        time.sleep(0.3)
        assert len(_record(client, m)["segments"]) == 1


def test_attendee_transcript_without_text_is_acknowledged_and_ignored(attendee_app):
    client, _ = attendee_app()
    with client:
        m = _launch(client).json()
        body = sample("transcript_update_no_transcription.json", m["recall_bot_id"])
        assert _hook(client, body).status_code == 200
        broken = sample("transcript_update.json", m["recall_bot_id"], timestamp_ms="not a number")
        assert _hook(client, broken).status_code == 200
        time.sleep(0.3)
        assert _record(client, m)["segments"] == []


def test_attendee_webhook_for_an_unknown_bot_is_acknowledged_so_it_is_not_retried(attendee_app):
    client, _ = attendee_app()
    with client:
        _launch(client)
        assert _hook(client, caption("bot_somebody_else", "Hello.")).status_code == 200


def test_attendee_chat_and_participant_webhooks_do_not_reach_the_engine(attendee_app):
    client, _ = attendee_app()
    with client:
        m = _launch(client).json()
        assert _hook(client, sample("chat_messages_update.json", m["recall_bot_id"])).status_code == 200
        other = sample("chat_messages_update.json", m["recall_bot_id"])
        other["trigger"] = "participant_events.join_leave"
        assert _hook(client, other).status_code == 200
        time.sleep(0.3)
        assert _record(client, m)["segments"] == []


def test_attendee_signature_mismatch_is_logged_not_rejected(attendee_app, lane_env):
    lane_env.setenv("ATTENDEE_WEBHOOK_SECRET", SECRET)
    client, lane = attendee_app()
    with client:
        m = _launch(client).json()
        good = caption(m["recall_bot_id"], "Signed and good.")
        assert _hook(client, good, headers={"X-Webhook-Signature": sign(good, SECRET)}).status_code == 200
        assert lane.receiver.signature_mismatches == 0
        bad = caption(m["recall_bot_id"], "Forged but the token was right.", offset_s=5)
        assert _hook(client, bad, headers={"X-Webhook-Signature": "AAAA"}).status_code == 200
        assert lane.receiver.signature_mismatches == 1
        wait_for(lambda: len(_record(client, m)["segments"]) == 2)


def test_attendee_and_recall_payloads_are_never_confused():
    attendee = json.loads((HERE / "transcript_update.json").read_text())
    assert is_attendee_payload(attendee)
    for name in ("transcript_data.json", "bot_status_call_ended.json", "transcript_partial_data.json",
                 "participant_events_join.json"):
        assert not is_attendee_payload(fixture_body(name, "b1")), name
    assert not is_attendee_payload({"trigger": "x"}) and not is_attendee_payload([1, 2])


def test_recall_webhooks_still_work_when_attendee_is_selected(attendee_app):
    """The receiver slot is shared: the replay posts Recall-shaped webhooks whatever BOT_PROVIDER says."""
    client, lane = attendee_app()
    with client:
        m = client.post("/api/dev/meetings", json={"title": "t"}).json()
        assert _hook(client, fixture_body("transcript_data.json", m["recall_bot_id"])).status_code == 200
        wait_for(lambda: _record(client, m)["segments"])


# ======================= failures first: sending the bot =======================
def test_sending_an_attendee_bot_without_its_key_names_the_setting(attendee_app, lane_env, fake_attendee):
    lane_env.setenv("ATTENDEE_API_KEY", "")
    client, _ = attendee_app()
    with client:
        r = _launch(client)
        assert r.status_code == 503 and "ATTENDEE_API_KEY" in r.json()["detail"]
        assert fake_attendee.calls == []
        assert any("ATTENDEE_API_KEY" in p for p in client.get("/api/health").json()["placeholders"])


def test_attendee_refuses_an_http_webhook_address_so_we_do_not_call_it(attendee_app, lane_env, fake_attendee):
    lane_env.setenv("PUBLIC_BASE_URL", "http://example.invalid")
    client, _ = attendee_app()
    with client:
        r = _launch(client)
        assert r.status_code == 503 and "https://" in r.json()["detail"]
        assert fake_attendee.calls == []


def test_attendee_refusing_the_bot_gives_502_no_meeting_and_no_key(attendee_app, fake_attendee):
    fake_attendee.fail["/api/v1/bots"] = 400
    client, _ = attendee_app()
    with client:
        r = _launch(client)
        assert r.status_code == 502 and "Attendee" in r.json()["detail"] and "400" in r.json()["detail"]
        assert FAKE_ATTENDEE_KEY not in r.text
        assert client.get("/api/meetings").json() == []


def test_offline_never_sends_an_attendee_bot(lane_app, lane_env, fake_attendee):
    lane_env.setenv("BOT_PROVIDER", "attendee")
    lane_env.setenv("OFFLINE", "1")
    lane_env.setenv("RECALL_API_KEY", "")
    client, lane = lane_app()
    with client:
        r = _launch(client)
        assert r.status_code == 503 and "RECALL_API_KEY" in r.json()["detail"] and "OFFLINE=1" in r.json()["detail"]
        assert fake_attendee.calls == [] and lane.provider == "recall"
        assert any("OFFLINE=1" in p for p in client.get("/api/health").json()["placeholders"])


def test_provider_choice_defaults_to_recall():
    assert reg.choose_provider("", offline=False)[0] == "recall"
    assert reg.choose_provider("recall", offline=False)[0] == "recall"
    assert reg.choose_provider(" Attendee ", offline=False)[0] == "attendee"
    assert reg.choose_provider("attendee", offline=True)[0] == "recall"
    assert reg.choose_provider("zoom", offline=False)[0] == "recall"


# ======================= failures first: audio, chat, stop =======================
def test_attendee_refusing_audio_marks_the_answer_failed_warns_and_chat_still_posts(attendee_app, fake_attendee):
    client, _ = attendee_app()
    with client:
        m = _launch(client).json()
        _in_call(client, fake_attendee, m)
        fake_attendee.fail["/output_audio"] = 400
        _hook(client, caption(m["recall_bot_id"], "Hey AGI, what was Q3 revenue?"))
        wait_for(lambda: _record(client, m)["answers"] and _record(client, m)["answers"][0]["status"] == "failed")
        wait_for(lambda: len(fake_attendee.bodies("/send_chat_message")) == 1)
        assert AUDIO_WARNING in client.get("/api/health").json()["warnings"]
        del fake_attendee.fail["/output_audio"]
        _hook(client, caption(m["recall_bot_id"], "Hey AGI, and Q2?", offset_s=30))
        wait_for(lambda: AUDIO_WARNING not in client.get("/api/health").json()["warnings"])


def test_attendee_refusing_chat_marks_the_post_failed_and_warns(attendee_app, fake_attendee):
    client, _ = attendee_app()
    with client:
        m = _launch(client).json()
        _in_call(client, fake_attendee, m)
        fake_attendee.fail["/send_chat_message"] = 400
        _hook(client, caption(m["recall_bot_id"], "Hey AGI, what was Q3 revenue?"))
        wait_for(lambda: any(c["status"] == "failed" for c in _record(client, m)["chat_posts"]))
        assert CHAT_WARNING in client.get("/api/health").json()["warnings"]


def test_emoji_in_a_chat_line_would_make_attendee_reject_it_so_it_is_removed(fake_attendee):
    async def go():
        fake_attendee.bots["b1"] = {"id": "b1", "state": "joined_recording", "events": [], "metadata": {}}
        client = AttendeeClient("k", "https://attendee.invalid", transport=fake_attendee.transport)
        await client.send_chat("b1", "Because you asked: revenue fell 4% \U0001F4C9 per the deck…")
    asyncio.run(go())
    (body,) = fake_attendee.bodies("/send_chat_message")
    assert body == {"to": "everyone", "message": "Because you asked: revenue fell 4%  per the deck…"}


def test_stop_cannot_cut_attendee_audio_but_drops_queued_answers_and_health_says_so(attendee_app, fake_attendee):
    client, lane = attendee_app()
    with client:
        m = _launch(client).json()
        _in_call(client, fake_attendee, m)
        _hook(client, caption(m["recall_bot_id"], "Hey AGI, what was Q3 revenue?"))
        wait_for(lambda: len(fake_attendee.bodies("/output_audio")) >= 1, timeout=10)
        assert client.post(f"/api/meetings/{m['meeting_id']}/stop").status_code == 200
        wait_for(lambda: lane.attendee.stop_requests >= 1)
        assert fake_attendee.paths("DELETE") == []          # there is no stop-audio call to make
        assert lane.attendee.stop_requests >= 1            # but the stop reached the Attendee client
        placeholders = client.get("/api/health").json()["placeholders"]
        assert any("cannot cut a clip already playing" in p for p in placeholders)


def test_attendee_stop_audio_makes_no_vendor_call(fake_attendee):
    client = AttendeeClient("k", "https://attendee.invalid", transport=fake_attendee.transport)
    asyncio.run(client.stop_audio("b1"))
    assert fake_attendee.calls == [] and client.stop_requests == 1


# ======================= failures first: status =======================
def test_attendee_status_check_failing_shows_a_warning_and_keeps_polling(attendee_app, fake_attendee):
    client, _ = attendee_app()
    with client:
        m = _launch(client).json()
        bot = m["recall_bot_id"]
        fake_attendee.fail[f"/bots/{bot}"] = 502
        wait_for(lambda: any("Attendee bot status" in w for w in client.get("/api/health").json()["warnings"]))
        del fake_attendee.fail[f"/bots/{bot}"]
        fake_attendee.set_state(bot, "joined_recording", "joined_meeting")
        wait_for(lambda: _record(client, m)["bot_status"] == "in_call")
        wait_for(lambda: client.get("/api/health").json()["warnings"] == [])


def test_attendee_bot_status_never_updates_without_webhooks_so_it_is_polled(attendee_app, fake_attendee):
    client, lane = attendee_app()
    with client:
        m = _launch(client).json()
        bot, mid = m["recall_bot_id"], m["meeting_id"]
        fake_attendee.set_state(bot, "waiting_room", "put_in_waiting_room")
        wait_for(lambda: _record(client, m)["bot_status"] == "waiting_room")
        fake_attendee.set_state(bot, "joined_recording", "joined_meeting")
        wait_for(lambda: _record(client, m)["bot_status"] == "in_call")
        fake_attendee.set_state(bot, "leaving", "leave_requested")      # no change reported
        fake_attendee.set_state(bot, "post_processing", "meeting_ended")
        record = wait_for(lambda: (r := _record(client, m))["ended_at"] and r)
        assert record["bot_status"] == "left"
        assert [s.status for s in _events(client, mid, "bot.status")] == ["joining", "waiting_room", "in_call", "left"]
        assert [e.reason for e in _events(client, mid, "meeting.ended")] == ["call_ended"]
        wait_for(lambda: lane.bot.watchers[mid].done())


def test_attendee_status_by_webhook_and_by_poll_is_published_once(attendee_app, fake_attendee):
    client, _ = attendee_app()
    with client:
        m = _launch(client).json()
        bot, mid = m["recall_bot_id"], m["meeting_id"]
        _in_call(client, fake_attendee, m)
        time.sleep(0.3)
        fake_attendee.set_state(bot, "post_processing", "meeting_ended")
        body = sample("bot_state_change_meeting_ended.json", bot)
        _hook(client, body)
        _hook(client, body)    # Attendee retry
        wait_for(lambda: _record(client, m)["ended_at"])
        time.sleep(0.3)
        assert [s.status for s in _events(client, mid, "bot.status")] == ["joining", "in_call", "left"]
        assert len(_events(client, mid, "meeting.ended")) == 1


def test_attendee_bot_that_could_not_join_ends_the_meeting_as_failed(attendee_app, fake_attendee):
    client, _ = attendee_app()
    with client:
        m = _launch(client).json()
        _hook(client, sample("bot_state_change_fatal_error.json", m["recall_bot_id"]))
        wait_for(lambda: _record(client, m)["ended_at"])
        last = _events(client, m["meeting_id"], "bot.status")[-1]
        assert last.status == "failed" and last.detail == "meeting_not_started"
        assert [e.reason for e in _events(client, m["meeting_id"], "meeting.ended")] == ["bot_left"]


def test_a_half_sentence_is_not_lost_when_the_attendee_meeting_ends(attendee_app, fake_attendee):
    client, _ = attendee_app()
    with client:
        m = _launch(client).json()
        _hook(client, caption(m["recall_bot_id"], "and the last thing I wanted to say"))
        _hook(client, sample("bot_state_change_meeting_ended.json", m["recall_bot_id"]))
        record = wait_for(lambda: (r := _record(client, m))["ended_at"] and r)
        assert [s["text"] for s in record["segments"]] == ["and the last thing I wanted to say"]


# ======================= happy paths =======================
def test_attendee_bot_is_created_as_meet_agi_with_captions_webhooks_and_consent(attendee_app, fake_attendee):
    client, _ = attendee_app()
    with client:
        r = _launch(client)
        assert r.status_code == 200, r.text
        m = r.json()
        assert m["source"] == "recall" and m["recall_bot_id"] == "bot_fakeAttendee0001" and m["bot_status"] == "joining"
        assert fake_attendee.paths("POST") == ["/api/v1/bots"]
        (payload,) = fake_attendee.bodies("/api/v1/bots")
        assert payload["bot_name"] == "Meet AGI" and payload["meeting_url"] == MEET_URL
        assert payload["webhooks"] == [{"url": f"https://example.invalid/webhooks/recall/{TOKEN}",
                                        "triggers": ["bot.state_change", "transcript.update"]}]
        assert payload["transcription_settings"] == {"meeting_closed_captions": {"google_meet_language": "en-US"}}
        assert payload["metadata"] == {"created_by": "meet-agi"}
        assert payload["bot_chat_message"]["to"] == "everyone" and "Hey AGI" in payload["bot_chat_message"]["message"]
        assert fake_attendee.auth_headers[0] == f"Token {FAKE_ATTENDEE_KEY}"
        assert _launch(client).status_code == 409   # one meeting at a time


def test_dashboard_end_makes_the_attendee_bot_leave(attendee_app, fake_attendee):
    client, _ = attendee_app()
    with client:
        m = _launch(client).json()
        assert client.post(f"/api/meetings/{m['meeting_id']}/end").status_code == 200
        assert f"/api/v1/bots/{m['recall_bot_id']}/leave" in fake_attendee.paths("POST")


def test_end_all_leaves_our_live_attendee_bots_only(tmp_path, monkeypatch):
    monkeypatch.setenv("BOT_PROVIDER", "attendee")
    fake = FakeAttendee()
    rt = make_rt(tmp_path)
    reg.install(rt, reg.MeetingLane(rt, attendee_transport=fake.transport))
    rt.store.create_meeting("live", "recall", MEET_URL, recall_bot_id="bot_live")
    fake.bots["bot_live"] = {"id": "bot_live", "state": "joined_recording", "events": [], "metadata": {"created_by": "meet-agi"}}
    fake.bots["bot_stray"] = {"id": "bot_stray", "state": "waiting_room", "events": [], "metadata": {"created_by": "meet-agi"}}
    fake.bots["bot_other_app"] = {"id": "bot_other_app", "state": "joined_recording", "events": [], "metadata": {"created_by": "someone"}}
    fake.bots["bot_done"] = {"id": "bot_done", "state": "ended", "events": [], "metadata": {"created_by": "meet-agi"}}
    told = asyncio.run(reg.lane_for(rt).bot.end_all())
    assert sorted(told) == ["bot_live", "bot_stray"]
    assert "/api/v1/bots/bot_other_app/leave" not in fake.paths("POST")
    assert any("states=joined_recording" in q for q in fake.queries)


def test_attendee_state_map_covers_every_documented_state():
    documented = ["ready", "joining", "joined_not_recording", "joined_recording", "leaving", "post_processing",
                  "fatal_error", "waiting_room", "ended", "data_deleted", "scheduled", "staged",
                  "joined_recording_paused", "joining_breakout_room", "leaving_breakout_room",
                  "joined_recording_permission_denied"]
    vendor = AttendeeVendor()
    for state in documented:
        status, _, ended = vendor.read_status({"state": state, "events": []})
        assert status in (None, "joining", "waiting_room", "in_call", "left", "failed"), state
        assert (ended is not None) == (status in ("left", "failed")), state


def test_replay_never_reaches_attendee_even_when_it_is_selected(attendee_app, fake_attendee, fake_inworld):
    from backend.app.dev.fake_meeting import load_script, timeline
    client, lane = attendee_app()
    with client:
        script = load_script()
        m = client.post("/api/dev/meetings", json={"title": script["title"]}).json()
        for _, body in timeline(script, m["recall_bot_id"]):
            assert _hook(client, body).status_code == 200
        wait_for(lambda: _record(client, m)["summary"], timeout=10)
        assert fake_attendee.calls == [] and fake_inworld.calls == []
        assert len(_record(client, m)["segments"]) == len(script["lines"])


def test_hey_agi_over_attendee_speaks_the_answer_and_posts_chat(attendee_app, fake_attendee, fake_inworld):
    """End to end: an Attendee-shaped caption 'Hey AGI, what was Q3 revenue?' -> the engine's one door ->
    a spoken answer sent to the fake Attendee output_audio, and a chat line to its send_chat_message."""
    client, lane = attendee_app()
    with client:
        m = _launch(client).json()
        bot = m["recall_bot_id"]
        _in_call(client, fake_attendee, m)
        assert _hook(client, caption(bot, "Hey AGI, what was Q3 revenue?")).status_code == 200
        wait_for(lambda: len(fake_attendee.bodies("/send_chat_message")) == 1, timeout=10)
        wait_for(lambda: (a := _record(client, m)["answers"]) and a[0]["status"] == "played", timeout=10)
        record = _record(client, m)
        (segment,) = record["segments"]
        assert segment["speaker_name"] == "Tom Walsh" and segment["text"] == "Hey AGI, what was Q3 revenue?"
        assert _events(client, m["meeting_id"], "wake")
        audio = fake_attendee.bodies("/output_audio")
        assert len(audio) == 2 and all(a["type"] == "audio/mp3" and a["data"] for a in audio)   # filler + answer
        assert any(c["text"].startswith("CANNED ANSWER") for c in fake_inworld.calls)
        (chat,) = fake_attendee.bodies("/send_chat_message")
        assert chat["to"] == "everyone" and chat["message"].startswith("Because you asked:") and len(chat["message"]) <= 500
        assert [c["status"] for c in record["chat_posts"]][-1] == "sent"
        assert client.get("/api/health").json()["warnings"] == []
