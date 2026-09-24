"""
WHY THIS EXISTS
The one door every finished transcript sentence enters the engine by. Its
name and signature are FROZEN by the contract (DESIGN.md §4.4):

    async def process_segment(segment: TranscriptSegment, ctx: MeetingContext) -> None

The engine answers only by publishing events on ctx.bus.

FAILURE IT PREVENTS
The meeting lane and the engine lane drifting apart on how a sentence is
handed over; the meeting lane builds against this signature blind.

THIS FILE IS A PLACEHOLDER (milestone 0). It does not think. It fires one
CANNED alert when a sentence claims Q3 revenue was rising, and one CANNED
spoken answer when a sentence starts with "Hey AGI". Everything it emits is
marked canned=True and says "CANNED" in its text. The engine lane replaces
the body of this function; it must keep the name and signature.
"""
from __future__ import annotations

import re

from ..contract.context import MeetingContext
from ..contract.events import Alert, ChatPost, Evidence, SpokenAnswer, TranscriptSegment, Wake
from ..core.ids import new_id

CANNED_EVIDENCE = Evidence(
    document="SAMPLE_board_deck_q3.md",
    passage="Q3 revenue was $41.2M, down 4% from $42.9M in Q2.",
    locator="Revenue summary",
)


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", text.lower())).strip()


async def process_segment(segment: TranscriptSegment, ctx: MeetingContext) -> None:
    text = _normalize(segment.text)
    record = ctx.store.get(ctx.meeting_id)

    if text.startswith("hey agi"):
        question = segment.text.split(",", 1)[-1].strip() if "," in segment.text else segment.text
        await ctx.bus.publish(ctx.meeting_id, "wake", Wake(
            trigger="phrase", segment_id=segment.segment_id, matched_variant="hey agi", question=question))
        await _canned_answer(ctx, question, segment.speaker_name)
        return

    already_alerted = record is not None and any(a.canned for a in record.alerts)
    if not already_alerted and "q3" in text and "revenue" in text and re.search(r"\b(rising|up|grew)\b", text):
        alert_id = new_id("alr")
        await ctx.bus.publish(ctx.meeting_id, "alert", Alert(
            alert_id=alert_id, kind="contradiction", topic="Q3 revenue was rising",
            claim=segment.text, said_by=[segment.speaker_name], segment_ids=[segment.segment_id],
            finding="CANNED: the board deck says Q3 revenue fell 4%.",
            evidence=[CANNED_EVIDENCE],
            reasoning="CANNED PLACEHOLDER REASONING - the placeholder engine matched the words "
                      "'Q3', 'revenue' and 'rising'; no model was called.",
            confidence=1.0, gated=False, delivered_to_chat=not ctx.muted,
            models_used=["placeholder"], canned=True))
        await ctx.bus.publish(ctx.meeting_id, "chat.post", ChatPost(
            chat_id=new_id("chat"), reason="alert", ref_id=alert_id,
            status="suppressed_muted" if ctx.muted else "pending",
            text="Because you mentioned Q3 revenue was rising: the board deck says it fell 4%. "
                 "Details in the dashboard. (CANNED placeholder)"))


async def _canned_answer(ctx: MeetingContext, question: str, asked_by: str | None) -> None:
    answer_id = new_id("ans")
    await ctx.bus.publish(ctx.meeting_id, "spoken.answer", SpokenAnswer(
        answer_id=answer_id, question=question, asked_by=asked_by,
        text="CANNED ANSWER: According to the board deck, Q3 revenue was 41.2 million dollars, "
             "down 4 percent from Q2.",
        evidence=[CANNED_EVIDENCE], status="muted" if ctx.muted else "queued", canned=True))
    await ctx.bus.publish(ctx.meeting_id, "chat.post", ChatPost(
        chat_id=new_id("chat"), reason="answer", ref_id=answer_id,
        status="suppressed_muted" if ctx.muted else "pending",
        text="Because you asked: Q3 revenue was $41.2M, down 4% from Q2 (board deck). (CANNED placeholder)"))
