"""
WHY THIS EXISTS
The real AI provider. By default Gemini Flash-Lite does the cheap check that
runs on every sentence (and the wake check on sentences that only SOUNDED
like "Hey AGI"); the three careful jobs (dispute judgement, spoken answer,
summary) are settings too, Gemini by default since 25 Sep. Which vendor serves a job follows the
model name in Settings: any "gemini-..." model goes to Gemini, anything else
to Claude - so if one vendor is down (or out of credit) Ray can move jobs to
the other from the settings screen, with no code change. This file holds the instructions (prompts) each model gets
and turns each reply into the engine's result shapes, clamping anything out
of range.

FAILURE IT PREVENTS
- Paying for the careful model on every "yeah, sounds good": only the cheap
  model sees every sentence (DESIGN.md §3.1).
- A general-knowledge answer passing as a document answer: when the passages
  are silent the model may answer from general knowledge, but the first
  sentence must say "That's not in your documents, but generally ..." - and
  if the model reports it used no document yet forgot to say so, the
  sentence is added here (Ray, 27 Sep 2026; replaces DESIGN.md §8 decision 9).
- An answer the room can't use when cut off: 2-5 complete spoken sentences,
  most important first, so "stop talking" part-way still leaves sense.
- A model reply that is almost right (a confidence of 1.3, a topic of 200
  characters, a passage number that does not exist) breaking the contract:
  every field is checked and clamped here.
"""
from __future__ import annotations

import json

from ...contract.events import Alert, TranscriptSegment
from ...knowledge import Passage
from .base import (
    NOT_IN_DOCS, ActionItem, AnswerDraft, CheapCheck, LLMError, SummaryDraft, Verdict, WakeCheck,
)
from .vendors import VendorClient

KINDS = {"contradiction", "disagreement", "uncertainty"}


def _lines(lines: list[TranscriptSegment]) -> str:
    return "\n".join(f"[{i}] {s.speaker_name}: {s.text}" for i, s in enumerate(lines))


def _passages(passages: list[Passage]) -> str:
    if not passages:
        return "(no matching passages in the documents)"
    return "\n\n".join(f"[{i}] {p.document} / {p.locator or 'no heading'}:\n{p.text}"
                       for i, p in enumerate(passages))


def _new_note(lines: list, new_lines: int) -> str:
    n = max(1, min(new_lines, len(lines)))
    if n == 1:
        return "the last line is the new one"
    return f"the last {n} lines are new; earlier ones are context"


def _flagged(topics: list[str]) -> str:
    return "; ".join(topics) if topics else "(none yet)"


CHEAP_SYSTEM = """You watch a live business meeting transcript (the last 8 lines, with speaker names) \
for ONE thing: a factual claim that is disputed, doubtful or flip-flopping. Flag it if, in a NEW line \
(the transcript header says which lines are new), someone
- states a fact or number that contradicts the document passages, or
- disagrees with a fact someone else just stated, or
- contradicts or corrects THEMSELVES about a fact or number ("it was rising... no wait, that's wrong, \
it fell"; "this is right... no, this is wrong"), or
- hedges about a number or fact ("I'm not sure that's right", "hmm, not sure that's correct", "I \
think it was around..."), or
- is unsure about a fact the documents could settle.
Self-correction, hedging about a number, and "this is right / this is wrong" flip-flops are ALWAYS worth \
a look, even when the documents say nothing about the fact and even when only one person is speaking.
Do NOT flag opinions, plans, small talk, questions to the assistant, or facts the documents agree with \
that nobody doubted.
Do NOT flag a dispute whose topic is already in the "already flagged" list.
Reply with JSON only: {"worth_a_look": true|false, "score": 0.0-1.0, "topic": "<= 8 words"}"""

CHEAP_SCHEMA = {
    "type": "object",
    "properties": {"worth_a_look": {"type": "boolean"}, "score": {"type": "number"},
                   "topic": {"type": "string"}},
    "required": ["worth_a_look", "score", "topic"],
}

JUDGE_SYSTEM = """You are the careful fact-checker for a live business meeting. You are shown the last \
transcript lines (numbered, with speaker names) and passages from the company's own documents (numbered).
Decide whether a NEW line (the transcript header says which lines are new) is worth a short note in \
the meeting chat:
- "contradiction": a stated fact conflicts with the documents, OR one speaker contradicts or corrects \
themselves about a fact or number (said it rose, then that it fell; "this is right... this is wrong");
- "disagreement": participants disagree about a fact;
- "uncertainty": someone hedges or is unsure about a fact or number ("not sure that's correct").
Explicitly worth a look: self-correction, hedging about a number, and right/wrong flip-flops - also \
from a single speaker, not only between people.
Evidence: when the documents cover the fact, say what they say and cite them (passage_numbers). When \
the documents do NOT cover it, it can still be an issue of kind "contradiction" or "uncertainty": the \
finding then says what was said both ways (or what was doubted) and that the documents don't settle \
it; never invent the true figure from outside knowledge. Confidence is how sure you are that the \
room would want this pointed out (clear self-contradiction or explicit doubt about a number: high; \
vague or rhetorical: low).
If the topic is already in the "already flagged" list, is_issue is false.
topic: a short phrase that reads naturally after "Because you mentioned" (max 8 words), \
e.g. "Q3 revenue was rising" or "revenue going both ways".
finding: one sentence - what the documents say, naming the document; or, when they are silent, \
what was said both ways and that the documents don't settle it.
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

ANSWER_SYSTEM = """You are Meet AGI, answering a question out loud in a live meeting. Answer the \
question that was actually asked (the recent transcript is there only to resolve words like "that" \
or "it"; captions may misspell words, e.g. "bortech" for "board deck").
Form: 2 to 5 complete spoken sentences, at most {max_words} words in total, the most important fact \
FIRST, so the answer still makes sense if someone says "stop talking" after any sentence. Speak \
naturally: no lists, no markdown, numbers written as people say them.
Sources: prefer the numbered document passages and name the document you used in plain words \
(e.g. "the board deck"). If the passages do not answer it, answer from general knowledge and begin \
the first sentence with exactly "{not_in_docs}" - never present general knowledge as coming from \
the documents. from_documents is true only if the answer comes from the passages.
chat_line: the answer as one short line for the meeting chat (under 300 characters), with figures.
Record your result with the record_answer tool."""

ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "spoken": {"type": "string"},
        "chat_line": {"type": "string"},
        "passage_numbers": {"type": "array", "items": {"type": "integer"}},
        "from_documents": {"type": "boolean"},
    },
    "required": ["spoken", "chat_line", "passage_numbers", "from_documents"],
}

WAKE_SYSTEM = """A meeting has an AI assistant called "AGI" (Meet AGI). People wake it by saying \
"Hey AGI". Live captions often misspell that as "Hey GI", "Hey giant", "Hey GI Joe", "Hey AJ", \
"Hey Ajay", "Hey edgy", "Hey Aggie", "Okay AGI". You are shown recent transcript lines and the LAST \
line, which sounded like it might be "Hey AGI".
addressed: true only if the speaker of the last line is talking TO the AI assistant (asking it \
something or calling it). False if they greet or address a person (e.g. "Hey Jim", "Hi Gina, how are \
you"), talk ABOUT the assistant ("if you say hey AGI it answers"), or it is ordinary speech.
question: if addressed, the question or request put to the assistant, in the speaker's words \
without the greeting (empty if they only called it). Otherwise empty.
Reply with JSON only."""

WAKE_SCHEMA = {
    "type": "object",
    "properties": {"addressed": {"type": "boolean"}, "question": {"type": "string"}},
    "required": ["addressed", "question"],
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

    async def _structured(self, model: str, system: str, prompt: str, name: str, schema: dict,
                          max_tokens: int) -> tuple[dict, str]:
        """Route by model name: any "gemini-..." model goes to Gemini's JSON mode, anything else
        to Claude's forced tool call. Same result either way, so Settings can move any job
        between vendors with no code change (review B item 1)."""
        if model.startswith("gemini-"):
            fields = json.dumps(schema, separators=(",", ":"))
            return await self.client.gemini_json(
                model, f"{system}\nInstead of a tool, reply with JSON only: one object matching this "
                       f"JSON schema: {fields}", prompt, max_tokens)
        return await self.client.claude_tool(model, system, prompt, name, schema, max_tokens)

    async def cheap_check(self, model, lines, passages, already_flagged, new_lines: int = 1) -> CheapCheck:
        prompt = (f"Document passages:\n{_passages(passages)}\n\nAlready flagged: {_flagged(already_flagged)}"
                  f"\n\nTranscript ({_new_note(lines, new_lines)}):\n{_lines(lines)}")
        data, used = await self._structured(model, CHEAP_SYSTEM, prompt, "record_check", CHEAP_SCHEMA, 256)
        return CheapCheck(worth_a_look=bool(data.get("worth_a_look")), score=_clamp(data.get("score")),
                          topic=str(data.get("topic") or "")[:60], model=used)

    async def confirm_wake(self, model, text: str, heard: str, lines) -> WakeCheck:
        recent = _lines(list(lines)[-4:]) if lines else "(none)"
        prompt = (f"Recent transcript:\n{recent}\n\nLast line (the AGI-like word heard: {heard!r}):\n{text}")
        d, used = await self._structured(model, WAKE_SYSTEM, prompt, "record_wake", WAKE_SCHEMA, 200)
        question = " ".join(str(d.get("question") or "").split())
        return WakeCheck(addressed=bool(d.get("addressed")), question=question or None, model=used)

    async def judge(self, model, lines, passages, already_flagged, new_lines: int = 1) -> Verdict:
        prompt = (f"Document passages:\n{_passages(passages)}\n\nAlready flagged: {_flagged(already_flagged)}"
                  f"\n\nTranscript ({_new_note(lines, new_lines)}):\n{_lines(lines)}")
        d, used = await self._structured(model, JUDGE_SYSTEM, prompt, "record_verdict", JUDGE_SCHEMA, 1024)
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

    async def answer(self, model, question, asked_by, passages, max_words, context=None) -> AnswerDraft:
        system = ANSWER_SYSTEM.format(max_words=max_words, not_in_docs=NOT_IN_DOCS)
        recent = f"Recent transcript:\n{_lines(list(context)[-6:])}\n\n" if context else ""
        prompt = (f"Document passages:\n{_passages(passages)}\n\n{recent}"
                  f"Question from {asked_by or 'a participant'}: {question}")
        d, used = await self._structured(model, system, prompt, "record_answer", ANSWER_SCHEMA, 700)
        spoken = " ".join(str(d.get("spoken") or "").split())
        if not spoken:
            raise LLMError("answer was empty")
        indexes = _indexes(d.get("passage_numbers"), len(passages))
        from_docs = d.get("from_documents")
        if (from_docs is False or (from_docs is None and not indexes)) and not spoken.lower().startswith(
                NOT_IN_DOCS.lower()[:28]):
            # The model answered from general knowledge but did not say so first: say it for it.
            spoken = f"{NOT_IN_DOCS}, {spoken[0].lower()}{spoken[1:]}"
        if from_docs is False:
            indexes = []
        return AnswerDraft(spoken=spoken, chat_line=" ".join(str(d.get("chat_line") or spoken).split()),
                           passage_indexes=indexes, model=used)

    async def summarize(self, model, segments: list[TranscriptSegment], alerts: list[Alert]) -> SummaryDraft:
        alert_text = "\n".join(
            f"- id={a.alert_id} kind={a.kind} topic={a.topic!r} said_by={a.said_by} finding={a.finding!r}"
            for a in alerts) or "(no alerts)"
        prompt = f"Alerts raised:\n{alert_text}\n\nTranscript:\n{_lines(segments)}"
        d, used = await self._structured(model, SUMMARY_SYSTEM, prompt, "record_summary",
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
