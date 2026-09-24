"""
WHY THIS EXISTS
"The mouth" (DESIGN.md §3.1 desk 4). It listens on the event bus and:

- on `wake`: plays a cached filler line at once ("Sure, let me look that up.");
- on `spoken.answer` with status "queued": turns the answer into speech and
  puts it in line;
- plays ONE clip at a time: it sends a clip to the meeting, then waits for
  that clip's own length before sending the next;
- on `stop` ("AGI, stop talking" or the stop button): throws away everything
  in line, cuts the clip that is playing (Recall DELETE output_audio), and
  marks those answers "stopped";
- on `mute`: the same, marking them "muted"; while muted nothing is played;
- on `meeting.ended`: throws away whatever is left.

Each answer's progress is published as a NEW spoken.answer event with the
same answer_id and a new status ("playing", "played", "stopped", "muted",
"failed") - DESIGN.md §4.3. It reacts only to status "queued" (the engine's
initial status), so it never reacts to its own updates.

FAILURE IT PREVENTS
Two answers talking over each other; the bot speaking while muted or after
being told to stop; one failed clip blocking every clip after it.

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

log = logging.getLogger("meet_agi.audio")
PLAYBACK_MARGIN_SECONDS = 0.25   # small gap so Recall has finished one clip before the next arrives
INITIAL_STATUS = "queued"


@dataclass(eq=False)
class _Item:
    clip_task: asyncio.Task
    answer: SpokenAnswer | None = None     # None for a filler line
    dropped: bool = False
    finished: bool = False


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
        s = self.get_settings()
        task = asyncio.get_running_loop().create_task(self.voice.synthesize(
            answer.text, voice_id=s.voice.voice_id, model_id=s.voice.model,
            allow_vendor=not m.target.is_dry_run))
        m.queue.put_nowait(_Item(clip_task=task, answer=answer))

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
        for item in dropped:
            item.dropped = True
            item.clip_task.cancel()
        if m.wait_task is not None:
            m.wait_task.cancel()
        if cut_audio:
            await self._cut(m)
        for item in dropped:
            if item.answer is not None:
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

    async def _play(self, m: _MeetingAudio, item: _Item) -> None:
        await asyncio.wait({item.clip_task})
        if item.dropped or item.clip_task.cancelled():
            return
        clip: VoiceClip = item.clip_task.result()
        if self._muted(m.meeting_id):
            item.dropped = True
            if item.answer is not None:
                await self._publish(m.meeting_id, item.answer, "muted")
            return
        if item.answer is not None:
            await self._publish(m.meeting_id, item.answer, "playing")
        if item.dropped:          # stop or mute arrived while "playing" was being announced
            return
        try:
            await m.target.output_audio(m.bot_id, clip.b64)
        except RecallError as exc:
            log.warning("Recall refused a clip for %s: %s", m.meeting_id, exc)
            if item.answer is not None:
                await self._publish(m.meeting_id, item.answer, "failed")
            return
        m.played.append(item.answer.answer_id if item.answer else "filler")
        if item.dropped:          # stop arrived while the clip was being sent
            await self._cut(m)
            return
        if not m.target.is_dry_run:   # the fake meeting has nothing to wait for
            m.wait_task = asyncio.get_running_loop().create_task(
                self.sleep(clip.duration + PLAYBACK_MARGIN_SECONDS))
            await asyncio.wait({m.wait_task})
        if item.dropped:
            return
        if item.answer is not None:
            await self._publish(m.meeting_id, item.answer, "played")

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
