"""
WHY THIS EXISTS
MeetingContext is what the engine's frozen entry point receives alongside each
transcript segment: which meeting, the current settings, and the bus and store
to publish to. It is part of the contract (DESIGN.md §4.4).

FAILURE IT PREVENTS
Lanes reaching into each other's internals or global state to find the bus,
which breaks the moment two lanes are merged.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .records import Settings

if TYPE_CHECKING:  # avoid an import cycle; these are runtime objects, not data shapes
    from ..core.bus import EventBus
    from ..core.store import Store


@dataclass
class MeetingContext:
    meeting_id: str
    settings: Settings
    bus: "EventBus"
    store: "Store"
    muted: bool = False
    speech_mode: bool = False

    @classmethod
    def build(cls, meeting_id: str, bus: Any, store: Any, settings: Settings) -> "MeetingContext":
        record = store.get(meeting_id)
        return cls(meeting_id=meeting_id, settings=settings, bus=bus, store=store,
                   muted=bool(record and record.muted))
