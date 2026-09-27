"""
WHY THIS EXISTS
Findings from gate review v0.1 (reviews/gate-v0.1.md, 27 Sep 2026), each reproduced as a test named after
what the room would see. Engine and meeting findings live here too, so the gate's fixes are proven in one
place.
"""
import json

from backend.app.contract.records import Settings
from backend.app.settings import SettingsStore

OLD_STOP = ["agi stop talking", "a g i stop talking", "aji stop talking", "agi stop", "stop talking agi"]


# ---- item 1: a saved settings file hid new defaults ------------------------------------------------

def test_saved_settings_hid_the_new_stop_phrases(tmp_path):
    old = Settings().model_dump(mode="json")
    old["stop_variants"] = OLD_STOP
    old["answer_max_words"] = 60
    old["wake"]["question_wait_seconds"] = 8
    old["speakers"] = [{"match_name": "Ray", "display_name": "Ray Wan", "role": "CFO"}]
    (tmp_path / "settings.json").write_text(json.dumps(old), encoding="utf-8")

    s = SettingsStore(tmp_path).get()
    assert "stop talking" in s.stop_variants          # old default upgraded
    assert s.answer_max_words == 120
    assert s.wake.question_wait_seconds == 15
    assert s.speakers[0].display_name == "Ray Wan"    # the user's own choices survive


def test_saved_settings_keep_a_value_the_user_chose(tmp_path):
    chosen = Settings().model_dump(mode="json")
    chosen["answer_max_words"] = 80
    chosen["gate"]["min_confidence"] = 0.9
    (tmp_path / "settings.json").write_text(json.dumps(chosen), encoding="utf-8")
    s = SettingsStore(tmp_path).get()
    assert s.answer_max_words == 80
    assert s.gate.min_confidence == 0.9


def test_a_later_default_change_reaches_settings_saved_from_the_dashboard(tmp_path, monkeypatch):
    store = SettingsStore(tmp_path)
    store.put(Settings(answer_max_words=80))          # saved from the dashboard: stamps today's defaults
    # A later release changes the default fillers; the user never touched them.
    import backend.app.settings as settings_mod
    new_fillers = ["One sec."]

    class Later(Settings):
        fillers: list[str] = new_fillers

    monkeypatch.setattr(settings_mod, "Settings", Later)
    s = settings_mod.SettingsStore(tmp_path).get()
    assert s.fillers == new_fillers
    assert s.answer_max_words == 80


# ---- item 2: a Gemini rate limit made the bot apologize on stage -----------------------------------
import asyncio  # noqa: E402

import httpx  # noqa: E402

from backend.app.providers.llm import RealProvider  # noqa: E402
from backend.app.providers.llm import vendors  # noqa: E402
from backend.app.providers.llm.vendors import VendorClient  # noqa: E402
from backend.tests.engine.test_engine_providers import LINES, PASSAGES  # noqa: E402

OK_CHECK = {"candidates": [{"content": {"parts": [
    {"text": '{"worth_a_look": true, "score": 0.8, "topic": "Q3 revenue"}'}]}}]}
RATE_LIMITED = {"error": {"message": "Resource has been exhausted (e.g. check quota)."}}


def _gemini(handler, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "sk-test-FAKE-not-a-real-key-000000000000000000")
    monkeypatch.setattr(vendors, "RATE_LIMIT_PAUSE_SECONDS", 0.0)
    return RealProvider(VendorClient(transport=httpx.MockTransport(handler)))


def _model(request):
    return request.url.path.split("/")[-1].split(":")[0]


def test_one_gemini_rate_limit_made_the_bot_say_it_could_not_look_that_up(monkeypatch):
    seen = []

    def handler(request):
        seen.append(_model(request))
        if len(seen) == 1:
            return httpx.Response(429, json=RATE_LIMITED)
        return httpx.Response(200, json=OK_CHECK)

    result = asyncio.run(_gemini(handler, monkeypatch).cheap_check("gemini-3.5-flash-lite", LINES, PASSAGES, []))
    assert result.worth_a_look
    assert seen == ["gemini-3.5-flash-lite", "gemini-3.5-flash-lite"]


def test_a_used_up_gemini_quota_moves_to_the_backup_model_with_its_own_quota(monkeypatch):
    seen = []

    def handler(request):
        seen.append(_model(request))
        if _model(request) == "gemini-3.5-flash-lite":
            return httpx.Response(429, json=RATE_LIMITED)
        return httpx.Response(200, json=OK_CHECK)

    result = asyncio.run(_gemini(handler, monkeypatch).cheap_check("gemini-3.5-flash-lite", LINES, PASSAGES, []))
    assert result.model == "gemini-2.5-flash-lite"
    assert seen == ["gemini-3.5-flash-lite", "gemini-3.5-flash-lite", "gemini-2.5-flash-lite"]


# ---- item 3: go.py shut everything down 10 s after READY when the tunnel retry had succeeded -------
import importlib.util  # noqa: E402
import os  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
from pathlib import Path  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]


def _load_go():
    spec = importlib.util.spec_from_file_location("go_under_test", ROOT / "scripts" / "go.py")
    go = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(go)
    return go


def test_tunnel_retry_succeeded_then_go_py_shut_everything_down(tmp_path, monkeypatch):
    go = _load_go()
    monkeypatch.setattr(go, "LOGS", tmp_path)
    first, _ = go.start("tunnel", [sys.executable, "-c", "import sys; sys.exit(1)"])   # ERR_NGROK_334 attempt
    first.wait(timeout=10)
    second, _ = go.start("tunnel", [sys.executable, "-c", "import time; time.sleep(30)"])  # the retry that works
    try:
        assert go.dead_parts() == []
    finally:
        second.kill()
        second.wait(timeout=10)


def test_reusing_an_earlier_ngrok_made_go_py_shut_everything_down():
    src = (ROOT / "scripts" / "go.py").read_text(encoding="utf-8")
    reuse = src.index("already running from an earlier ngrok")
    assert 'running.pop("tunnel", None)' in src[reuse:reuse + 300]


def test_a_part_that_really_crashes_is_still_caught(tmp_path, monkeypatch):
    go = _load_go()
    monkeypatch.setattr(go, "LOGS", tmp_path)
    p, _ = go.start("backend", [sys.executable, "-c", "import sys; sys.exit(3)"])
    p.wait(timeout=10)
    assert go.dead_parts() == ["backend"]


def test_go_py_failing_after_ready_left_the_bot_in_the_call(tmp_path, monkeypatch):
    go = _load_go()
    ended = []
    monkeypatch.setattr(go, "LOGS", tmp_path)
    monkeypatch.setattr(go, "end_live_meetings", lambda why: ended.append(why))
    backend, _ = go.start("backend", [sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        go.fail("A part stopped unexpectedly: tunnel.", "test")
    except SystemExit:
        pass
    assert ended == ["Stopping after a failure"]
    assert backend.poll() is not None      # and everything was still stopped


# ---- item 4: the presenter's own loose numbers got "Because you mentioned..." alerts ---------------
def test_presenter_saying_about_19_cents_i_think_got_a_chat_alert():
    # The model's behavior can't be unit-tested; what can be pinned is that both prompts tell it that
    # casual approximation is not doubt, while explicit doubt and self-correction still count (Ray, Round 2).
    from backend.app.providers.llm.real import CHEAP_SYSTEM, JUDGE_SYSTEM
    for prompt in (CHEAP_SYSTEM, JUDGE_SYSTEM):
        assert "about 19 cents, I think" in prompt
        assert "not doubt" in prompt
        assert "I think it was around" not in prompt      # the phrase that invited the false alerts
        assert "not sure that's correct" in prompt        # explicit doubt still counts
        assert "corrects" in prompt                       # self-correction still counts


# ---- item 5: ordinary phrases were treated as "sounds like Hey AGI" ---------------------------------
import pytest  # noqa: E402

from backend.app.pipeline.phrases import STRONG_TOKENS, fuzzy_wake_candidate  # noqa: E402


@pytest.mark.parametrize("sentence", [
    "Okay, again, what was Q3 revenue?", "OK, agreed, let's move on.", "Hey, agreed.",
    "Okay, give me the churn number.", "OK, agenda item two.", "Hey, good to see you, give me a sec.",
])
def test_okay_again_what_was_q3_revenue_asked_of_a_person_woke_the_bot(sentence):
    assert fuzzy_wake_candidate(sentence) is None      # not even worth asking the model


@pytest.mark.parametrize("sentence", ["Hey Aggie, what was Q3 revenue?", "Okay AGI, what's churn?",
                                      "Hey giant, what was churn?", "Hey GI Joe, pipeline?"])
def test_real_caption_misspellings_still_reach_the_wake_check(sentence):
    assert fuzzy_wake_candidate(sentence) is not None


def test_colleague_ajay_woke_the_bot_when_the_wake_check_was_down():
    assert "aj" not in STRONG_TOKENS and "ajay" not in STRONG_TOKENS


# ---- item 6: a new "Hey AGI" during a long answer didn't cut the old one --------------------------
from backend.tests.engine.harness import Harness, Scripted, run  # noqa: E402


def test_hey_agi_follow_up_during_a_long_answer_waited_for_the_whole_old_answer(tmp_path):
    async def go():
        h = Harness(tmp_path, Scripted())
        await h.say("Ray Wan", "Hey AGI, what was Q3 revenue?", at=10)
        await h.settle()
        assert h.events("stop") == []                      # nothing was playing before the first question
        await h.bus.publish(h.meeting_id, "spoken.answer",  # the meeting lane: the answer is now playing
                            h.events("spoken.answer")[-1].payload.model_copy(update={"status": "playing"}))
        await h.say("Ray Wan", "Hey AGI, and what about Q4?", at=15)
        await h.settle()
        [stop] = h.events("stop")
        second_wake = h.events("wake")[-1]
        assert stop.seq < second_wake.seq                   # old answer cut before the new filler
        assert second_wake.payload.question == "and what about Q4?"
    run(go())


def test_hey_agi_long_after_the_last_answer_publishes_no_stop(tmp_path):
    async def go():
        h = Harness(tmp_path, Scripted())
        await h.say("Ray Wan", "Hey AGI, what was Q3 revenue?", at=10)
        await h.settle()
        await h.bus.publish(h.meeting_id, "spoken.answer",
                            h.events("spoken.answer")[-1].payload.model_copy(update={"status": "played"}))
        h.engine._state(h.meeting_id).last_answer_at -= 60   # a minute later
        await h.say("Ray Wan", "Hey AGI, and what about Q4?", at=75)
        await h.settle()
        assert h.events("stop") == []
    run(go())


# ---- item 7: after "stop talking" the next sentence still played (already queued at Attendee) ------
def test_stop_talking_let_the_next_sentence_play_because_it_was_already_queued_at_attendee():
    # Only a rehearsal proves the right number; this pins the margin that leaves Attendee 0.6 s to start
    # each clip before the next one is sent (it was 0.25 s).
    from backend.app.speech import audio_out
    assert audio_out.PLAYBACK_MARGIN_SECONDS >= 0.5


# ---- item 9: every event rewrote the whole meeting file, slowing a long meeting -------------------
def test_long_meeting_rewrote_the_whole_file_on_every_event_and_lagged(tmp_path, monkeypatch):
    import json as _json
    import time as _time

    from backend.app.contract.events import MeetingEnded, TranscriptSegment
    from backend.app.core import store as store_mod
    from backend.app.core.bus import EventBus
    from backend.app.core.store import Store

    async def go():
        s = Store(tmp_path)
        bus = EventBus(s)
        mid = s.create_meeting("t", "recall").meeting_id
        saves = []
        real_save = s._save
        monkeypatch.setattr(s, "_save", lambda m: (saves.append(m), real_save(m)))
        for i in range(50):
            await bus.publish(mid, "transcript.segment", TranscriptSegment(
                segment_id=f"s{i}", speaker_id="a", speaker_name="A", text="words", t_start=i, t_end=i + 1,
                source="recall"))
        assert len(saves) <= 2                              # not 50 whole-file rewrites
        await asyncio.sleep(store_mod.SAVE_EVERY_SECONDS + 0.3)
        on_disk = _json.loads(s.path_for(mid).read_text(encoding="utf-8"))
        assert len(on_disk["record"]["segments"]) == 50      # nothing lost, just batched
        await bus.publish(mid, "meeting.ended", MeetingEnded(reason="call_ended"))
        on_disk = _json.loads(s.path_for(mid).read_text(encoding="utf-8"))
        assert on_disk["record"]["ended_at"] is not None     # the end is written at once
    asyncio.run(go())


# ---- item 10: the dashboard opened by the laptop's name (same Wi-Fi) showed errors and leaked the stream
def test_dashboard_opened_from_the_venue_wifi_showed_errors_and_relayed_the_live_transcript():
    # Seen when checking item 10 (27 Sep): through Next.js with Host "laptop-7toqnc8b:3000", /api/health got
    # the tunnel guard's 404 but /api/meetings/<id>/events streamed. The dashboard must listen on 127.0.0.1.
    src = (ROOT / "scripts" / "go.py").read_text(encoding="utf-8")
    assert '"start", "--", "-p", "3000", "-H", "127.0.0.1"' in src
