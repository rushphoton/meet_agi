"""
WHY THIS EXISTS
The real AI provider: Gemini Flash-Lite for the cheap check that runs on
every sentence, Claude for the three careful jobs (dispute judgement, spoken
answer, summary). This file holds the instructions (prompts) each model gets
and turns each reply into the engine's result shapes, clamping anything out
of range.

FAILURE IT PREVENTS
- Paying for the careful model on every "yeah, sounds good": only the cheap
  model sees every sentence (DESIGN.md §3.1).
- Answers from general knowledge: the answer prompt allows only the given
  passages and tells the model to say "I couldn't find that in our
  documents" otherwise (DESIGN.md §8 decision 9).
- A model reply that is almost right (a confidence of 1.3, a topic of 200
  characters, a passage number that does not exist) breaking the contract:
  every field is checked and clamped here.
"""
from __future__ import annotations

from ...contract.events import Alert, TranscriptSegment
from ...knowledge import Passage
from .base import NOT_FOUND, ActionItem, AnswerDraft, CheapCheck, LLMError, SummaryDraft, Verdict
from .vendors import VendorClient

KINDS = {"contradiction", "disagreement", "uncertainty"}


def _lines(lines: list[TranscriptSegment]) -> str:
    return "\n".join(f"[{i}] {s.speaker_name}: {s.text}" for i, s in enumerate(lines))


def _passages(passages: list[Passage]) -> str:
    if not passages:
        return "(no matching passages in the documents)"
    return "\n\n".join(f"[{i}] {p.document} / {p.locator or 'no heading'}:\n{p.text}"
                       for i, p in enumerate(passages))


def _flagged(topics: list[str]) -> str:
    return "; ".join(topics) if topics else "(none yet)"


CHEAP_SYSTEM = """You watch a live business meeting transcript for ONE thing: a factual claim that is \
disputed or doubtful. Flag the LAST line only if, in it, someone
- states a fact or number that contradicts the document passages, or
- disagrees with a fact someone else just stated, or
- is unsure about a fact or number that the documents could settle.
Do NOT flag opinions, plans, small talk, questions to the assistant, or facts the documents agree with.
Do NOT flag a dispute whose topic is already in the "already flagged" list.
Reply with JSON only: {"worth_a_look": true|false, "score": 0.0-1.0, "topic": "<= 8 words"}"""

JUDGE_SYSTEM = """You are the careful fact-checker for a live business meeting. You are shown recent \
transcript lines (numbered) and passages from the company's own documents (numbered).
Decide whether the LAST line contains a factual dispute worth interrupting the meeting for:
- "contradiction": a stated fact conflicts with the documents;
- "disagreement": participants disagree about a fact;
- "uncertainty": someone is unsure of a fact the documents answer.
Only the documents count as evidence; never use outside knowledge. If the documents do not settle \
it, confidence must be low. If the topic is already in the "already flagged" list, is_issue is false.
topic: a short phrase that reads naturally after "Because you mentioned" (max 8 words), \
e.g. "Q3 revenue was rising".
finding: one sentence stating what the documents say, naming the document.
reasoning: full explanation - who said what, which passage, why it conflicts, how sure you are.
Record your result with the record_verdict tool."""

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "is_issue": {"type": "boolean"},
        "kind": {"type": "string", "enum": sorted(KINDS)},
        "topic": {"type": "string"},
        "claim": {"type": "string", "description": "the disputed statement, quoted or paraphrased"},
        "said_by": {"type": "array", "items": {"type": "string"}},
        "line_numbers": {"type": "array", "items": {"type": "integer"}},
        "finding": {"type": "string"},
        "reasoning": {"type": "string"},
        "confidence": {"type": "number"},
        "passage_numbers": {"type": "array", "items": {"type": "integer"}},
    },
    "required": ["is_issue", "kind", "topic", "claim", "said_by", "line_numbers", "finding",
                 "reasoning", "confidence", "passage_numbers"],
}

ANSWER_SYSTEM = """You are Meet AGI, answering a question out loud in a live meeting. Use ONLY the \
numbered document passages. Name the document you used in the answer (in plain words, e.g. "the \
board deck"). Speak naturally: no lists, no markdown, numbers written as people say them. At most \
{max_words} words. If the passages do not contain the answer, the spoken answer is exactly: \
"{not_found}"
chat_line: the answer as one short line for the meeting chat (under 300 characters), with figures.
Record your result with the record_answer tool."""

ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "spoken": {"type": "string"},
        "chat_line": {"type": "string"},
        "passage_numbers": {"type": "array", "items": {"type": "integer"}},
    },
    "required": ["spoken", "chat_line", "passage_numbers"],
}

SUMMARY_SYSTEM = """You write the executive summary of a business meeting from its transcript and \
the fact-check alerts raised during it.
key_topics: 3-6 short phrases. takeaways: 3-6 one-sentence conclusions.
action_items: concrete follow-ups someone committed to or was asked to do, and open questions left \
unresolved; owner is the person's name if named, else null.
unsettled_alert_ids: ids of alerts whose dispute was NOT resolved later in the transcript.
Record your result with the record_summary tool."""

SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "key_topics": {"type": "array", "items": {"type": "string"}},
        "takeaways": {"type": "array", "items": {"type": "string"}},
        "action_items": {"type": "array", "items": {
            "type": "object",
            "properties": {"text": {"type": "string"}, "owner": {"type": ["string", "null"]}},
            "required": ["text"]}},
        "unsettled_alert_ids": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["key_topics", "takeaways", "action_items", "unsettled_alert_ids"],
}


def _clamp(x, lo=0.0, hi=1.0) -> float:
    try:
        return max(lo, min(hi, float(x)))
    except (TypeError, ValueError):
        return 0.0


def _indexes(values, n: int) -> list[int]:
    out = []
    for v in values or []:
        try:
            i = int(v)
        except (TypeError, ValueError):
            continue
        if 0 <= i < n and i not in out:
            out.append(i)
    return out


class RealProvider:
    name = "real"
    canned = False

    def __init__(self, client: VendorClient | None = None) -> None:
        self.client = client or VendorClient()

    async def cheap_check(self, model, lines, passages, already_flagged) -> CheapCheck:
        prompt = (f"Document passages:\n{_passages(passages)}\n\nAlready flagged: {_flagged(already_flagged)}"
                  f"\n\nTranscript (last line is the new one):\n{_lines(lines)}")
        data, used = await self.client.gemini_json(model, CHEAP_SYSTEM, prompt)
        return CheapCheck(worth_a_look=bool(data.get("worth_a_look")), score=_clamp(data.get("score")),
                          topic=str(data.get("topic") or "")[:60], model=used)

    async def judge(self, model, lines, passages, already_flagged) -> Verdict:
        prompt = (f"Document passages:\n{_passages(passages)}\n\nAlready flagged: {_flagged(already_flagged)}"
                  f"\n\nTranscript (last line is the new one):\n{_lines(lines)}")
        d, used = await self.client.claude_tool(model, JUDGE_SYSTEM, prompt, "record_verdict", JUDGE_SCHEMA)
        kind = d.get("kind") if d.get("kind") in KINDS else "uncertainty"
        speakers = {s.speaker_name for s in lines}
        said_by = [n for n in (d.get("said_by") or []) if isinstance(n, str) and n in speakers]
        return Verdict(
            is_issue=bool(d.get("is_issue")), kind=kind, topic=str(d.get("topic") or "").strip(),
            claim=str(d.get("claim") or ""), said_by=said_by or ([lines[-1].speaker_name] if lines else []),
            segment_indexes=_indexes(d.get("line_numbers"), len(lines)) or [len(lines) - 1],
            finding=str(d.get("finding") or "").strip(), reasoning=str(d.get("reasoning") or ""),
            confidence=_clamp(d.get("confidence")),
            passage_indexes=_indexes(d.get("passage_numbers"), len(passages)), model=used)

    async def answer(self, model, question, asked_by, passages, max_words) -> AnswerDraft:
        system = ANSWER_SYSTEM.format(max_words=max_words, not_found=NOT_FOUND)
        prompt = f"Document passages:\n{_passages(passages)}\n\nQuestion from {asked_by or 'a participant'}: {question}"
        d, used = await self.client.claude_tool(model, system, prompt, "record_answer", ANSWER_SCHEMA, 512)
        spoken = " ".join(str(d.get("spoken") or "").split())
        if not spoken:
            raise LLMError("answer was empty")
        return AnswerDraft(spoken=spoken, chat_line=" ".join(str(d.get("chat_line") or spoken).split()),
                           passage_indexes=_indexes(d.get("passage_numbers"), len(passages)), model=used)

    async def summarize(self, model, segments: list[TranscriptSegment], alerts: list[Alert]) -> SummaryDraft:
        alert_text = "\n".join(
            f"- id={a.alert_id} kind={a.kind} topic={a.topic!r} said_by={a.said_by} finding={a.finding!r}"
            for a in alerts) or "(no alerts)"
        prompt = f"Alerts raised:\n{alert_text}\n\nTranscript:\n{_lines(segments)}"
        d, used = await self.client.claude_tool(model, SUMMARY_SYSTEM, prompt, "record_summary",
                                                SUMMARY_SCHEMA, 2048)
        ids = {a.alert_id for a in alerts}
        items = []
        for item in d.get("action_items") or []:
            if isinstance(item, dict) and str(item.get("text") or "").strip():
                owner = item.get("owner")
                items.append(ActionItem(text=str(item["text"]).strip(),
                                        owner=str(owner) if isinstance(owner, str) and owner else None))
        return SummaryDraft(
            key_topics=[str(t) for t in d.get("key_topics") or [] if str(t).strip()],
            takeaways=[str(t) for t in d.get("takeaways") or [] if str(t).strip()],
            action_items=items,
            unsettled_alert_ids=[i for i in d.get("unsettled_alert_ids") or [] if i in ids],
            model=used)
