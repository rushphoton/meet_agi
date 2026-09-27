"""
WHY THIS EXISTS
"The mouth" (DESIGN.md §3.1 desk 4). It listens on the event bus and:

- on `wake`: plays a cached filler line at once ("Sure, let me look that up.");
- on `spoken.answer` with status "queued": cuts the answer into sentences
  (speech/sentences.py), starts turning the FIRST sentence into speech at
  once and puts the answer in line;
- plays ONE clip at a time: it sends a clip to the meeting, then waits for
  that clip's own length before sending the next. An answer is a chain of
  sentence clips: while one sentence plays, the next is being synthesised,
  so a long answer starts as fast as a short one;
- on `stop` ("stop talking" or the stop button): throws away everything in
  line, including the sentences of the current answer not yet sent, cuts the
  clip that is playing where the vendor can (Recall DELETE output_audio;
  Attendee has no such call, so at most the current SENTENCE finishes), and
  marks those answers "stopped". What was actually said is logged;
- on `mute`: the same, marking them "muted"; while muted nothing is played;
- on `meeting.ended`: throws away whatever is left; nothing is ever played
  into a meeting that has ended.
- if the voice fails during a real call: plays nothing (never the canned
  clip), marks the answer "failed" and skips fillers while the voice is down.

Each answer's progress is published as a NEW spoken.answer event with the
same answer_id and a new status ("playing", "played", "stopped", "muted",
"failed") - DESIGN.md §4.3. It reacts only to status "queued" (the engine's
initial status), so it never reacts to its own updates.

The fake meeting (DRY RUN target) and OFFLINE=1 use the canned clip, whose
audio is the same whatever the text, so there an answer stays ONE clip.

FAILURE IT PREVENTS
Two answers talking over each other; the bot speaking while muted or after
being told to stop; one failed clip blocking every clip after it; and (live
test 27 Sep 2026) the bot finishing a whole 20-40 s answer on Attendee after
"stop talking", because the answer was one clip that could not be cut.

DEPENDENCIES (CLAUDE.md rule 4): standard library (asyncio) only.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from ..contract.events import SpokenAnswer
from ..integrations.recall_client import RecallError
from ..providers.voice import VoiceClip, VoiceService
from .fillers import FillerBank
from .sentences import split_sentences

log = logging.getLogger("meet_agi.audio")
PLAYBACK_MARGIN_SECONDS = 0.25   # small gap so Recall has finished one clip before the next arrives
INITIAL_STATUS = "queued"


@dataclass(eq=False)
class _Item:
    clip_task: asyncio.Task                # the first (or only) clip, started the moment it was queued
    answer: SpokenAnswer | None = None     # None for a filler line
    sentences: list[str] = field(default_factory=list)   # an answer's clips, in order (empty for a filler)
    next_task: asyncio.Task | None = None  # the next sentence, synthesised while the current one plays
    sent: list[str] = field(default_factory=list)        # sentences already handed to the meeting
    dropped: bool = False
    finished: bool = False

    def cancel_clips(self) -> None:
        for task in (self.clip_task, self.next_task):
            if task is not None:
                task.cancel()


@dataclass(eq=False)
class _MeetingAudio:
    meeting_id: str
    target: object
    bot_id: str
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    current: _Item | None = None
    wait_task: asyncio.Task | None = None
    worker: asyncio.Task | None = None
    closed: bool = False
    played: list[str] = field(default_factory=list)   # "filler" / answer_id, in the order played

    @property
    def idle(self) -> bool:
        return self.current is None and self.queue.empty()


class AudioOut:
    def __init__(self, bus, store, get_settings, voice: VoiceService, fillers: FillerBank,
                 target_for: Callable[[object], tuple[object, str]],
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> None:
        self.bus, self.store, self.get_settings = bus, store, get_settings
        self.voice, self.fillers, self.target_for, self.sleep = voice, fillers, target_for, sleep
        self._meetings: dict[str, _MeetingAudio] = {}

    # ------------- bus handlers (subscribed in integrations/register.py) -------------
    async def on_wake(self, event) -> None:
        if self._muted(event.meeting_id) or self._ended(event.meeting_id):
            return
        m = self._meeting(event.meeting_id)
        if self.voice.failing and not m.target.is_dry_run:
            return  # voice is down: no filler, no 8 s wait - the answer goes to chat (review B item 6)
        s = self.get_settings()
        line = self.fillers.next_line(s.fillers)
        task = asyncio.get_running_loop().create_task(self.fillers.clip(
            line, s.voice.voice_id, s.voice.model, allow_vendor=not m.target.is_dry_run))
        m.queue.put_nowait(_Item(clip_task=task))

    async def on_spoken_answer(self, event) -> None:
        answer: SpokenAnswer = event.payload
        if answer.status != INITIAL_STATUS:
            return  # our own status updates, or an answer the engine already marked muted
        if self._muted(event.meeting_id):
            await self._publish(event.meeting_id, answer, "muted")
            return
        if self._ended(event.meeting_id):
            await self._publish(event.meeting_id, answer, "stopped")
            return
        m = self._meeting(event.meeting_id)
        if m.target.is_dry_run or self.voice.offline:
            sentences = [answer.text]      # canned clip: the same audio whatever the text, so one clip
        else:
            sentences = split_sentences(answer.text) or [answer.text]
        item = _Item(clip_task=self._synthesize(m, sentences[0]), answer=answer, sentences=sentences)
        m.queue.put_nowait(item)

    async def on_stop(self, event) -> None:
        await self.discard(event.meeting_id, "stopped", cut_audio=True)

    async def on_mute(self, event) -> None:
        if event.payload.muted:
            await self.discard(event.meeting_id, "muted", cut_audio=True)

    async def on_meeting_ended(self, event) -> None:
        await self.discard(event.meeting_id, "stopped", cut_audio=False)
        m = self._meetings.get(event.meeting_id)
        if m:
            m.closed = True
            if m.worker:
                m.worker.cancel()

    # ------------- queue mechanics -------------
    async def discard(self, meeting_id: str, status: str, cut_audio: bool) -> None:
        m = self._meetings.get(meeting_id)
        if m is None:
            return
        dropped: list[_Item] = []
        if m.current is not None and not m.current.dropped and not m.current.finished:
            dropped.append(m.current)
        while not m.queue.empty():
            dropped.append(m.queue.get_nowait())
        for item in dropped:      # synchronous: nothing more can be sent from here on
            item.dropped = True
            item.cancel_clips()
        if m.wait_task is not None:
            m.wait_task.cancel()
        if cut_audio:
            await self._cut(m)
        for item in dropped:
            if item.answer is not None:
                self._log_cut(meeting_id, item, status)
                await self._publish(meeting_id, item.answer, status)

    def meeting_state(self, meeting_id: str) -> _MeetingAudio | None:
        return self._meetings.get(meeting_id)

    def _meeting(self, meeting_id: str) -> _MeetingAudio:
        m = self._meetings.get(meeting_id)
        if m is None or m.closed:
            target, bot_id = self.target_for(self.store.get(meeting_id))
            m = _MeetingAudio(meeting_id=meeting_id, target=target, bot_id=bot_id)
            self._meetings[meeting_id] = m
            m.worker = asyncio.get_running_loop().create_task(self._run(m))
        return m

    async def _run(self, m: _MeetingAudio) -> None:
        while not m.closed:
            item = await m.queue.get()
            if item.dropped:
                continue
            m.current = item
            try:
                await self._play(m, item)
            except asyncio.CancelledError:
                raise
            except Exception:  # one bad clip must not stop the clips after it
                log.exception("Playing a clip failed in %s", m.meeting_id)
            finally:
                item.finished = True
                m.current = None
                m.wait_task = None

    def _synthesize(self, m: _MeetingAudio, text: str) -> asyncio.Task:
        s = self.get_settings()
        return asyncio.get_running_loop().create_task(self.voice.synthesize(
            text, voice_id=s.voice.voice_id, model_id=s.voice.model, allow_vendor=not m.target.is_dry_run))

    async def _play(self, m: _MeetingAudio, item: _Item) -> None:
        if item.answer is None:
            await self._play_filler(m, item)
            return
        for i, sentence in enumerate(item.sentences):
            task = item.clip_task if i == 0 else item.next_task
            await asyncio.wait({task})
            if item.dropped or task.cancelled():
                return
            if task.exception() is not None:   # real call, voice failing: play nothing more, never canned
                self._log_cut(m.meeting_id, item, "failed")
                await self._publish(m.meeting_id, item.answer, "failed")
                return
            clip: VoiceClip = task.result()
            if self._ended(m.meeting_id):
                await self._publish(m.meeting_id, item.answer, "stopped")
                return
            if self._muted(m.meeting_id):
                item.dropped = True
                await self._publish(m.meeting_id, item.answer, "muted")
                return
            if i == 0:
                await self._publish(m.meeting_id, item.answer, "playing")
                if item.dropped:      # stop or mute arrived while "playing" was being announced
                    return
            if i + 1 < len(item.sentences):   # the next sentence is synthesised while this one plays
                item.next_task = self._synthesize(m, item.sentences[i + 1])
            if not await self._send(m, item, clip):
                return
            item.sent.append(sentence)
            if item.dropped:          # stop arrived while the clip was being sent
                await self._cut(m)
                return
            await self._wait_clip(m, clip)
            if item.dropped:
                return
        await self._publish(m.meeting_id, item.answer, "played")

    async def _play_filler(self, m: _MeetingAudio, item: _Item) -> None:
        await asyncio.wait({item.clip_task})
        if item.dropped or item.clip_task.cancelled() or item.clip_task.exception() is not None:
            return                    # a failing voice in a real call: no filler
        if self._ended(m.meeting_id) or self._muted(m.meeting_id):
            return
        if not await self._send(m, item, item.clip_task.result()):
            return
        if item.dropped:
            await self._cut(m)
            return
        await self._wait_clip(m, item.clip_task.result())

    async def _send(self, m: _MeetingAudio, item: _Item, clip: VoiceClip) -> bool:
        try:
            await m.target.output_audio(m.bot_id, clip.b64)
        except RecallError as exc:
            log.warning("The bot vendor refused a clip for %s: %s", m.meeting_id, exc)
            if item.answer is not None:
                if item.next_task is not None:
                    item.next_task.cancel()
                self._log_cut(m.meeting_id, item, "failed")
                await self._publish(m.meeting_id, item.answer, "failed")
            return False
        m.played.append(item.answer.answer_id if item.answer else "filler")
        return True

    async def _wait_clip(self, m: _MeetingAudio, clip: VoiceClip) -> None:
        if m.target.is_dry_run:       # the fake meeting has nothing to wait for
            return
        m.wait_task = asyncio.get_running_loop().create_task(self.sleep(clip.duration + PLAYBACK_MARGIN_SECONDS))
        await asyncio.wait({m.wait_task})

    def _log_cut(self, meeting_id: str, item: _Item, status: str) -> None:
        """The contract has no field for "words spoken so far", so it goes to the log: the
        sentences handed to the meeting (finished ones plus the one playing when cut)."""
        spoken = " ".join(item.sent)
        log.info("Answer %s %s in %s after %d of %d sentence(s); spoken so far (%d words): %r",
                 item.answer.answer_id, status, meeting_id, len(item.sent), len(item.sentences),
                 len(spoken.split()), spoken)

    async def _cut(self, m: _MeetingAudio) -> None:
        try:
            await m.target.stop_audio(m.bot_id)
        except RecallError as exc:
            log.warning("Recall could not stop audio for %s: %s", m.meeting_id, exc)

    async def _publish(self, meeting_id: str, answer: SpokenAnswer, status: str) -> None:
        await self.bus.publish(meeting_id, "spoken.answer", answer.model_copy(update={"status": status}))

    def _muted(self, meeting_id: str) -> bool:
        record = self.store.get(meeting_id)
        return bool(record and record.muted)

    def _ended(self, meeting_id: str) -> bool:
        record = self.store.get(meeting_id)
        return record is None or record.ended_at is not None
