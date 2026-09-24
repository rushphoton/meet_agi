"""
WHY THIS EXISTS
Receives what Recall.ai sends while a meeting runs (finished transcript
lines, bot status changes) at POST /webhooks/recall/{token}, and turns them
into contract events.

FAILURE IT PREVENTS
- Forged or stray requests: the token in the URL is compared in constant time.
- Recall re-sending the same line (it retries every second on slow replies):
  we answer immediately and do the work afterwards, and drop duplicates.
- Lines processed out of order: one worker per meeting handles them in turn.

THIS FILE IS A PLACEHOLDER (milestone 0). It treats each Recall utterance as
one sentence (no sentence assembly, no speaker-name mapping, no signature
check) - enough for the fake meeting. The meeting lane replaces it.
"""
from __future__ import annotations

import asyncio
import hmac
import logging

from ..contract.context import MeetingContext
from ..contract.events import BotStatus, MeetingEnded, TranscriptSegment
from ..core.ids import new_id
from ..core.runtime import Runtime
from ..pipeline.entry import process_segment

log = logging.getLogger("meet_agi.receiver")

STATUS_MAP = {
    "bot.joining_call": "joining",
    "bot.in_waiting_room": "waiting_room",
    "bot.in_call_not_recording": "in_call",
    "bot.in_call_recording": "in_call",
    "bot.call_ended": "left",
    "bot.done": "left",
    "bot.fatal": "failed",
}


class PlaceholderReceiver:
    def __init__(self, rt: Runtime) -> None:
        self.rt = rt
        self._seen: set[tuple] = set()
        self._queues: dict[str, asyncio.Queue] = {}

    async def __call__(self, token: str, headers: dict, body: dict) -> int:
        expected = self.rt.config.recall_webhook_token
        if not expected or not hmac.compare_digest(token.encode(), expected.encode()):
            return 401
        event_name = body.get("event", "")
        bot_id = ((body.get("data") or {}).get("bot") or {}).get("id")
        record = self.rt.store.meeting_for_bot(bot_id) if bot_id else None
        if record is None:
            log.warning("Webhook for unknown bot %r (%s) ignored", bot_id, event_name)
            return 200  # a 2xx stops Recall retrying something we will never accept
        if event_name == "transcript.data":
            key = _dedupe_key(bot_id, body)
            if key in self._seen:
                return 200
            self._seen.add(key)
        elif event_name not in STATUS_MAP:
            return 200  # e.g. transcript.partial_data - we never act on partials
        self._queue_for(record.meeting_id).put_nowait((event_name, body))
        return 200

    def _queue_for(self, meeting_id: str) -> asyncio.Queue:
        if meeting_id not in self._queues:
            queue: asyncio.Queue = asyncio.Queue()
            self._queues[meeting_id] = queue
            asyncio.get_running_loop().create_task(self._worker(meeting_id, queue))
        return self._queues[meeting_id]

    async def _worker(self, meeting_id: str, queue: asyncio.Queue) -> None:
        while True:
            event_name, body = await queue.get()
            try:
                await self._handle(meeting_id, event_name, body)
            except Exception:
                log.exception("Failed handling %s for %s", event_name, meeting_id)

    async def _handle(self, meeting_id: str, event_name: str, body: dict) -> None:
        inner = body["data"]
        if event_name in STATUS_MAP:
            status = STATUS_MAP[event_name]
            await self.rt.bus.publish(meeting_id, "bot.status", BotStatus(
                status=status, detail=(inner.get("data") or {}).get("sub_code"),
                recall_bot_id=inner["bot"]["id"]))
            record = self.rt.store.get(meeting_id)
            if status in ("left", "failed") and record and record.ended_at is None:
                await self.rt.bus.publish(meeting_id, "meeting.ended", MeetingEnded(
                    reason="replay_finished" if record.source == "replay" else "call_ended"))
            return
        words = inner["data"]["words"]
        participant = inner["data"]["participant"]
        record = self.rt.store.get(meeting_id)
        segment = TranscriptSegment(
            segment_id=new_id("seg"),
            speaker_id=str(participant["id"]),
            speaker_name=participant.get("name") or "Unknown speaker",
            text=" ".join(w["text"] for w in words).strip(),
            t_start=float(words[0]["start_timestamp"]["relative"]),
            t_end=float((words[-1].get("end_timestamp") or words[-1]["start_timestamp"])["relative"]),
            source="replay" if record and record.source == "replay" else "recall",
        )
        await self.rt.bus.publish(meeting_id, "transcript.segment", segment)
        ctx = MeetingContext.build(meeting_id, self.rt.bus, self.rt.store, self.rt.get_settings())
        await process_segment(segment, ctx)


def _dedupe_key(bot_id: str, body: dict) -> tuple:
    data = body["data"]["data"]
    first = data["words"][0]["start_timestamp"]["relative"] if data.get("words") else None
    return (bot_id, data.get("participant", {}).get("id"), first)
