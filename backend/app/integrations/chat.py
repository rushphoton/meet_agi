"""
WHY THIS EXISTS
Posts the engine's chat lines (alerts and "Because you asked: ..." summaries)
into the Google Meet chat through Recall, one at a time and in order.

- It reacts only to chat.post events with status "pending" (the engine's
  initial status). The result goes out as a NEW chat.post event with the same
  chat_id: "sent", "suppressed_muted" or "failed" (DESIGN.md §4.3). Because it
  ignores every status but "pending", it never reacts to its own updates.
- While the meeting is muted, nothing is posted ("suppressed_muted").
- Once the meeting has ended, nothing is posted at all (for example after a
  failed leave_call, or after the replay ended a real meeting).
- Google Meet rejects chat messages over 500 characters (DESIGN.md §2 row 9).
  fit_chat() keeps every message under that, counting the way Meet's browser
  does (UTF-16 units, so an emoji counts as two), and cuts at a word boundary
  with "…" (risk R9).

FAILURE IT PREVENTS
Recall rejecting a long message and the alert silently vanishing; posting
while muted; a slow vendor call holding up the engine (posting runs in its
own background worker, so the engine never waits for Recall).

DEPENDENCIES (CLAUDE.md rule 4): standard library (asyncio) only.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Callable

from ..contract.events import CHAT_LIMIT, ChatPost
from .recall_client import RecallError

log = logging.getLogger("meet_agi.chat")
INITIAL_STATUS = "pending"
ELLIPSIS = "…"


def _units(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def fit_chat(text: str, limit: int = CHAT_LIMIT) -> str:
    """Return text unchanged if it fits; otherwise cut at a word boundary and end with '…'."""
    text = text.strip()
    if _units(text) <= limit:
        return text
    budget = limit - _units(ELLIPSIS)
    cut = ""
    for ch in text:
        if _units(cut + ch) > budget:
            break
        cut += ch
    head = cut.rsplit(" ", 1)[0] if " " in cut else cut
    return head.rstrip(" ,;:-") + ELLIPSIS


class ChatPoster:
    def __init__(self, bus, store, target_for: Callable[[object], tuple[object, str]]) -> None:
        self.bus, self.store, self.target_for = bus, store, target_for
        self._queues: dict[str, asyncio.Queue] = {}
        self._workers: dict[str, asyncio.Task] = {}
        self._in_flight: dict[str, int] = {}

    async def on_chat_post(self, event) -> None:
        """Subscribed to chat.post in integrations/register.py."""
        post: ChatPost = event.payload
        if post.status != INITIAL_STATUS:
            return
        queue = self._queues.get(event.meeting_id)
        if queue is None:
            queue = self._queues[event.meeting_id] = asyncio.Queue()
            self._workers[event.meeting_id] = asyncio.get_running_loop().create_task(
                self._run(event.meeting_id, queue))
        queue.put_nowait(post)

    def pending(self, meeting_id: str) -> int:
        queue = self._queues.get(meeting_id)
        return (queue.qsize() if queue else 0) + self._in_flight.get(meeting_id, 0)

    async def _run(self, meeting_id: str, queue: asyncio.Queue) -> None:
        while True:
            post = await queue.get()
            self._in_flight[meeting_id] = 1
            try:
                await self._post(meeting_id, post)
            except Exception:
                log.exception("Chat post %s failed unexpectedly", post.chat_id)
            finally:
                self._in_flight[meeting_id] = 0

    async def _post(self, meeting_id: str, post: ChatPost) -> None:
        record = self.store.get(meeting_id)
        if record is None or record.ended_at is not None:
            log.info("Chat post %s dropped: the meeting has ended", post.chat_id)
            return   # never post into a call the meeting has already left (review B, P2)
        if record.muted:
            await self._publish(meeting_id, post, "suppressed_muted", post.text)
            return
        text = fit_chat(post.text)
        target, bot_id = self.target_for(record)
        try:
            await target.send_chat(bot_id, text)
        except RecallError as exc:
            log.warning("Recall refused chat post %s: %s", post.chat_id, exc)
            await self._publish(meeting_id, post, "failed", text)
            return
        await self._publish(meeting_id, post, "sent", text)

    async def _publish(self, meeting_id: str, post: ChatPost, status: str, text: str) -> None:
        await self.bus.publish(meeting_id, "chat.post", post.model_copy(update={"status": status, "text": text}))
