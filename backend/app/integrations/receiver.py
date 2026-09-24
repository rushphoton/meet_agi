"""
WHY THIS EXISTS
Receives what Recall.ai sends while a meeting runs, at
POST PUBLIC_BASE_URL/webhooks/recall/{token}, and turns it into contract
events (DESIGN.md §3.3 "Hearing"):

1. Check the token in the address against RECALL_WEBHOOK_TOKEN in constant
   time (wrong or unset -> 401). If RECALL_WORKSPACE_SECRET is set, also try
   Recall's signature (see signature.py for why that is logged, not enforced).
2. Keep only finished lines (transcript.data) and bot status changes. Partial
   lines (transcript.partial_data) and anything else are acknowledged and
   ignored.
3. Drop repeats (Recall re-sends when we are slow): same bot, same speaker,
   same first-word time.
4. Answer "200 OK" at once; the work happens afterwards in one worker per
   meeting, so lines are handled in the order they arrived.
5. Glue pieces into finished sentences with the speaker's name (the
   Settings speaker table first, then Recall's name, then "Unknown speaker"),
   publish transcript.segment, and hand each sentence to the engine's single
   door, process_segment().
6. When the bot leaves or fails: hand on any half-finished sentence first,
   then publish meeting.ended. For a real bot, a status already reported by
   the poller in bot.py is not published twice.
7. Note the time of every accepted webhook in rt.last_webhook_at, so
   /api/health can tell a quiet room from a dead pipe (review B item 8).

FAILURE IT PREVENTS
Forged or duplicate lines; Recall giving up on us because we answered slowly
(it retries every second, 60 times, then marks the endpoint failed); the
engine seeing half-sentences or unnamed speakers; a strange payload crashing
the receiver (risk R2: our samples are synthesised from docs, not recorded).
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

from ..contract.context import MeetingContext
from ..contract.events import BotStatus, MeetingEnded, TranscriptSegment
from ..core.ids import new_id
from ..core.runtime import Runtime
from ..pipeline import entry as engine_entry   # looked up at call time: the engine lane replaces its body
from ..speech.assembler import SentenceAssembler, Sentence, Word
from .bot import publish_status
from .signature import recall_signature_valid, token_matches

log = logging.getLogger("meet_agi.receiver")

STATUS_MAP = {
    "bot.joining_call": "joining",
    "bot.in_waiting_room": "waiting_room",
    "bot.in_call_not_recording": "in_call",
    "bot.recording_permission_allowed": "in_call",
    "bot.in_call_recording": "in_call",
    "bot.call_ended": "left",
    "bot.done": "left",
    "bot.fatal": "failed",
}
FLUSH_AFTER_SILENCE_SECONDS = 1.2   # wall clock: hand on an unfinished sentence if nothing else arrives
DEDUPE_MEMORY = 5000                # remembered line keys per bot


class RecallReceiver:
    def __init__(self, rt: Runtime, flush_after: float = FLUSH_AFTER_SILENCE_SECONDS) -> None:
        self.rt = rt
        self.flush_after = flush_after
        self._seen: dict[str, dict[tuple, None]] = {}
        self._queues: dict[str, asyncio.Queue] = {}
        self._assemblers: dict[str, SentenceAssembler] = {}
        self._timers: dict[str, asyncio.TimerHandle] = {}
        self.signature_mismatches = 0

    # ---------------- the fast part: answer Recall ----------------
    async def __call__(self, token: str, headers: dict, body: dict) -> int:
        if not token_matches(token, self.rt.config.recall_webhook_token):
            return 401
        self.rt.last_webhook_at = datetime.now(timezone.utc)   # /api/health: "is the pipe alive?"
        self._check_signature(headers, body)
        if not isinstance(body, dict):
            return 200
        event_name = str(body.get("event") or "")
        data = body.get("data") if isinstance(body.get("data"), dict) else {}
        bot = data.get("bot") if isinstance(data.get("bot"), dict) else {}
        bot_id = str(bot.get("id") or "")
        if event_name != "transcript.data" and event_name not in STATUS_MAP:
            return 200  # transcript.partial_data, participant_events.*, anything new: acknowledged, ignored
        record = self.rt.store.meeting_for_bot(bot_id) if bot_id else None
        if record is None:
            log.warning("Webhook %s for unknown bot ignored", event_name)
            return 200  # a 2xx stops Recall retrying something we will never accept
        if event_name == "transcript.data":
            key = _dedupe_key(data)
            if key is None:
                log.warning("transcript.data without words or speaker ignored (payload shape changed? risk R2)")
                return 200
            seen = self._seen.setdefault(bot_id, {})
            if key in seen:
                return 200
            seen[key] = None
            if len(seen) > DEDUPE_MEMORY:
                seen.pop(next(iter(seen)))
        self._queue_for(record.meeting_id).put_nowait((event_name, data))
        return 200

    def _check_signature(self, headers: dict, body: dict) -> None:
        secret = self.rt.config.recall_workspace_secret
        if not secret:
            return
        raw = json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode()
        if not recall_signature_valid(raw, headers, secret):
            self.signature_mismatches += 1
            log.warning("Recall signature not confirmed (checked against re-serialised JSON; "
                        "the path token was valid, so the webhook is accepted)")

    # ---------------- the slow part: one worker per meeting ----------------
    def _queue_for(self, meeting_id: str) -> asyncio.Queue:
        if meeting_id not in self._queues:
            queue: asyncio.Queue = asyncio.Queue()
            self._queues[meeting_id] = queue
            asyncio.get_running_loop().create_task(self._worker(meeting_id, queue))
        return self._queues[meeting_id]

    async def _worker(self, meeting_id: str, queue: asyncio.Queue) -> None:
        while True:
            event_name, data = await queue.get()
            try:
                await self._handle(meeting_id, event_name, data)
            except Exception:
                log.exception("Failed handling %s for %s", event_name, meeting_id)

    def pending(self, meeting_id: str) -> int:
        queue = self._queues.get(meeting_id)
        return queue.qsize() if queue else 0

    async def _handle(self, meeting_id: str, event_name: str, data: dict) -> None:
        if event_name == "flush":
            await self._emit(meeting_id, self._assembler(meeting_id).flush())
            return
        if event_name in STATUS_MAP:
            await self._status(meeting_id, STATUS_MAP[event_name], data)
            return
        inner = data.get("data") or {}
        participant = inner.get("participant") or {}
        words = [Word(text=str(w.get("text") or ""),
                      start=_rel(w.get("start_timestamp")),
                      end=_rel(w.get("end_timestamp") or w.get("start_timestamp")))
                 for w in inner.get("words") or [] if isinstance(w, dict)]
        speaker_id = str(participant.get("id"))
        sentences = self._assembler(meeting_id).add(speaker_id, self._speaker_name(participant), words)
        await self._emit(meeting_id, sentences)
        self._arm_flush_timer(meeting_id)

    async def _status(self, meeting_id: str, status: str, data: dict) -> None:
        if status in ("left", "failed"):
            await self._emit(meeting_id, self._assembler(meeting_id).flush())
        record = self.rt.store.get(meeting_id)
        detail = (data.get("data") or {}).get("sub_code")
        bot_id = str((data.get("bot") or {}).get("id") or "") or None
        if record is not None and record.source == "recall":
            # the poller (bot.py) may have reported this already: publish only a change
            await publish_status(self.rt, meeting_id, status, detail, bot_id,
                                 "call_ended" if status in ("left", "failed") else None)
            return
        # the fake (replay) meeting: unchanged since milestone 0
        await self.rt.bus.publish(meeting_id, "bot.status", BotStatus(
            status=status, detail=detail, recall_bot_id=bot_id))
        record = self.rt.store.get(meeting_id)
        if status in ("left", "failed") and record and record.ended_at is None:
            await self.rt.bus.publish(meeting_id, "meeting.ended", MeetingEnded(reason="replay_finished"))

    async def _emit(self, meeting_id: str, sentences: list[Sentence]) -> None:
        record = self.rt.store.get(meeting_id)
        source = "replay" if record and record.source == "replay" else "recall"
        for s in sentences:
            segment = TranscriptSegment(
                segment_id=new_id("seg"), speaker_id=s.speaker_id, speaker_name=s.speaker_name,
                text=s.text, t_start=s.t_start, t_end=s.t_end, source=source)
            await self.rt.bus.publish(meeting_id, "transcript.segment", segment)
            ctx = MeetingContext.build(meeting_id, self.rt.bus, self.rt.store, self.rt.get_settings())
            await engine_entry.process_segment(segment, ctx)

    # ---------------- helpers ----------------
    def _assembler(self, meeting_id: str) -> SentenceAssembler:
        if meeting_id not in self._assemblers:
            self._assemblers[meeting_id] = SentenceAssembler()
        return self._assemblers[meeting_id]

    def _arm_flush_timer(self, meeting_id: str) -> None:
        old = self._timers.pop(meeting_id, None)
        if old:
            old.cancel()
        if self._assembler(meeting_id).has_pending:
            queue = self._queues[meeting_id]
            self._timers[meeting_id] = asyncio.get_running_loop().call_later(
                self.flush_after, queue.put_nowait, ("flush", {}))

    def _speaker_name(self, participant: dict) -> str:
        recall_name = str(participant.get("name") or "").strip()
        for mapping in self.rt.get_settings().speakers:
            if recall_name and mapping.match_name.strip().lower() == recall_name.lower():
                return mapping.display_name
        return recall_name or "Unknown speaker"


def _rel(stamp) -> float:
    try:
        return float((stamp or {}).get("relative") or 0.0)
    except (TypeError, ValueError, AttributeError):
        return 0.0


def _dedupe_key(data: dict) -> tuple | None:
    inner = data.get("data") if isinstance(data.get("data"), dict) else {}
    words = inner.get("words") if isinstance(inner.get("words"), list) else []
    participant = inner.get("participant") if isinstance(inner.get("participant"), dict) else {}
    if not words or not isinstance(words[0], dict) or participant.get("id") is None:
        return None
    return (str(participant.get("id")), _rel(words[0].get("start_timestamp")))
