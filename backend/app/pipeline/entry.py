"""
WHY THIS EXISTS
The one door every finished transcript sentence enters the engine by. Its
name and signature are FROZEN by the contract (DESIGN.md §4.4):

    async def process_segment(segment: TranscriptSegment, ctx: MeetingContext) -> None

The engine answers only by publishing events on ctx.bus. The real work is
in engine.py; this file only finds the running engine and hands it the
sentence.

FAILURE IT PREVENTS
The meeting lane and the engine lane drifting apart on how a sentence is
handed over; the meeting lane builds against this signature blind. And a
crash inside the engine stopping the transcript: any error is logged and
swallowed here, so the next sentence still arrives.
"""
from __future__ import annotations

import logging

from ..contract.context import MeetingContext
from ..contract.events import TranscriptSegment

log = logging.getLogger("meet_agi.engine")

_engine = None  # set by register.py at startup; built on first use otherwise


def set_engine(engine) -> None:
    global _engine
    _engine = engine


def get_engine():
    global _engine
    if _engine is None:
        from ..knowledge import KnowledgeBase
        from ..providers.llm import choose_provider
        from ..settings import load_config
        from .engine import Engine
        config = load_config()
        _engine = Engine(choose_provider(config.offline), KnowledgeBase(config.knowledge_dir))
    return _engine


async def process_segment(segment: TranscriptSegment, ctx: MeetingContext) -> None:
    try:
        await get_engine().on_segment(segment, ctx)
    except Exception:
        log.exception("Engine failed on segment %s; continuing with the next one", segment.segment_id)
