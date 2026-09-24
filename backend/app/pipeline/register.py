"""
WHY THIS EXISTS
The engine lane's own hook-in point. main.py calls register(rt) once at
startup; the engine subscribes to the bus events it cares about here (manual
wake button, meeting end) without anyone editing main.py.

FAILURE IT PREVENTS
The engine lane needing to edit the shared entry file (CLAUDE.md rule 10).

PLACEHOLDER (milestone 0): on the manual wake button it gives the CANNED
answer; at meeting end it publishes one CANNED summary. The engine lane
replaces this body and keeps the function name and signature.
"""
from __future__ import annotations

from ..contract.context import MeetingContext
from ..contract.events import MeetingSummary
from ..core.runtime import Runtime
from .entry import _canned_answer


def register(rt: Runtime) -> None:
    rt.placeholders.add("engine (canned alert/answer/summary)")

    async def on_wake(event) -> None:
        if event.payload.trigger != "button":
            return  # phrase wakes are handled inside process_segment
        ctx = MeetingContext.build(event.meeting_id, rt.bus, rt.store, rt.get_settings())
        await _canned_answer(ctx, event.payload.question or "(manual wake, no question)", None)

    async def on_meeting_ended(event) -> None:
        record = rt.store.get(event.meeting_id)
        if record is None or record.summary is not None:
            return
        await rt.bus.publish(event.meeting_id, "meeting.summary", MeetingSummary(
            key_topics=["CANNED: Q3 revenue", "CANNED: Q4 pipeline"],
            takeaways=["CANNED: Q3 revenue fell 4% per the board deck, not rose.",
                       "CANNED placeholder summary - no model was called."],
            follow_up_count=0,
            alert_count=sum(not a.gated for a in record.alerts),
            canned=True))

    rt.bus.subscribe("wake", on_wake)
    rt.bus.subscribe("meeting.ended", on_meeting_ended)
