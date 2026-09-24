"""
Bot lifecycle (create "Meet AGI", status, leave, end-all), the lane's
registration on the bus, and the whole fake meeting end to end - all against
the fake Recall and fake Inworld. Failure paths first (CLAUDE.md rule 5).
"""
import asyncio
import base64
import time

from backend.app.integrations import register as reg
from backend.app.integrations.bot import BotLifecycle, latest_status
from backend.app.integrations.chat import ChatPoster
from backend.app.integrations.fake_recall import FakeRecall
from backend.app.integrations.recall_client import RecallClient
from backend.app.dev.fake_meeting import load_script, timeline
from backend.app.providers.voice.mp3 import mp3_duration
from backend.app.speech.audio_out import AudioOut
from backend.tests.conftest import wait_for
from backend.tests.meeting.conftest import (
    FAKE_RECALL_KEY, TOKEN, fixture_body, make_rt, transcript_body,
)

MEET_URL = "https://meet.google.com/abc-defg-hij"


def _launch(client, title="Board prep"):
    return client.post("/api/meetings", json={"meeting_url": MEET_URL, "title": title})


# ================= bot lifecycle: failures first =================
def test_sending_a_bot_without_recall_key_says_which_setting_is_missing(lane_app, lane_env, fake_recall):
    lane_env.setenv("RECALL_API_KEY", "")
    client, _ = lane_app()
    with client:
        r = _launch(client)
        assert r.status_code == 503 and "RECALL_API_KEY" in r.json()["detail"]
        assert fake_recall.calls == []
        assert client.get("/api/meetings").json() == []
        assert any("RECALL_API_KEY" in p for p in client.get("/api/health").json()["placeholders"])


def test_sending_a_bot_without_public_url_is_refused_without_calling_recall(lane_app, lane_env, fake_recall):
    lane_env.setenv("PUBLIC_BASE_URL", "")
    client, _ = lane_app()
    with client:
        r = _launch(client)
        assert r.status_code == 503 and "PUBLIC_BASE_URL" in r.json()["detail"]
        assert fake_recall.calls == []


def test_recall_refusing_the_bot_gives_502_and_no_meeting(lane_app, fake_recall):
    fake_recall.fail["/bot/"] = 400
    client, _ = lane_app()
    with client:
        r = _launch(client)
        assert r.status_code == 502 and "400" in r.json()["detail"]
        assert FAKE_RECALL_KEY not in r.text
        assert client.get("/api/meetings").json() == []


def test_leave_failure_does_not_stop_the_meeting_ending(lane_app, fake_recall):
    client, _ = lane_app()
    with client:
        m = _launch(client).json()
        fake_recall.fail["/leave_call/"] = 500
        r = client.post(f"/api/meetings/{m['meeting_id']}/end")
        assert r.status_code == 200 and r.json()["ended_at"] is not None


# ================= bot lifecycle: happy paths =================
def test_bot_is_created_as_meet_agi_with_finalized_transcripts_audio_and_consent(lane_app, fake_recall):
    client, _ = lane_app()
    with client:
        r = _launch(client)
        assert r.status_code == 200, r.text
        m = r.json()
        assert m["source"] == "recall" and m["recall_bot_id"] == "bot_fake_0001" and m["bot_status"] == "joining"
        (payload,) = fake_recall.bodies("/api/v1/bot/")
        assert payload["bot_name"] == "Meet AGI" and payload["meeting_url"] == MEET_URL
        rc = payload["recording_config"]
        assert rc["realtime_endpoints"] == [{"type": "webhook", "url": f"https://example.invalid/webhooks/recall/{TOKEN}",
                                              "events": ["transcript.data"]}]
        assert rc["transcript"]["diarization"]["use_separate_streams_when_available"] is True
        assert "recallai_streaming" in rc["transcript"]["provider"]
        clip = base64.b64decode(payload["automatic_audio_output"]["in_call_recording"]["data"]["b64_data"])
        assert 0.3 < mp3_duration(clip) < 1.0
        assert payload["chat"]["on_bot_join"]["send_to"] == "everyone"
        assert "Hey AGI" in payload["chat"]["on_bot_join"]["message"]
        assert fake_recall.auth_headers[0] == f"Token {FAKE_RECALL_KEY}"
        assert _launch(client).status_code == 409   # one meeting at a time


def test_dashboard_end_makes_the_bot_leave(lane_app, fake_recall):
    client, _ = lane_app()
    with client:
        m = _launch(client).json()
        client.post(f"/api/meetings/{m['meeting_id']}/end")
        assert "/api/v1/bot/bot_fake_0001/leave_call/" in fake_recall.paths("POST")


def test_status_maps_recall_codes_and_survives_recall_errors(tmp_path):
    async def go():
        recall = FakeRecall()
        rt = make_rt(tmp_path)
        bot = BotLifecycle(rt, RecallClient("k", transport=recall.transport), api_key_set=True)
        recall.bots["b1"] = {"id": "b1", "status_changes": [{"code": "joining_call"}, {"code": "in_waiting_room"}]}
        assert await bot.status("b1") == "waiting_room"
        assert await bot.status("missing") is None
        assert latest_status({"status_changes": [{"code": "fatal"}]}) == "failed"
    asyncio.run(go())


def test_end_all_leaves_live_bots_and_stray_meet_agi_bots_only(tmp_path):
    async def go():
        recall = FakeRecall()
        rt = make_rt(tmp_path)
        bot = BotLifecycle(rt, RecallClient("k", transport=recall.transport), api_key_set=True)
        live = rt.store.create_meeting("live", "recall", MEET_URL, recall_bot_id="b_live")
        done = rt.store.create_meeting("done", "recall", MEET_URL, recall_bot_id="b_done")
        from backend.app.contract.events import MeetingEnded
        await rt.bus.publish(done.meeting_id, "meeting.ended", MeetingEnded(reason="call_ended"))
        rt.store.create_meeting("replay", "replay", recall_bot_id="replay-bot_x")
        recall.bots.update({
            "b_live": {"id": "b_live", "bot_name": "Meet AGI", "status_changes": [{"code": "in_call_recording"}]},
            "b_stray": {"id": "b_stray", "bot_name": "Meet AGI", "status_changes": [{"code": "in_call_recording"}]},
            "b_other": {"id": "b_other", "bot_name": "Someone else", "status_changes": [{"code": "in_call_recording"}]},
            "b_gone": {"id": "b_gone", "bot_name": "Meet AGI", "status_changes": [{"code": "done"}]},
        })
        told = await bot.end_all()
        assert sorted(told) == ["b_live", "b_stray"]
        assert rt.store.get(live.meeting_id).ended_at is not None
    asyncio.run(go())


# ================= wiring =================
def test_queue_and_chat_poster_are_subscribed_on_the_bus(lane_app):
    client, lane = lane_app()
    with client:
        rt = client.app.state.runtime
        subs = rt.bus._subs

        def owners(event_type):
            return {type(getattr(h, "__self__", None)) for h in subs[event_type]}
        assert AudioOut in owners("spoken.answer")
        assert AudioOut in owners("stop")
        assert AudioOut in owners("wake") and AudioOut in owners("mute")
        assert ChatPoster in owners("chat.post")
        assert rt.recall_webhook is lane.receiver and rt.launch_bot == lane.bot.launch and rt.end_bot == lane.bot.leave


def test_plain_register_fills_every_slot_and_subscribes(tmp_path, monkeypatch):
    monkeypatch.setenv("RECALL_API_KEY", "")
    rt = make_rt(tmp_path, offline=True)
    reg.register(rt)
    assert rt.recall_webhook and rt.launch_bot and rt.end_bot
    for event_type in ("spoken.answer", "chat.post", "stop", "wake", "mute"):
        assert rt.bus._subs[event_type], event_type
    assert any("CANNED" in p for p in rt.placeholders)


# ================= end to end =================
def _post_timeline(client, bot_id, items):
    for _, body in items:
        assert client.post(f"/webhooks/recall/{TOKEN}", json=body).status_code == 200


def test_fake_meeting_end_to_end_delivers_one_answer_and_two_chat_lines_by_dry_run(lane_app, fake_recall, fake_inworld):
    client, lane = lane_app()
    with client:
        script = load_script()
        m = client.post("/api/dev/meetings", json={"title": script["title"]}).json()
        _post_timeline(client, m["recall_bot_id"], timeline(script, m["recall_bot_id"]))
        record = wait_for(lambda: (r := client.get(f"/api/meetings/{m['meeting_id']}").json())["summary"] and r, timeout=10)
        time.sleep(0.3)
        record = client.get(f"/api/meetings/{m['meeting_id']}").json()
        assert len(record["segments"]) == len(script["lines"])          # 25 lines, one per utterance
        assert len([a for a in record["alerts"] if not a["gated"]]) == 1
        assert len(record["answers"]) == 1 and record["answers"][0]["status"] == "played"
        assert sorted(c["status"] for c in record["chat_posts"]) == ["sent", "sent"]
        assert fake_recall.calls == [] and fake_inworld.calls == []      # the fake meeting never reaches a vendor
        kinds = [k for k, _ in lane.dry_run.calls]
        assert kinds.count("output_audio") == 2 and kinds.count("send_chat") == 2   # filler + answer, alert + answer


def test_real_bot_meeting_plays_filler_then_answer_and_posts_chat_through_recall(lane_app, fake_recall, fake_inworld):
    client, lane = lane_app()
    with client:
        m = _launch(client).json()
        bot = m["recall_bot_id"]
        _post_timeline(client, bot, [(0, fixture_body("bot_status_in_call_recording.json", bot)),
                                     (0, transcript_body(bot, 104, "Tom Walsh",
                                                         "Hey AGI, what was Q3 revenue according to the board deck?", 104.0))])
        wait_for(lambda: len(fake_recall.bodies("/send_chat_message/")) == 1, timeout=10)
        wait_for(lambda: client.get(f"/api/meetings/{m['meeting_id']}").json()["answers"][0]["status"] == "played", timeout=10)
        audio = [b for b in fake_recall.bodies("/output_audio/") if b]
        assert len(audio) == 2
        texts = [c["text"] for c in fake_inworld.calls]
        assert any(t.startswith("CANNED ANSWER") for t in texts)          # the answer was synthesised
        assert any(t in client.app.state.runtime.get_settings().fillers for t in texts)
        (chat,) = fake_recall.bodies("/send_chat_message/")
        assert chat["to"] == "everyone" and chat["message"].startswith("Because you asked:") and len(chat["message"]) <= 500


# ================= review B fixes =================
def _events(client, meeting_id, event_type):
    rt = client.app.state.runtime
    return [e.payload for e in rt.store.events_since(meeting_id) if e.type == event_type]


def _set_status(fake_recall, bot_id, code, sub_code=None):
    fake_recall.bots[bot_id]["status_changes"].append({"code": code, "sub_code": sub_code})


def test_bot_status_never_updates_and_meeting_never_ends_without_status_webhooks(lane_app, fake_recall):
    """Review B item 2: status webhooks are dashboard-level at Recall, so the bot must be polled."""
    client, lane = lane_app()
    with client:
        m = _launch(client).json()
        bot, mid = m["recall_bot_id"], m["meeting_id"]
        _set_status(fake_recall, bot, "in_waiting_room")
        wait_for(lambda: client.get(f"/api/meetings/{mid}").json()["bot_status"] == "waiting_room")
        _set_status(fake_recall, bot, "in_call_recording")
        wait_for(lambda: client.get(f"/api/meetings/{mid}").json()["bot_status"] == "in_call")
        _set_status(fake_recall, bot, "call_ended", "call_ended_by_host")
        record = wait_for(lambda: (r := client.get(f"/api/meetings/{mid}").json())["ended_at"] and r)
        assert record["bot_status"] == "left"
        assert [s.status for s in _events(client, mid, "bot.status")] == ["joining", "waiting_room", "in_call", "left"]
        assert [e.reason for e in _events(client, mid, "meeting.ended")] == ["call_ended"]
        wait_for(lambda: lane.bot.watchers[mid].done())


def test_status_arriving_by_webhook_and_by_poll_is_published_once(lane_app, fake_recall):
    client, lane = lane_app()
    with client:
        m = _launch(client).json()
        bot, mid = m["recall_bot_id"], m["meeting_id"]
        _set_status(fake_recall, bot, "in_call_recording")
        r = client.post(f"/webhooks/recall/{TOKEN}", json=fixture_body("bot_status_in_call_recording.json", bot))
        assert r.status_code == 200
        wait_for(lambda: client.get(f"/api/meetings/{mid}").json()["bot_status"] == "in_call")
        time.sleep(0.3)   # several polls see in_call too
        _set_status(fake_recall, bot, "call_ended")
        for name in ("bot_status_call_ended.json", "bot_status_done.json"):
            client.post(f"/webhooks/recall/{TOKEN}", json=fixture_body(name, bot))
        wait_for(lambda: client.get(f"/api/meetings/{mid}").json()["ended_at"])
        time.sleep(0.3)
        assert [s.status for s in _events(client, mid, "bot.status")] == ["joining", "in_call", "left"]
        assert len(_events(client, mid, "meeting.ended")) == 1


def test_bot_that_dies_fatally_ends_the_meeting_as_bot_left(lane_app, fake_recall):
    client, _ = lane_app()
    with client:
        m = _launch(client).json()
        _set_status(fake_recall, m["recall_bot_id"], "fatal", "bot_kicked_from_waiting_room")
        wait_for(lambda: client.get(f"/api/meetings/{m['meeting_id']}").json()["ended_at"])
        assert [e.reason for e in _events(client, m["meeting_id"], "meeting.ended")] == ["bot_left"]
        assert _events(client, m["meeting_id"], "bot.status")[-1].status == "failed"


def test_recall_status_check_failing_shows_a_warning_and_keeps_polling(lane_app, fake_recall):
    client, _ = lane_app()
    with client:
        m = _launch(client).json()
        bot = m["recall_bot_id"]
        fake_recall.fail[f"/bot/{bot}/"] = 502
        wait_for(lambda: client.get("/api/health").json()["warnings"])
        del fake_recall.fail[f"/bot/{bot}/"]
        _set_status(fake_recall, bot, "in_call_recording")
        wait_for(lambda: client.get(f"/api/meetings/{m['meeting_id']}").json()["bot_status"] == "in_call")
        wait_for(lambda: client.get("/api/health").json()["warnings"] == [])


def test_polling_stops_when_the_dashboard_ends_the_meeting(lane_app, fake_recall):
    client, lane = lane_app()
    with client:
        m = _launch(client).json()
        client.post(f"/api/meetings/{m['meeting_id']}/end")
        wait_for(lambda: lane.bot.watchers[m["meeting_id"]].done())


def test_health_cannot_tell_a_dead_pipe_from_a_quiet_room(lane_app):
    """Review B item 8: every accepted webhook stamps rt.last_webhook_at; rejected ones do not."""
    client, _ = lane_app()
    with client:
        m = client.post("/api/dev/meetings", json={"title": "t"}).json()
        assert client.get("/api/health").json()["last_webhook_at"] is None
        client.post("/webhooks/recall/wrong-token", json=fixture_body("transcript_data.json", m["recall_bot_id"]))
        assert client.get("/api/health").json()["last_webhook_at"] is None
        client.post(f"/webhooks/recall/{TOKEN}", json=fixture_body("transcript_partial_data.json", m["recall_bot_id"]))
        first = client.get("/api/health").json()["last_webhook_at"]
        assert first is not None
        time.sleep(0.01)
        client.post(f"/webhooks/recall/{TOKEN}", json=fixture_body("transcript_data.json", m["recall_bot_id"]))
        assert client.get("/api/health").json()["last_webhook_at"] > first


def test_panic_button_end_all_is_reachable_from_the_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("RECALL_API_KEY", "fake-recall-key-for-tests")
    recall = FakeRecall()
    rt = make_rt(tmp_path, offline=True)
    reg.install(rt, reg.MeetingLane(rt, recall_transport=recall.transport))
    rt.store.create_meeting("live", "recall", MEET_URL, recall_bot_id="b_live")
    recall.bots["b_live"] = {"id": "b_live", "bot_name": "Meet AGI", "status_changes": [{"code": "in_call_recording"}]}
    told = asyncio.run(reg.lane_for(rt).bot.end_all())
    assert told == ["b_live"] and "/api/v1/bot/b_live/leave_call/" in recall.paths("POST")
