"""
WHY THIS EXISTS
Names the five jobs the engine asks an AI model to do - the cheap "is this
worth a closer look?" check, the cheap "is this person talking to the
assistant?" wake check, the careful dispute judgement, the spoken answer, and
the end-of-meeting summary - and the shape of each result. The
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

import re
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
class WakeCheck:
    """The cheap model's answer to "is the speaker addressing the AI assistant?" for a sentence
    that only sounded like "Hey AGI" ("Hey Aggie, ...")."""
    addressed: bool
    question: str | None
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

    # new_lines: how many of the last `lines` have not been checked yet (more than 1 when a
    # backlog was skipped); the rest are context.
    async def cheap_check(self, model: str, lines: list[TranscriptSegment], passages: list[Passage],
                          already_flagged: list[str], new_lines: int = 1) -> CheapCheck: ...

    # text: the sentence that sounded like "Hey AGI"; heard: the AGI-like word(s) in it;
    # lines: recent transcript for context.
    async def confirm_wake(self, model: str, text: str, heard: str,
                           lines: list[TranscriptSegment]) -> WakeCheck: ...

    async def judge(self, model: str, lines: list[TranscriptSegment], passages: list[Passage],
                    already_flagged: list[str], new_lines: int = 1) -> Verdict: ...

    # context: the last few transcript lines, so "what about that number?" makes sense.
    async def answer(self, model: str, question: str, asked_by: str | None, passages: list[Passage],
                     max_words: int, context: list[TranscriptSegment] | None = None) -> AnswerDraft: ...

    async def summarize(self, model: str, segments: list[TranscriptSegment],
                        alerts: list[Alert]) -> SummaryDraft: ...


# When the documents are silent the bot answers from general knowledge and says so FIRST (Ray,
# 27 Sep 2026; replaces DESIGN §8 decision 9's "I couldn't find that in our documents").
NOT_IN_DOCS = "That's not in your documents, but generally"

# Where an answer came from (the answer model says which). Only "general" gets the NOT_IN_DOCS opening;
# "conversational" ("are you here?", "can you hear me?") gets a plain short reply (Ray's live lines, 27 Sep).
ANSWER_SOURCES = ("documents", "general", "conversational")

_NOT_IN_DOCS_SAID = re.compile(r"\bnot in (?:your|our|the) documents\b")
_NOT_IN_DOCS_LEAD = re.compile(
    r"^\s*(?:that(?:'s|’s|\s+is)\s+not|that\s+isn(?:'|’)t|it(?:'s|’s|\s+is)\s+not|it\s+isn(?:'|’)t)\s+in\s+"
    r"(?:your|our|the)\s+(?:documents|docs)\s*,?\s*(?:but\s+generally(?:\s+speaking)?\s*,?\s*)?",
    re.IGNORECASE)


def _first_sentence(text: str) -> str:
    return re.split(r"(?<=[.!?])\s+", text.strip(), maxsplit=1)[0]


def says_not_in_docs(text: str) -> bool:
    """True when the FIRST sentence already says the answer isn't from the documents, however it is
    worded: "that's"/"that is", "isn't"/"is not", "documents"/"docs". Stops the doubled
    "That's not in your documents, but generally, that is not in your documents, ..." heard live."""
    s = _first_sentence(text).lower().replace("’", "'")
    s = re.sub(r"\bthat's\b", "that is", s)
    s = re.sub(r"\bit's\b", "it is", s)
    s = re.sub(r"\bisn't\b", "is not", s)
    s = re.sub(r"\bdocs\b", "documents", s)
    return bool(_NOT_IN_DOCS_SAID.search(s))


def with_not_in_docs(text: str) -> str:
    """The general-knowledge opening, added once."""
    if says_not_in_docs(text):
        return text
    return f"{NOT_IN_DOCS}, {text[0].lower()}{text[1:]}" if text else NOT_IN_DOCS + "."


def without_not_in_docs(text: str) -> str:
    """A conversational reply ("Yes, I'm here") must not open with "That's not in your documents"."""
    stripped = _NOT_IN_DOCS_LEAD.sub("", text, count=1).strip()
    if stripped == text.strip() or not stripped:
        return text
    return stripped[0].upper() + stripped[1:]
