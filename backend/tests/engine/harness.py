"""
WHY THIS EXISTS
Test helpers for the engine lane: a real event bus and store in a temporary
folder, the real engine, and a *scripted* AI provider whose every reply (or
failure, or slowness) the test decides. Nothing here calls a vendor.

FAILURE IT PREVENTS
Engine tests that only pass because a real model happened to answer well,
or that cost money every time the test hook runs.
"""
from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from backend.app.contract.context import MeetingContext
from backend.app.contract.events import MeetingEnded, TranscriptSegment
from backend.app.contract.records import Settings
from backend.app.core.bus import EventBus
from backend.app.core.ids import new_id
from backend.app.core.store import Store
from backend.app.knowledge import KnowledgeBase
from backend.app.pipeline.engine import Engine
from backend.app.providers.llm.base import (
    AnswerDraft, CheapCheck, LLMError, SummaryDraft, Verdict,
)

ROOT = Path(__file__).resolve().parents[3]
SAMPLE_DOC = ROOT / "knowledge" / "SAMPLE_board_deck_q3.md"


@dataclass
class Scripted:
    """A provider whose behaviour each test sets. Callables get the same args as the real one."""
    cheap: object = None          # callable(lines) -> CheapCheck, or an Exception to raise
    verdict: object = None        # callable(lines) -> Verdict, or an Exception
    answer_text: str = "The board deck says Q3 revenue was 41.2 million dollars, down 4 percent."
    answer_error: Exception | None = None
    answer_delay: float = 0.0
    judge_delay: float = 0.0
    cheap_delay: float = 0.0
    new_lines_seen: list = field(default_factory=list)
    summary_error: Exception | None = None
    calls: dict = field(default_factory=lambda: {"cheap": 0, "judge": 0, "answer": 0, "summary": 0})
    name: str = "scripted"
    canned: bool = False

    async def cheap_check(self, model, lines, passages, already_flagged, new_lines=1):
        self.calls["cheap"] += 1
        self.new_lines_seen.append(new_lines)
        if self.cheap_delay:
            await asyncio.sleep(self.cheap_delay)
        if isinstance(self.cheap, Exception):
            raise self.cheap
        if self.cheap is None:
            return CheapCheck(False, 0.0, "", "cheap-model")
        return self.cheap(lines, new_lines)

    async def judge(self, model, lines, passages, already_flagged, new_lines=1):
        self.calls["judge"] += 1
        if self.judge_delay:
            await asyncio.sleep(self.judge_delay)
        if isinstance(self.verdict, Exception):
            raise self.verdict
        if self.verdict is None:
            return Verdict(is_issue=False, model="judge-model")
        return self.verdict(lines, new_lines)

    async def answer(self, model, question, asked_by, passages, max_words):
        self.calls["answer"] += 1
        if self.answer_delay:
            await asyncio.sleep(self.answer_delay)
        if self.answer_error:
            raise self.answer_error
        return AnswerDraft(spoken=self.answer_text, chat_line="Q3 revenue was $41.2M, down 4% (board deck).",
                           passage_indexes=[0] if passages else [], model="answer-model")

    async def summarize(self, model, segments, alerts):
        self.calls["summary"] += 1
        if self.summary_error:
            raise self.summary_error
        return SummaryDraft(key_topics=["Q3 revenue"], takeaways=["Revenue fell 4%."], action_items=[],
                            unsettled_alert_ids=[a.alert_id for a in alerts], model="summary-model")


def flag_when(word: str, score: float = 0.9):
    """Cheap check that flags when any NEW line contains `word`."""
    def check(lines, new_lines=1):
        hit = any(word in l.text.lower() for l in lines[-new_lines:])
        return CheapCheck(hit, score if hit else 0.0, word, "cheap-model")
    return check


def verdict(confidence: float = 0.9, topic: str = "Q3 revenue was rising", finding: str | None = None):
    def make(lines, new_lines=1):
        return Verdict(is_issue=True, kind="contradiction", topic=topic, claim=lines[-1].text,
                       said_by=[lines[-1].speaker_name], segment_indexes=[len(lines) - 1],
                       finding=finding or "the board deck says Q3 revenue fell 4%.",
                       reasoning="Full reasoning for the dashboard.", confidence=confidence,
                       passage_indexes=[0], model="judge-model")
    return make


class Harness:
    def __init__(self, tmp: Path, provider, settings: Settings | None = None, with_docs: bool = True):
        docs = tmp / "knowledge"
        docs.mkdir(parents=True, exist_ok=True)
        if with_docs:
            shutil.copy(SAMPLE_DOC, docs / SAMPLE_DOC.name)
        self.store = Store(tmp / "data")
        self.bus = EventBus(self.store)
        self.settings = settings or Settings()
        self.provider = provider
        self.engine = Engine(provider, KnowledgeBase(docs))
        self.meeting_id = self.store.create_meeting("test", "replay").meeting_id
        self.t = 0.0
        # the same subscriptions register.py makes
        self.bus.subscribe("wake", self._on_wake)
        self.bus.subscribe("stop", lambda e: self.engine.on_stop(e.meeting_id))
        self.bus.subscribe("spoken.answer", lambda e: self.engine.on_spoken_answer(e.meeting_id, e.payload.status))
        self.bus.subscribe("meeting.ended", lambda e: self.engine.on_meeting_ended(self.ctx()))

    async def _on_wake(self, event):
        if event.payload.trigger == "button":
            await self.engine.on_wake_button(event.payload.question, self.ctx())

    def ctx(self) -> MeetingContext:
        return MeetingContext.build(self.meeting_id, self.bus, self.store, self.settings)

    async def say(self, speaker: str, text: str, at: float | None = None, speaker_id: str | None = None):
        self.t = at if at is not None else self.t + 5
        seg = TranscriptSegment(segment_id=new_id("seg"), speaker_id=speaker_id or speaker.lower(),
                                speaker_name=speaker, text=text, t_start=self.t, t_end=self.t + 3,
                                source="replay")
        await self.bus.publish(self.meeting_id, "transcript.segment", seg)
        from backend.app.pipeline.entry import process_segment, set_engine
        set_engine(self.engine)
        await process_segment(seg, self.ctx())
        await asyncio.sleep(0)  # like the live receiver, let background checks run between sentences
        return seg

    async def settle(self, timeout: float = 5.0):
        await self.engine.drain(self.meeting_id, timeout)

    async def end(self):
        await self.bus.publish(self.meeting_id, "meeting.ended", MeetingEnded(reason="replay_finished"))
        for _ in range(500):
            if self.record.summary is not None:
                return
            await asyncio.sleep(0.01)
        raise AssertionError("no summary within 5 s")

    @property
    def record(self):
        return self.store.get(self.meeting_id)

    def events(self, type_: str | None = None):
        return [e for e in self.store.events_since(self.meeting_id) if type_ is None or e.type == type_]


def run(coro):
    return asyncio.run(coro)


__all__ = ["Harness", "Scripted", "flag_when", "verdict", "run", "LLMError", "SAMPLE_DOC"]
