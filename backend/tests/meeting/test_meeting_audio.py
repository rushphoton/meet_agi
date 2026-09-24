"""
The voice, the audio queue ("the mouth") and the chat poster, against the fake
Recall and fake Inworld. Failure paths first (CLAUDE.md rule 5).
"""
import asyncio
import base64

from backend.app.contract.events import ChatPost, Mute, SpokenAnswer, Stop, Wake
from backend.app.integrations import register as reg
from backend.app.providers.voice import CANNED_CLIP_TEXT, InworldVoice, VoiceService
from backend.app.integrations.fake_recall import FakeRecall
from backend.tests.meeting.conftest import ROOT, FakeInworld, events_of, instant_sleep, make_rt, settle

CANNED_B64 = base64.b64encode((ROOT / "backend/app/providers/voice/assets/canned_sample_clip.mp3").read_bytes()).decode()


class GateSleep:
    """A sleep the test releases by hand, to prove the next clip waits for this one."""

    def __init__(self) -> None:
        self.waiting: list[float] = []
        self.release = asyncio.Event()

    async def __call__(self, seconds: float) -> None:
        self.waiting.append(seconds)
        await self.release.wait()


def _lane(tmp_path, monkeypatch, recall=None, inworld=None, sleep=instant_sleep, offline=False):
    monkeypatch.setenv("RECALL_API_KEY", "fake-recall-key-for-tests")
    monkeypatch.setenv("INWORLD_API_KEY", "fake-inworld-key-for-tests")
    rt = make_rt(tmp_path, offline=offline)
    recall = recall or FakeRecall()
    inworld = inworld or FakeInworld()
    lane = reg.install(rt, reg.MeetingLane(rt, recall_transport=recall.transport,
                                           inworld_transport=inworld.transport, sleep=sleep))
    record = rt.store.create_meeting("t", source="recall", meeting_url="https://meet.google.com/x",
                                     recall_bot_id="bot_live")
    recall.bots["bot_live"] = {"id": "bot_live", "status_changes": []}
    return rt, lane, recall, inworld, record.meeting_id


def _answer(answer_id="ans_1", status="queued", text="Q3 revenue fell 4 percent.") -> SpokenAnswer:
    return SpokenAnswer(answer_id=answer_id, question="q", text=text, evidence=[], status=status)


def _statuses(rt, mid, answer_id):
    return [e.payload.status for e in events_of(rt, mid, "spoken.answer") if e.payload.answer_id == answer_id]


def _audio_posts(recall):
    return [b["b64_data"] for m, p, b in recall.calls if m == "POST" and p.endswith("/output_audio/")]


# ================= voice =================
def test_inworld_server_error_falls_back_to_canned_clip_that_says_so():
    async def go():
        voice = VoiceService(InworldVoice("k", transport=FakeInworld(status=500).transport), offline=False)
        clip = await voice.synthesize("hello", voice_id="Grant", model_id="inworld-tts-2", allow_vendor=True)
        assert clip.canned and clip.provider == "canned" and "500" in clip.note
        assert "canned sample clip" in CANNED_CLIP_TEXT
    asyncio.run(go())


def test_inworld_timeout_or_bad_answer_falls_back_to_canned_clip():
    async def go():
        for fake in (FakeInworld(raise_timeout=True), FakeInworld(body={"nope": 1}),
                     FakeInworld(body={"audioContent": ""})):
            voice = VoiceService(InworldVoice("k", transport=fake.transport), offline=False)
            clip = await voice.synthesize("hi", voice_id="Grant", model_id="m", allow_vendor=True)
            assert clip.canned
    asyncio.run(go())


def test_offline_no_key_and_fake_meeting_never_call_inworld():
    async def go():
        fake = FakeInworld()
        cases = [VoiceService(InworldVoice("k", transport=fake.transport), offline=True),
                 VoiceService(None, offline=False)]
        for voice in cases:
            assert (await voice.synthesize("x", voice_id="v", model_id="m", allow_vendor=True)).canned
        live = VoiceService(InworldVoice("k", transport=fake.transport), offline=False)
        assert (await live.synthesize("x", voice_id="v", model_id="m", allow_vendor=False)).canned
        assert fake.calls == []
    asyncio.run(go())


def test_inworld_request_shape_and_decoded_audio():
    async def go():
        fake = FakeInworld()
        voice = VoiceService(InworldVoice("fake-key", transport=fake.transport), offline=False)
        clip = await voice.synthesize("Hello there", voice_id="Grant", model_id="inworld-tts-2", allow_vendor=True)
        assert not clip.canned and clip.provider == "inworld" and clip.duration > 0
        assert fake.calls == [{"text": "Hello there", "voiceId": "Grant", "modelId": "inworld-tts-2",
                               "audioConfig": {"audioEncoding": "MP3"}}]
        assert fake.auth == ["Basic fake-key"]
    asyncio.run(go())


# ================= audio queue =================
def test_second_clip_waits_until_the_first_has_finished_playing(tmp_path, monkeypatch):
    async def go():
        gate = GateSleep()
        rt, lane, recall, _, mid = _lane(tmp_path, monkeypatch, sleep=gate)
        await rt.bus.publish(mid, "wake", Wake(trigger="button"))
        await rt.bus.publish(mid, "spoken.answer", _answer())
        await settle()
        assert len(_audio_posts(recall)) == 1          # the filler only
        assert len(gate.waiting) == 1 and gate.waiting[0] > 0.5
        assert _statuses(rt, mid, "ans_1") == ["queued"]
        gate.release.set()
        await settle()
        assert len(_audio_posts(recall)) == 2
        assert _statuses(rt, mid, "ans_1") == ["queued", "playing", "played"]
    asyncio.run(go())


def test_stop_phrase_discards_queued_audio_and_cuts_the_playing_clip(tmp_path, monkeypatch):
    async def go():
        gate = GateSleep()
        rt, lane, recall, _, mid = _lane(tmp_path, monkeypatch, sleep=gate)
        await rt.bus.publish(mid, "spoken.answer", _answer("ans_1"))
        await rt.bus.publish(mid, "spoken.answer", _answer("ans_2"))
        await settle()
        assert len(_audio_posts(recall)) == 1
        await rt.bus.publish(mid, "stop", Stop(trigger="phrase"))
        await settle()
        gate.release.set()
        await settle()
        assert len(_audio_posts(recall)) == 1                      # ans_2 never played
        assert ("DELETE", "/api/v1/bot/bot_live/output_audio/", None) in recall.calls
        assert _statuses(rt, mid, "ans_1") == ["queued", "playing", "stopped"]
        assert _statuses(rt, mid, "ans_2") == ["queued", "stopped"]
    asyncio.run(go())


def test_mute_discards_queue_and_nothing_plays_while_muted(tmp_path, monkeypatch):
    async def go():
        gate = GateSleep()
        rt, lane, recall, _, mid = _lane(tmp_path, monkeypatch, sleep=gate)
        await rt.bus.publish(mid, "spoken.answer", _answer("ans_1"))
        await rt.bus.publish(mid, "spoken.answer", _answer("ans_2"))
        await settle()
        await rt.bus.publish(mid, "mute", Mute(muted=True))
        await rt.bus.publish(mid, "wake", Wake(trigger="button"))          # no filler while muted
        await rt.bus.publish(mid, "spoken.answer", _answer("ans_3"))     # engine raced the mute
        gate.release.set()
        await settle()
        assert len(_audio_posts(recall)) == 1
        assert _statuses(rt, mid, "ans_1")[-1] == "muted"
        assert _statuses(rt, mid, "ans_2") == ["queued", "muted"]
        assert _statuses(rt, mid, "ans_3") == ["queued", "muted"]
    asyncio.run(go())


def test_own_status_updates_are_never_replayed(tmp_path, monkeypatch):
    async def go():
        rt, lane, recall, _, mid = _lane(tmp_path, monkeypatch)
        for status in ("playing", "played", "stopped", "muted", "failed"):
            await rt.bus.publish(mid, "spoken.answer", _answer(status=status))
        await settle()
        assert _audio_posts(recall) == []
    asyncio.run(go())


def test_recall_refusing_a_clip_marks_it_failed_and_the_next_still_plays(tmp_path, monkeypatch):
    async def go():
        recall = FakeRecall(fail={"/output_audio/": 500})
        rt, lane, _, _, mid = _lane(tmp_path, monkeypatch, recall=recall)
        await rt.bus.publish(mid, "spoken.answer", _answer("ans_1"))
        await settle()
        recall.fail.clear()
        await rt.bus.publish(mid, "spoken.answer", _answer("ans_2"))
        await settle()
        assert _statuses(rt, mid, "ans_1") == ["queued", "playing", "failed"]
        assert _statuses(rt, mid, "ans_2") == ["queued", "playing", "played"]
    asyncio.run(go())


def test_voice_failure_plays_the_canned_clip_instead_of_silence(tmp_path, monkeypatch):
    async def go():
        rt, lane, recall, _, mid = _lane(tmp_path, monkeypatch, inworld=FakeInworld(status=503))
        await rt.bus.publish(mid, "spoken.answer", _answer())
        await settle()
        assert _audio_posts(recall) == [CANNED_B64]
        assert _statuses(rt, mid, "ans_1")[-1] == "played"
    asyncio.run(go())


def test_filler_lines_are_synthesised_once_and_reused(tmp_path, monkeypatch):
    async def go():
        inworld = FakeInworld()
        rt, lane, recall, _, mid = _lane(tmp_path, monkeypatch, inworld=inworld)
        n = len(rt.get_settings().fillers)
        for _ in range(n * 2):
            await rt.bus.publish(mid, "wake", Wake(trigger="button"))
            await settle()
        assert len(inworld.calls) == n
        assert len(_audio_posts(recall)) == n * 2
        assert {c["text"] for c in inworld.calls} == set(rt.get_settings().fillers)
    asyncio.run(go())


def test_meeting_end_throws_away_audio_still_in_line(tmp_path, monkeypatch):
    async def go():
        from backend.app.contract.events import MeetingEnded
        gate = GateSleep()
        rt, lane, recall, _, mid = _lane(tmp_path, monkeypatch, sleep=gate)
        await rt.bus.publish(mid, "spoken.answer", _answer("ans_1"))
        await rt.bus.publish(mid, "spoken.answer", _answer("ans_2"))
        await settle()
        await rt.bus.publish(mid, "meeting.ended", MeetingEnded(reason="call_ended"))
        gate.release.set()
        await settle()
        assert len(_audio_posts(recall)) == 1
        assert _statuses(rt, mid, "ans_2") == ["queued", "stopped"]
        await rt.bus.publish(mid, "spoken.answer", _answer("ans_3"))
        await settle()
        assert _statuses(rt, mid, "ans_3") == ["queued", "stopped"]
    asyncio.run(go())


# ================= chat poster =================
def _post(chat_id="chat_1", status="pending", text="Because you asked: it fell 4%.") -> ChatPost:
    return ChatPost(chat_id=chat_id, text=text, reason="answer", status=status)


def _chat_statuses(rt, mid, chat_id):
    return [e.payload.status for e in events_of(rt, mid, "chat.post") if e.payload.chat_id == chat_id]


def test_chat_post_refused_by_recall_is_marked_failed(tmp_path, monkeypatch):
    async def go():
        recall = FakeRecall(fail={"/send_chat_message/": 400})
        rt, lane, _, _, mid = _lane(tmp_path, monkeypatch, recall=recall)
        await rt.bus.publish(mid, "chat.post", _post())
        await settle()
        assert _chat_statuses(rt, mid, "chat_1") == ["pending", "failed"]
    asyncio.run(go())


def test_chat_is_suppressed_while_muted(tmp_path, monkeypatch):
    async def go():
        rt, lane, recall, _, mid = _lane(tmp_path, monkeypatch)
        await rt.bus.publish(mid, "mute", Mute(muted=True))
        await rt.bus.publish(mid, "chat.post", _post())
        await settle()
        assert _chat_statuses(rt, mid, "chat_1") == ["pending", "suppressed_muted"]
        assert not any(p.endswith("/send_chat_message/") for p in recall.paths())
    asyncio.run(go())


def test_chat_post_is_sent_once_to_everyone_and_not_looped(tmp_path, monkeypatch):
    async def go():
        rt, lane, recall, _, mid = _lane(tmp_path, monkeypatch)
        await rt.bus.publish(mid, "chat.post", _post())
        await rt.bus.publish(mid, "chat.post", _post("chat_2", status="sent"))   # someone else's result
        await settle()
        assert recall.bodies("/send_chat_message/") == [{"to": "everyone", "message": "Because you asked: it fell 4%."}]
        assert _chat_statuses(rt, mid, "chat_1") == ["pending", "sent"]
        assert recall.auth_headers[-1] == "Token fake-recall-key-for-tests"
    asyncio.run(go())
