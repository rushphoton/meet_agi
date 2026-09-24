"""
WHY THIS EXISTS
The in-process event bus: the shared in-tray every lane publishes to and
subscribes from. Each published event gets the next sequence number for its
meeting, is applied to the store (and so written to disk), is pushed to any
open live-stream connections, and is handed to subscribers.

FAILURE IT PREVENTS
Lanes calling each other directly (tight coupling that breaks on merge), and
the dashboard missing or double-counting events (seq numbers let it resume).

Subscribers are awaited one after another in the order they subscribed. A
subscriber that raises is logged and skipped, so one broken lane cannot stop
the others. A subscriber that needs slow work (an LLM call) should start its
own task rather than block the publisher.

DEPENDENCIES (CLAUDE.md rule 4): Python standard library only.
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Awaitable, Callable

from pydantic import BaseModel, TypeAdapter

from ..contract.events import EVENT_PAYLOADS, Event
from .ids import new_id

log = logging.getLogger("meet_agi.bus")
Handler = Callable[[object], Awaitable[None]]
_event_adapter = TypeAdapter(Event)


class EventBus:
    def __init__(self, store) -> None:
        self.store = store
        self._subs: dict[str, list[Handler]] = defaultdict(list)
        self._listeners: dict[str, set[asyncio.Queue]] = defaultdict(set)

    def subscribe(self, event_type: str, handler: Handler) -> None:
        """event_type is one of the contract's types, or "*" for all."""
        if event_type != "*" and event_type not in EVENT_PAYLOADS:
            raise ValueError(f"Unknown event type {event_type!r}; the list is closed (DESIGN.md §4.3)")
        self._subs[event_type].append(handler)

    async def publish(self, meeting_id: str, event_type: str, payload: BaseModel | dict):
        if event_type not in EVENT_PAYLOADS:
            raise ValueError(f"Unknown event type {event_type!r}")
        payload_model, _ = EVENT_PAYLOADS[event_type]
        if isinstance(payload, BaseModel):
            payload = payload.model_dump()
        payload_model.model_validate(payload)  # fail loudly on a malformed payload
        event = _event_adapter.validate_python({
            "id": new_id("evt"),
            "meeting_id": meeting_id,
            "seq": self.store.next_seq(meeting_id),
            "at": datetime.now(timezone.utc),
            "type": event_type,
            "payload": payload,
        })
        self.store.apply(event)
        for queue in list(self._listeners[meeting_id]):
            queue.put_nowait(event)
        for handler in list(self._subs[event_type]) + list(self._subs["*"]):
            try:
                await handler(event)
            except Exception:  # one broken lane must not take down the others
                log.exception("Subscriber %r failed on %s", handler, event_type)
        return event

    def listen(self, meeting_id: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        self._listeners[meeting_id].add(queue)
        return queue

    def unlisten(self, meeting_id: str, queue: asyncio.Queue) -> None:
        self._listeners[meeting_id].discard(queue)
