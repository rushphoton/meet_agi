"""
Sentence-by-sentence speech (Ray's live Meet test, 27 Sep 2026): "Some responses
need to be longer so that I can tell it to stop talking and it should still be
part-way through that response." On Attendee a clip already playing cannot be
cut, so an answer is now a chain of sentence clips and "stop" drops every
sentence not yet sent. Against the fake Recall and the fake Attendee, never a
live vendor. Failure paths first (CLAUDE.md rule 5), named after the symptom.
"""
import asyncio
import base64
import logging
import time

import httpx
import pytest

from backend.app.contract.events import MeetingEnded, Mute, SpokenAnswer, Stop, Wake
from backend.app.integrations import register as reg
from backend.app.integrations.fake_attendee import FakeAttendee
from backend.app.integrations.fake_recall import FakeRecall
from backend.app.speech.sentences import split_raw, split_sentences
from backend.tests.meeting.conftest import FAKE_INWORLD_KEY, FAKE_RECALL_KEY, INWORLD_AUDIO, FakeInworld, events_of, make_rt, settle

FIVE = ("Q3 revenue was 41.2 million dollars according to the board deck. "
        "That is down four percent from 42.9 million in Q2. "
        "The drop came mostly from the enterprise segment in the U.S. market. "
        "Bookings lead revenue by about one quarter here. "
        "So the dip reflects the weaker Q2 bookings.")
FIVE_SENTENCES = split_sentences(FIVE)
STOP_TIMINGS: dict[str, float] = {}     # filled by the stop-timing test, printed with -s


# ------------------------------------------------------------------ test equipment
class TextInworld(FakeInworld):
    """A fake Inworld whose clip carries its own text after the MP3 frames (the duration reader
    skips it), so the test can tell which sentence each clip sent to the meeting was. It can
    also take `seconds_per_sentence` to answer, and fail from the n-th call on."""

    def __init__(self, seconds_per_sentence: float = 0.0, fail_from_call: int | None = None) -> None:
        super().__init__()
        self.seconds_per_sentence, self.fail_from_call = seconds_per_sentence, fail_from_call

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.ahandle)

    async def ahandle(self, request: httpx.Request) -> httpx.Response:
        import json
        body = json.loads(request.content)
        self.calls.append(body)
        if self.seconds_per_sentence:
            await asyncio.sleep(self.seconds_per_sentence * max(1, len(split_raw(body["text"]))))
        if self.fail_from_call is not None and len(self.calls) >= self.fail_from_call:
            return httpx.Response(503, json={"error": "fake outage"})
        audio = INWORLD_AUDIO + b"|TEXT|" + body["text"].encode()
        return httpx.Response(200, json={"audioContent": base64.b64encode(audio).decode()})


class ClipSleep:
    """Holds every clip 'playing' until the test releases it, one at a time."""

    def __init__(self) -> None:
        self.gates: list[asyncio.Event] = []

    async def __call__(self, seconds: float) -> None:
        gate = asyncio.Event()
        self.gates.append(gate)
        await gate.wait()

    async def finish_clip(self) -> None:
        self.gates[-1].set()
        await settle()


class Timed(httpx.AsyncBaseTransport):
    """Wraps a fake vendor's transport and writes down when each call arrived."""

    def __init__(self, inner: httpx.MockTransport) -> None:
        self.inner, self.times = inner, []

    async def handle_async_request(self, request):
        self.times.append((time.perf_counter(), request.method, request.url.path))
        return await self.inner.handle_async_request(request)


class Vendor:
    """The bot vendor under test, Recall or Attendee, with one way to read what it was sent."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.fake = FakeRecall() if name == "recall" else FakeAttendee()
        self.timed = Timed(self.fake.transport)
        self.suffix = "/output_audio/" if name == "recall" else "/output_audio"

    def clips(self) -> list[str]:
        """Texts of the clips handed to the meeting, in order (b'' for a clip with no text)."""
        key = "b64_data" if self.name == "recall" else "data"
        out = []
        for method, path, body in self.fake.calls:
            if method == "POST" and path.endswith(self.suffix):
                raw = base64.b64decode(body[key])
                out.append(raw.split(b"|TEXT|", 1)[1].decode() if b"|TEXT|" in raw else "")
        return out

    def cut_calls(self) -> int:
        return len([1 for m, p, _ in self.fake.calls if m == "DELETE" and "output_audio" in p])


def _lane(tmp_path, monkeypatch, vendor: Vendor, inworld=None, sleep=None):
    monkeypatch.setenv("BOT_PROVIDER", vendor.name)
    monkeypatch.setenv("RECALL_API_KEY", FAKE_RECALL_KEY)
    monkeypatch.setenv("INWORLD_API_KEY", FAKE_INWORLD_KEY)
    rt = make_rt(tmp_path)
    inworld = inworld or TextInworld()
    kwargs = {"recall_transport": vendor.timed} if vendor.name == "recall" else {"attendee_transport": vendor.timed}
    lane = reg.install(rt, reg.MeetingLane(rt, inworld_transport=inworld.transport, sleep=sleep or ClipSleep(),
                                           **kwargs))
    assert lane.provider == vendor.name
    record = rt.store.create_meeting("t", source="recall", meeting_url="https://meet.google.com/x",
                                     recall_bot_id="bot_live")
    if vendor.name == "recall":
        vendor.fake.bots["bot_live"] = {"id": "bot_live", "status_changes": []}
    else:
        vendor.fake.bots["bot_live"] = {"id": "bot_live", "state": "joined_recording", "events": [], "metadata": {}}
    return rt, lane, inworld, record.meeting_id


def _answer(text=FIVE, answer_id="ans_1") -> SpokenAnswer:
    return SpokenAnswer(answer_id=answer_id, question="What was Q3 revenue?", text=text, evidence=[], status="queued")


def _statuses(rt, mid, answer_id="ans_1"):
    return [e.payload.status for e in events_of(rt, mid, "spoken.answer") if e.payload.answer_id == answer_id]


VENDORS = pytest.mark.parametrize("vendor_name", ["recall", "attendee"])


# ------------------------------------------------------------------ the splitter (failure paths first)
def test_41_2_million_was_read_as_two_broken_clips():
    text = "Q3 revenue was 41.2 million dollars. It was $1.5M below plan in total."
    assert split_raw(text) == ["Q3 revenue was 41.2 million dollars.", "It was $1.5M below plan in total."]


def test_q3_with_a_full_stop_is_not_cut_off_from_its_sentence():
    assert split_raw("Revenue fell in Q3. Margins held up well though.") == [
        "Revenue fell in Q3.", "Margins held up well though."]
    assert split_raw("The dip in Q3. revenue was driven by churn.") == [
        "The dip in Q3. revenue was driven by churn."]


def test_u_s_and_other_abbreviations_did_not_end_a_sentence():
    assert split_raw("Sales in the U.S. Market grew. Dr. Smith agreed, e.g. on pricing.") == [
        "Sales in the U.S. Market grew.", "Dr. Smith agreed, e.g. on pricing."]
    assert split_raw("Talk to J. Smith vs. the board first. Then decide.") == [
        "Talk to J. Smith vs. the board first.", "Then decide."]


def test_a_one_word_sentence_would_be_a_clip_of_its_own():
    assert split_sentences("Yes. Q3 revenue was 41.2 million. Sure.") == ["Yes. Q3 revenue was 41.2 million. Sure."]
    assert split_sentences("Short one. This one is long enough to stand alone. And this too, clearly.") == [
        "Short one. This one is long enough to stand alone.", "And this too, clearly."]
    assert split_sentences("Yes.") == ["Yes."] and split_sentences("") == []


def test_splitting_never_drops_or_rewords_a_word():
    assert " ".join(FIVE_SENTENCES) == FIVE
    assert len(FIVE_SENTENCES) == 5
    assert "41.2 million" in FIVE_SENTENCES[0] and "U.S. market" in FIVE_SENTENCES[2]


# ------------------------------------------------------------------ the chain, failure paths first
@VENDORS
def test_stop_talking_after_clip_2_still_played_the_rest_of_the_answer(tmp_path, monkeypatch, caplog, vendor_name):
    async def go():
        vendor, sleep = Vendor(vendor_name), ClipSleep()
        rt, lane, inworld, mid = _lane(tmp_path, monkeypatch, vendor, sleep=sleep)
        await rt.bus.publish(mid, "spoken.answer", _answer())
        await settle()
        assert vendor.clips() == FIVE_SENTENCES[:1]
        await sleep.finish_clip()
        assert vendor.clips() == FIVE_SENTENCES[:2]          # clip 2 is playing now
        with caplog.at_level(logging.INFO, logger="meet_agi.audio"):
            await rt.bus.publish(mid, "stop", Stop(trigger="phrase"))
            await settle()
        for _ in range(5):                                    # let any clip that was "playing" finish
            if sleep.gates:
                sleep.gates[-1].set()
            await settle()
        assert vendor.clips() == FIVE_SENTENCES[:2]           # clips 3-5 are never sent
        assert _statuses(rt, mid) == ["queued", "playing", "stopped"]
        assert [c["text"] for c in inworld.calls] == FIVE_SENTENCES[:3]   # 3 was being prepared, then dropped
        if vendor_name == "recall":
            assert vendor.cut_calls() == 1                    # Recall's DELETE output_audio
        else:
            assert vendor.cut_calls() == 0 and lane.attendee.stop_requests == 1   # Attendee has no cut call
        spoken = " ".join(FIVE_SENTENCES[:2])
        assert any("after 2 of 5 sentence(s)" in r.message and spoken in r.message for r in caplog.records)
    asyncio.run(go())


@VENDORS
def test_mute_in_the_middle_of_a_long_answer_kept_talking(tmp_path, monkeypatch, vendor_name):
    async def go():
        vendor, sleep = Vendor(vendor_name), ClipSleep()
        rt, lane, _, mid = _lane(tmp_path, monkeypatch, vendor, sleep=sleep)
        await rt.bus.publish(mid, "spoken.answer", _answer())
        await settle()
        await sleep.finish_clip()
        await sleep.finish_clip()
        assert len(vendor.clips()) == 3
        await rt.bus.publish(mid, "mute", Mute(muted=True))
        for _ in range(5):
            if sleep.gates:
                sleep.gates[-1].set()
            await settle()
        assert vendor.clips() == FIVE_SENTENCES[:3]
        assert _statuses(rt, mid) == ["queued", "playing", "muted"]
    asyncio.run(go())


@VENDORS
def test_voice_failing_mid_answer_plays_the_canned_clip_or_hangs(tmp_path, monkeypatch, vendor_name):
    async def go():
        vendor, sleep = Vendor(vendor_name), ClipSleep()
        inworld = TextInworld(fail_from_call=3)                 # sentence 3 fails
        rt, lane, _, mid = _lane(tmp_path, monkeypatch, vendor, inworld=inworld, sleep=sleep)
        await rt.bus.publish(mid, "spoken.answer", _answer())
        await settle()
        await sleep.finish_clip()
        await sleep.finish_clip()
        assert vendor.clips() == FIVE_SENTENCES[:2]           # nothing after the failure, never a canned clip
        assert "" not in vendor.clips()
        assert _statuses(rt, mid) == ["queued", "playing", "failed"]
        assert rt.warnings["meeting.voice"] == "Voice (Inworld) failing - answers go to chat only"
        await rt.bus.publish(mid, "spoken.answer", _answer("Next answer is a short one.", "ans_2"))
        await settle()
        assert _statuses(rt, mid, "ans_2") == ["queued", "failed"]   # voice still down: chat only
    asyncio.run(go())


@VENDORS
def test_vendor_refusing_clip_3_marks_the_answer_failed_and_sends_no_more(tmp_path, monkeypatch, vendor_name):
    async def go():
        vendor, sleep = Vendor(vendor_name), ClipSleep()
        rt, lane, _, mid = _lane(tmp_path, monkeypatch, vendor, sleep=sleep)
        await rt.bus.publish(mid, "spoken.answer", _answer())
        await settle()
        await sleep.finish_clip()
        vendor.fake.fail[vendor.suffix] = 500
        await sleep.finish_clip()
        vendor.fake.fail.clear()
        await settle()
        assert vendor.clips() == FIVE_SENTENCES[:3]           # clip 3 was tried and refused; 4 and 5 never tried
        assert _statuses(rt, mid) == ["queued", "playing", "failed"]
    asyncio.run(go())


@VENDORS
def test_meeting_ending_mid_answer_sends_nothing_more(tmp_path, monkeypatch, vendor_name):
    async def go():
        vendor, sleep = Vendor(vendor_name), ClipSleep()
        rt, lane, _, mid = _lane(tmp_path, monkeypatch, vendor, sleep=sleep)
        await rt.bus.publish(mid, "spoken.answer", _answer())
        await settle()
        await rt.bus.publish(mid, "meeting.ended", MeetingEnded(reason="dashboard"))
        for _ in range(5):
            if sleep.gates:
                sleep.gates[-1].set()
            await settle()
        assert vendor.clips() == FIVE_SENTENCES[:1]
        assert _statuses(rt, mid)[-1] == "stopped"
    asyncio.run(go())


# ------------------------------------------------------------------ the chain, happy path
@VENDORS
def test_a_five_sentence_answer_is_five_clips_in_order_one_at_a_time(tmp_path, monkeypatch, vendor_name):
    async def go():
        vendor, sleep = Vendor(vendor_name), ClipSleep()
        rt, lane, inworld, mid = _lane(tmp_path, monkeypatch, vendor, sleep=sleep)
        await rt.bus.publish(mid, "spoken.answer", _answer())
        await settle()
        for n in range(1, 6):
            assert vendor.clips() == FIVE_SENTENCES[:n]       # the next clip waits for this one
            assert len(sleep.gates) == n
            await sleep.finish_clip()
        assert vendor.clips() == FIVE_SENTENCES
        assert [c["text"] for c in inworld.calls] == FIVE_SENTENCES
        assert _statuses(rt, mid) == ["queued", "playing", "played"]   # status events as before
    asyncio.run(go())


@VENDORS
def test_filler_still_plays_first_and_the_first_sentence_is_prepared_meanwhile(tmp_path, monkeypatch, vendor_name):
    async def go():
        vendor, sleep = Vendor(vendor_name), ClipSleep()
        rt, lane, inworld, mid = _lane(tmp_path, monkeypatch, vendor, sleep=sleep)
        await rt.bus.publish(mid, "wake", Wake(trigger="button"))
        await rt.bus.publish(mid, "spoken.answer", _answer())
        await settle()
        fillers = rt.get_settings().fillers
        assert len(vendor.clips()) == 1 and vendor.clips()[0] in fillers          # the filler is playing
        assert FIVE_SENTENCES[0] in [c["text"] for c in inworld.calls]            # sentence 1 already synthesised
        await sleep.finish_clip()
        assert vendor.clips()[1] == FIVE_SENTENCES[0]
    asyncio.run(go())


@VENDORS
def test_long_answer_waited_for_the_whole_answer_to_be_synthesised_before_speaking(tmp_path, monkeypatch, vendor_name):
    """Fake voice: 1 s per sentence. Synthesising the whole 5-sentence answer as one clip
    would take 5 s; the first sentence clip must reach the meeting after about 1 s."""
    async def go():
        vendor, sleep = Vendor(vendor_name), ClipSleep()
        inworld = TextInworld(seconds_per_sentence=1.0)
        rt, lane, _, mid = _lane(tmp_path, monkeypatch, vendor, inworld=inworld, sleep=sleep)
        t0 = time.perf_counter()
        await rt.bus.publish(mid, "spoken.answer", _answer())
        while not vendor.clips() and time.perf_counter() - t0 < 6:
            await asyncio.sleep(0.01)
        first_clip = next(t for t, m, p in vendor.timed.times if p.endswith(vendor.suffix)) - t0
        STOP_TIMINGS[f"{vendor_name}: queued -> first clip sent"] = first_clip
        assert 0.9 <= first_clip < 1.5, first_clip
        await rt.bus.publish(mid, "stop", Stop(trigger="button"))
        await settle()
    asyncio.run(go())


@VENDORS
def test_stop_cuts_the_queue_in_the_same_step_and_the_last_vendor_call_is_immediate(tmp_path, monkeypatch, vendor_name):
    """Ray may say "stop talking" while the bot speaks. Meet's caption arrives ~1-2 s later; nothing
    on our side may add to that: the queue is cut before the stop handler first waits on anything."""
    async def go():
        vendor, sleep = Vendor(vendor_name), ClipSleep()
        rt, lane, inworld, mid = _lane(tmp_path, monkeypatch, vendor, sleep=sleep)
        await rt.bus.publish(mid, "spoken.answer", _answer())
        await settle()
        await sleep.finish_clip()                               # clip 2 playing, clip 3 prepared
        state = lane.audio.meeting_state(mid)
        item = state.current
        calls_before = len(vendor.timed.times)
        t0 = time.perf_counter()
        handler = asyncio.ensure_future(lane.audio.on_stop(type("E", (), {"meeting_id": mid})()))
        await asyncio.sleep(0)                                  # one step: the handler runs to its first await
        assert item.dropped                                     # the queue is cut before any vendor call
        assert item.next_task.cancelling() or item.next_task.done()   # sentence 3 will never be sent
        await handler
        await settle()
        after = vendor.timed.times[calls_before:]
        last = (after[-1][0] - t0) if after else 0.0
        STOP_TIMINGS[f"{vendor_name}: stop -> last vendor call"] = last
        STOP_TIMINGS[f"{vendor_name}: vendor calls after stop"] = len(after)
        if vendor_name == "recall":
            assert [(m, p.endswith("/output_audio/")) for _, m, p in after] == [("DELETE", True)]
        else:
            assert after == []                                  # Attendee: nothing to call; the queue is simply empty
        assert last < 0.05, last
        for _ in range(5):
            if sleep.gates:
                sleep.gates[-1].set()
            await settle()
        assert vendor.clips() == FIVE_SENTENCES[:2]
        print("\nSTOP TIMINGS:", {k: (round(v * 1000, 2) if isinstance(v, float) else v)
                                  for k, v in STOP_TIMINGS.items()}, "(ms / count)")
    asyncio.run(go())


def test_fake_meeting_keeps_one_canned_clip_per_answer(tmp_path, monkeypatch):
    """The DRY RUN target plays the canned clip, whose audio is the same for any text: splitting
    would only repeat 'this is a canned sample clip' once per sentence."""
    async def go():
        rt = make_rt(tmp_path)
        lane = reg.install(rt, reg.MeetingLane(rt, recall_transport=FakeRecall().transport))
        record = rt.store.create_meeting("t", source="replay", meeting_url=None, recall_bot_id="replay-bot_x")
        await rt.bus.publish(record.meeting_id, "spoken.answer", _answer())
        await settle()
        assert [k for k, _ in lane.dry_run.calls] == ["output_audio"]
        assert _statuses(rt, record.meeting_id) == ["queued", "playing", "played"]
    asyncio.run(go())
