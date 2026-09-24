"""
WHY THIS EXISTS
Names the four jobs the engine asks an AI model to do - the cheap "is this
worth a closer look?" check, the careful dispute judgement, the spoken
answer, and the end-of-meeting summary - and the shape of each result. The
real providers (Gemini, Claude) and the canned test provider all do these
same four jobs, so the pipeline never knows or cares which one it is using.

FAILURE IT PREVENTS
Tests quietly calling a paid vendor (the canned provider is a drop-in
replacement), and a vendor's odd reply shape leaking into the pipeline (every
provider must return these results or raise LLMError).

These are engine-internal working shapes. They never leave the backend; what
the dashboard sees is the contract's Alert / SpokenAnswer / MeetingSummary.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from ...contract.events import Alert, TranscriptSegment
from ...knowledge import Passage


class LLMError(Exception):
    """Any vendor failure: timeout, HTTP error, unusable reply. The pipeline catches it."""


@dataclass
class CheapCheck:
    worth_a_look: bool
    score: float
    topic: str
    model: str
    canned: bool = False


@dataclass
class Verdict:
    is_issue: bool
    kind: str = "uncertainty"          # contradiction | disagreement | uncertainty
    topic: str = ""
    claim: str = ""
    said_by: list[str] = field(default_factory=list)
    segment_indexes: list[int] = field(default_factory=list)   # into the lines given to the judge
    finding: str = ""
    reasoning: str = ""
    confidence: float = 0.0
    passage_indexes: list[int] = field(default_factory=list)   # into the passages given to the judge
    model: str = ""
    canned: bool = False


@dataclass
class AnswerDraft:
    spoken: str
    chat_line: str
    passage_indexes: list[int]
    model: str
    canned: bool = False


@dataclass
class ActionItem:
    text: str
    owner: str | None = None


@dataclass
class SummaryDraft:
    key_topics: list[str]
    takeaways: list[str]
    action_items: list[ActionItem]
    unsettled_alert_ids: list[str]
    model: str
    canned: bool = False


class LLMProvider(Protocol):
    name: str
    canned: bool

    async def cheap_check(self, model: str, lines: list[TranscriptSegment], passages: list[Passage],
                          already_flagged: list[str]) -> CheapCheck: ...

    async def judge(self, model: str, lines: list[TranscriptSegment], passages: list[Passage],
                    already_flagged: list[str]) -> Verdict: ...

    async def answer(self, model: str, question: str, asked_by: str | None, passages: list[Passage],
                     max_words: int) -> AnswerDraft: ...

    async def summarize(self, model: str, segments: list[TranscriptSegment],
                        alerts: list[Alert]) -> SummaryDraft: ...


NOT_FOUND = "I couldn't find that in our documents."
