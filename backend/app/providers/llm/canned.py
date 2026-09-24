"""
WHY THIS EXISTS
A stand-in for the AI models, used by every test, by `OFFLINE=1`, and
whenever a key is missing. It calls nothing and costs nothing. It follows a
few fixed rules written for the scripted fake meeting, and everything it
produces is marked canned=True and says "CANNED" in its text (CLAUDE.md
rule 6), so nobody mistakes it for real judgement.

FAILURE IT PREVENTS
Tests that depend on a vendor being up, being paid for, or answering the
same way twice. With this provider the fake meeting always yields exactly
one alert, one spoken answer and one summary.

Its rules (deliberately simple, deliberately visible):
- cheap check: a sentence that states a direction ("rising", "fell", ...)
  about a business metric ("revenue", "bookings", ...) is worth a look.
- judge: only one scripted dispute is recognised - someone saying Q3 revenue
  was rising/up/grew. Everything else is "no issue".
- answer: reads back the one document line sharing most words with the
  question (or says it couldn't find it).
- summary: topics from the alerts; follow-ups from "I'll ..." and
  "..., please ..." sentences; an alert counts as settled if someone later
  says "my mistake", "you're right" or "fair enough".
"""
from __future__ import annotations

import re

from ...contract.events import Alert, TranscriptSegment
from ...knowledge import Passage
from ...knowledge.index import tokenize
from .base import NOT_FOUND, ActionItem, AnswerDraft, CheapCheck, SummaryDraft, Verdict

MODEL = "canned"
_METRIC = re.compile(r"\b(revenue|bookings|margin|churn|pipeline|sales|profit|arr)\b")
_DIRECTION = re.compile(r"\b(rising|rose|grew|growing|up|increased|fell|falling|down|dropped|declined|decreased)\b")
_Q3_UP = re.compile(r"\bq3 revenue\b.*\b(rising|rose|grew|growing|up|increased)\b")
_SETTLED = re.compile(r"\b(my mistake|you're right|you are right|fair enough)\b")
_WILL = re.compile(r"^(i'll|i will)\b", re.I)
_PLEASE = re.compile(r"\bplease\b", re.I)


class CannedProvider:
    name = "canned"
    canned = True

    def __init__(self, reason: str = "canned provider") -> None:
        self.reason = reason

    async def cheap_check(self, model, lines: list[TranscriptSegment], passages, already_flagged,
                          new_lines: int = 1) -> CheapCheck:
        for seg in reversed(lines[-max(1, new_lines):]):
            text = seg.text.lower()
            if _METRIC.search(text) and _DIRECTION.search(text):
                return CheapCheck(worth_a_look=True, score=0.9, topic=_METRIC.search(text).group(1),
                                  model=MODEL, canned=True)
        return CheapCheck(worth_a_look=False, score=0.0, topic="", model=MODEL, canned=True)

    async def judge(self, model, lines: list[TranscriptSegment], passages: list[Passage],
                    already_flagged, new_lines: int = 1) -> Verdict:
        n = max(1, min(new_lines, len(lines)))
        hits = [i for i in range(len(lines) - n, len(lines)) if _Q3_UP.search(lines[i].text.lower())]
        if not hits or any(t.lower().startswith("q3 revenue") for t in already_flagged):
            return Verdict(is_issue=False, model=MODEL, canned=True)
        i = hits[-1]
        claim = lines[i]
        revenue = [j for j, p in enumerate(passages) if "q3 revenue" in p.text.lower()]
        return Verdict(
            is_issue=True, kind="contradiction", topic="Q3 revenue was rising", claim=claim.text,
            said_by=[claim.speaker_name], segment_indexes=[i],
            finding="the board deck says Q3 revenue fell 4% ($41.2M, down from $42.9M in Q2).",
            reasoning=(f"CANNED REASONING (no model was called; {self.reason}). {claim.speaker_name} said "
                       f"Q3 revenue was rising. The canned judge has one scripted rule: the sample board "
                       f"deck states Q3 revenue was $41.2M, down 4% from Q2, so a claim that it rose is "
                       f"a contradiction."),
            confidence=0.95, passage_indexes=revenue[:1], model=MODEL, canned=True)

    async def answer(self, model, question: str, asked_by, passages: list[Passage], max_words: int) -> AnswerDraft:
        # Pick the single document line sharing most words with the question, ignoring words
        # that only name the document ("board deck") and preferring lines with a number in them.
        best: tuple[float, int, str] | None = None
        for i, p in enumerate(passages):
            doc_words = set(tokenize(p.document.replace("_", " ")))
            wanted = set(tokenize(question)) - doc_words
            for line in p.text.splitlines():
                line = line.strip("- ").strip()
                if not line:
                    continue
                score = len(wanted & set(tokenize(line))) + (1 if re.search(r"\d", line) else 0)
                if score > 0 and (best is None or score > best[0]):
                    best = (score, i, line)
        if best is None:
            return AnswerDraft(spoken=f"CANNED ANSWER: {NOT_FOUND}", chat_line=f"{NOT_FOUND} (CANNED)",
                               passage_indexes=[], model=MODEL, canned=True)
        _, i, line = best
        doc = passages[i].document
        spoken = " ".join(f"CANNED ANSWER: according to {_doc_title(doc)}, {line}".split()[:max_words])
        return AnswerDraft(spoken=spoken, chat_line=f"{line} ({doc}) (CANNED)",
                           passage_indexes=[i], model=MODEL, canned=True)

    async def summarize(self, model, segments: list[TranscriptSegment], alerts: list[Alert]) -> SummaryDraft:
        first_names = {s.speaker_name.split()[0].lower(): s.speaker_name for s in segments if s.speaker_name}
        items: list[ActionItem] = []
        for s in segments:
            text = s.text.strip()
            if _WILL.search(text) and len(text.split()) > 3:
                items.append(ActionItem(text=text, owner=s.speaker_name))
            elif _PLEASE.search(text):
                for clause in re.split(r"(?<=[.!?])\s+", text):
                    if not _PLEASE.search(clause):
                        continue
                    first = clause.split(",")[0].strip().lower()
                    items.append(ActionItem(text=clause.strip(), owner=first_names.get(first)))
        unsettled = []
        for a in alerts:
            idx = max((i for i, s in enumerate(segments) if s.segment_id in a.segment_ids), default=-1)
            later = segments[idx + 1:]
            if not any(_SETTLED.search(s.text.lower()) for s in later):
                unsettled.append(a.alert_id)
        topics = [a.topic for a in alerts] or ["(no disputes flagged)"]
        takeaways = [f"{a.topic}: {a.finding}" for a in alerts]
        takeaways.append(f"CANNED summary - no model was called ({self.reason}).")
        return SummaryDraft(key_topics=[f"CANNED: {t}" for t in topics], takeaways=takeaways,
                            action_items=items, unsettled_alert_ids=unsettled, model=MODEL, canned=True)


def _doc_title(name: str) -> str:
    return name.rsplit(".", 1)[0].replace("SAMPLE_", "").replace("_", " ")
