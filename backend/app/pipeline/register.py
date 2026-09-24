"""
WHY THIS EXISTS
The engine lane's own hook-in point. main.py calls register(rt) once at
startup. Here the engine picks its AI provider (real or canned), opens the
documents folder, and listens on the bus for the things that do not arrive
as transcript sentences: the dashboard's wake and stop buttons, answer
status updates from the meeting lane, and the end of the meeting.

FAILURE IT PREVENTS
The engine lane needing to edit the shared entry file (CLAUDE.md rule 10),
and the health check hiding that the engine is running on canned answers:
when it is, /api/health lists "engine: canned AI provider (...why...)".
"""
from __future__ import annotations

from ..contract.context import MeetingContext
from ..core.runtime import Runtime
from ..knowledge import KnowledgeBase
from ..providers.llm import canned_reason, choose_provider
from .engine import Engine
from .entry import get_engine, set_engine


def register(rt: Runtime) -> None:
    reason = canned_reason(rt.config.offline)
    if reason:
        rt.placeholders.add(f"engine: canned AI provider ({reason})")
    set_engine(Engine(choose_provider(rt.config.offline), KnowledgeBase(rt.config.knowledge_dir)))

    # Handlers look the engine up at event time, so a test can swap in a scripted one.
    def ctx_for(meeting_id: str) -> MeetingContext:
        return MeetingContext.build(meeting_id, rt.bus, rt.store, rt.get_settings())

    async def on_wake(event) -> None:
        if event.payload.trigger == "button":  # phrase wakes are published by the engine itself
            await get_engine().on_wake_button(event.payload.question, ctx_for(event.meeting_id))

    async def on_stop(event) -> None:
        await get_engine().on_stop(event.meeting_id)

    async def on_spoken_answer(event) -> None:
        await get_engine().on_spoken_answer(event.meeting_id, event.payload.status)

    async def on_meeting_ended(event) -> None:
        await get_engine().on_meeting_ended(ctx_for(event.meeting_id))

    rt.bus.subscribe("wake", on_wake)
    rt.bus.subscribe("stop", on_stop)
    rt.bus.subscribe("spoken.answer", on_spoken_answer)
    rt.bus.subscribe("meeting.ended", on_meeting_ended)
