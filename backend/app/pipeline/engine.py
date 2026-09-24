"""
WHY THIS EXISTS
The "thinker". After every finished sentence it decides, in this order:
1. Is someone saying "AGI, stop talking" while the bot is speaking?
   -> publish `stop` and drop any answer still being prepared.
2. Is someone saying "Hey AGI" (or pressing the wake button)?
   -> publish `wake` at once (so the filler line can play), take the question
   (the rest of the sentence, or the same person's next sentence within
   8 seconds), look it up in the documents, have Claude answer in at most 60
   words, and publish `spoken.answer` plus a chat line "Because you asked: ...".
3. Otherwise: is there a factual dispute or doubt, whoever is speaking?
   -> Gemini Flash-Lite takes a quick look at every sentence; only when it
   sees something does Claude judge it; the gate (gate.py) decides whether
   the room hears about it. Publishes `alert` (full reasoning, for the
   dashboard) and, if the gate passes, a chat line "Because you mentioned
   ...: ..." under 500 characters.
At meeting end it writes the summary (key topics, takeaways, follow-ups).

FAILURE IT PREVENTS
- Slow AI calls holding up the live transcript: model calls run in the
  background, one meeting's dispute checks strictly in order, so the next
  sentence is never kept waiting.
- A vendor outage crashing the meeting: a failed check is skipped and
  logged; a failed answer says so out loud; a failed summary says so.
- A summary that misses the last alert: the summary waits for the dispute
  checks still in progress to finish first.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field

from ..contract.context import MeetingContext
from ..contract.events import (
    CHAT_LIMIT, Alert, ChatPost, Evidence, FollowUp, MeetingSummary, SpokenAnswer, Stop,
    TranscriptSegment, Wake,
)
from ..core.ids import new_id
from ..knowledge import KnowledgeBase, Passage
from ..knowledge.index import tokenize
from ..providers.llm.base import AnswerDraft, LLMError, LLMProvider
from .gate import check_gate
from .phrases import detect_stop, detect_wake

log = logging.getLogger("meet_agi.engine")

CONTEXT_LINES = 8            # DESIGN.md §3.3: the cheap check sees the last 8 segments
CHEAP_PASSAGES = 3           # ... plus the top 3 document passages
ANSWER_PASSAGES = 5          # answers see the top 5
STOP_GRACE_SECONDS = 5       # "AGI, stop" still counts up to 5 s after an answer (DESIGN.md §4.5)
QUEUED_ANSWER_MAX_SECONDS = 120  # an answer nobody marked "played" stops counting as speaking after this
END_DRAIN_SECONDS = 30       # how long the summary waits for checks still running
NO_QUESTION = "Sorry, I didn't catch a question."
ANSWER_FAILED = "Sorry, I couldn't look that up just now."


@dataclass
class _Pending:
    """A wake with no question yet: waiting for the asker's next sentence."""
    trigger: str
    speaker_id: str | None      # None = button: the next sentence from anyone counts
    speaker_name: str | None
    wake_t_end: float | None    # meeting time of the wake sentence (None for the button)
    timer: asyncio.Task | None = None


@dataclass
class _MeetingState:
    recent: deque = field(default_factory=lambda: deque(maxlen=CONTEXT_LINES))
    detect_queue: asyncio.Queue | None = None
    detect_worker: asyncio.Task | None = None
    detect_in_progress: int = 0              # sentences queued or being checked
    pending: _Pending | None = None
    answer_task: asyncio.Task | None = None
    last_answer_at: float | None = None      # time.monotonic() of the last spoken.answer event
    last_answer_status: str | None = None
    last_alert_t: float | None = None        # meeting time of the last alert that passed the gate
    flagged_topics: list[str] = field(default_factory=list)
    tasks: set = field(default_factory=set)
    summarizing: bool = False


def _truncate(text: str, limit: int) -> str:
    """Cut at a word boundary with an ellipsis (DESIGN.md risk R9)."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[: limit - 1].rsplit(" ", 1)[0].rstrip(",;:- ")
    return cut + "…"


def _limit_words(text: str, max_words: int) -> str:
    words = text.split()
    return text if len(words) <= max_words else " ".join(words[:max_words]).rstrip(",;:") + "…"


def _evidence(passages: list[Passage], indexes: list[int]) -> list[Evidence]:
    return [Evidence(document=passages[i].document, passage=_truncate(passages[i].text, 400),
                     locator=passages[i].locator) for i in indexes if 0 <= i < len(passages)]


def _canned_tag(text: str, canned: bool) -> str:
    return f"{text} (CANNED)" if canned and "CANNED" not in text else text


class Engine:
    def __init__(self, provider: LLMProvider, knowledge: KnowledgeBase) -> None:
        self.provider = provider
        self.knowledge = knowledge
        self._states: dict[str, _MeetingState] = {}

    # ================= entry: one finished sentence =================
    async def on_segment(self, segment: TranscriptSegment, ctx: MeetingContext) -> None:
        st = self._state(ctx.meeting_id)
        s = ctx.settings

        # 1. "AGI, stop talking" - only counts while the bot is (about to be) speaking.
        if detect_stop(segment.text, s.stop_variants) and self._speaking(st):
            self._cancel_speech(st)
            await ctx.bus.publish(ctx.meeting_id, "stop", Stop(trigger="phrase", segment_id=segment.segment_id))
            return

        # 2. "Hey AGI ..." at the start of a sentence.
        wake = detect_wake(segment.text, s.wake.variants, s.wake.max_word_position)
        if wake:
            self._cancel_speech(st)  # a new wake supersedes an older, unanswered one
            st.recent.append(segment)
            await ctx.bus.publish(ctx.meeting_id, "wake", Wake(
                trigger="phrase", segment_id=segment.segment_id, matched_variant=wake.variant,
                question=wake.question))
            if wake.question:
                self._start_answer(ctx, st, wake.question, segment.speaker_name)
            else:
                self._start_waiting(ctx, st, _Pending("phrase", segment.speaker_id, segment.speaker_name,
                                                      segment.t_end))
            return

        # 3. The question after a bare "Hey AGI" (or after the wake button).
        pending = st.pending
        if pending is not None:
            late = pending.wake_t_end is not None and segment.t_start - pending.wake_t_end > s.wake.question_wait_seconds
            if late:
                self._clear_pending(st)
                self._start_fixed_answer(ctx, st, NO_QUESTION, pending.speaker_name)
            elif pending.speaker_id is None or pending.speaker_id == segment.speaker_id:
                self._clear_pending(st)
                st.recent.append(segment)
                self._start_answer(ctx, st, segment.text, segment.speaker_name)
                return

        # 4. Everything else: is there a factual dispute or doubt? (any speaker)
        st.recent.append(segment)
        self._enqueue_detection(ctx, st, list(st.recent))

    # ================= bus events (subscribed in register.py) =================
    async def on_wake_button(self, question: str | None, ctx: MeetingContext) -> None:
        st = self._state(ctx.meeting_id)
        self._cancel_speech(st)
        if question and question.strip():
            self._start_answer(ctx, st, question.strip(), None)
        else:
            self._start_waiting(ctx, st, _Pending("button", None, None, None))

    async def on_stop(self, meeting_id: str) -> None:
        self._cancel_speech(self._state(meeting_id))

    async def on_spoken_answer(self, meeting_id: str, status: str) -> None:
        st = self._state(meeting_id)
        st.last_answer_at = time.monotonic()
        st.last_answer_status = status

    async def on_meeting_ended(self, ctx: MeetingContext) -> None:
        st = self._state(ctx.meeting_id)
        record = ctx.store.get(ctx.meeting_id)
        if st.summarizing or record is None or record.summary is not None:
            return
        st.summarizing = True
        self._spawn(st, self._summarize(ctx, st))

    async def drain(self, meeting_id: str, timeout: float = 10.0) -> None:
        """Wait until every background job for this meeting is finished (used by tests)."""
        st = self._states.get(meeting_id)
        deadline = time.monotonic() + timeout
        while st is not None and time.monotonic() < deadline:
            busy = [t for t in st.tasks if not t.done()]
            if not busy and st.detect_in_progress == 0:
                break
            await asyncio.sleep(0.01)

    # ================= speech mode =================
    def _speaking(self, st: _MeetingState) -> bool:
        if st.pending is not None or (st.answer_task is not None and not st.answer_task.done()):
            return True
        if st.last_answer_at is None:
            return False
        age = time.monotonic() - st.last_answer_at
        if st.last_answer_status in ("queued", "playing") and age < QUEUED_ANSWER_MAX_SECONDS:
            return True
        return age < STOP_GRACE_SECONDS

    def _cancel_speech(self, st: _MeetingState) -> None:
        self._clear_pending(st)
        if st.answer_task is not None and not st.answer_task.done():
            st.answer_task.cancel()  # drops an answer still being generated (DESIGN.md §3.3 Stop)
        st.answer_task = None

    def _clear_pending(self, st: _MeetingState) -> None:
        if st.pending is not None and st.pending.timer is not None and not st.pending.timer.done():
            st.pending.timer.cancel()
        st.pending = None

    def _start_waiting(self, ctx: MeetingContext, st: _MeetingState, pending: _Pending) -> None:
        st.pending = pending
        wait = ctx.settings.wake.question_wait_seconds

        async def give_up() -> None:
            await asyncio.sleep(wait)
            if st.pending is pending:
                st.pending = None
                self._start_fixed_answer(ctx, st, NO_QUESTION, pending.speaker_name)

        pending.timer = self._spawn(st, give_up())

    def _start_answer(self, ctx: MeetingContext, st: _MeetingState, question: str, asked_by: str | None) -> None:
        st.answer_task = self._spawn(st, self._answer(ctx, question, asked_by))

    def _start_fixed_answer(self, ctx: MeetingContext, st: _MeetingState, text: str, asked_by: str | None) -> None:
        st.answer_task = self._spawn(st, self._publish_answer(
            ctx, question="(no question heard)", asked_by=asked_by,
            draft=AnswerDraft(spoken=text, chat_line="", passage_indexes=[], model="none"),
            passages=[], post_chat=False))

    async def _answer(self, ctx: MeetingContext, question: str, asked_by: str | None) -> None:
        s = ctx.settings
        passages = self.knowledge.search(question, ANSWER_PASSAGES)
        try:
            draft = await self.provider.answer(s.models.answer, question, asked_by, passages, s.answer_max_words)
        except LLMError as exc:
            log.warning("Answer model failed (%s); saying so instead", exc)
            # The vendor's error text goes to the log only, never into the meeting chat.
            draft = AnswerDraft(spoken=ANSWER_FAILED, chat_line="I couldn't look that up just now.",
                                passage_indexes=[], model="none")
        await self._publish_answer(ctx, question, asked_by, draft, passages, post_chat=True)

    async def _publish_answer(self, ctx: MeetingContext, question: str, asked_by: str | None,
                              draft: AnswerDraft, passages: list[Passage], post_chat: bool) -> None:
        muted = self._muted(ctx)
        answer_id = new_id("ans")
        text = _limit_words(draft.spoken, ctx.settings.answer_max_words)
        await ctx.bus.publish(ctx.meeting_id, "spoken.answer", SpokenAnswer(
            answer_id=answer_id, question=question, asked_by=asked_by, text=text,
            evidence=_evidence(passages, draft.passage_indexes),
            status="muted" if muted else "queued", canned=draft.canned))
        await self.on_spoken_answer(ctx.meeting_id, "muted" if muted else "queued")
        if post_chat:
            line = _canned_tag(draft.chat_line or text, draft.canned)
            await ctx.bus.publish(ctx.meeting_id, "chat.post", ChatPost(
                chat_id=new_id("chat"), reason="answer", ref_id=answer_id,
                status="suppressed_muted" if muted else "pending",
                text=_truncate(f"Because you asked: {line}", CHAT_LIMIT - 1)))

    # ================= dispute detection =================
    def _enqueue_detection(self, ctx: MeetingContext, st: _MeetingState, lines: list[TranscriptSegment]) -> None:
        if st.detect_queue is None:
            st.detect_queue = asyncio.Queue()
            st.detect_worker = asyncio.get_running_loop().create_task(self._detect_worker(st))
        st.detect_in_progress += 1
        st.detect_queue.put_nowait((ctx, lines))

    async def _detect_worker(self, st: _MeetingState) -> None:
        while True:
            ctx, lines = await st.detect_queue.get()
            try:
                await self._detect(ctx, st, lines)
            except Exception:
                log.exception("Dispute check failed; skipping this sentence")
            finally:
                st.detect_in_progress -= 1
                st.detect_queue.task_done()

    async def _detect(self, ctx: MeetingContext, st: _MeetingState, lines: list[TranscriptSegment]) -> None:
        s = ctx.settings
        segment = lines[-1]
        query = " ".join(l.text for l in lines[-2:])
        try:
            cheap = await self.provider.cheap_check(s.models.cheap_check, lines,
                                                    self.knowledge.search(query, CHEAP_PASSAGES),
                                                    st.flagged_topics)
        except LLMError as exc:
            log.warning("Cheap check failed (%s); no alert for this sentence", exc)
            return
        if not cheap.worth_a_look or cheap.score < s.gate.cheap_threshold:
            return
        if self._continues_recent_alert(st, cheap.topic, segment.t_end, s.gate.cooldown_seconds):
            log.info("Cheap check flagged %r again inside the cooldown; same dispute, judge not called", cheap.topic)
            return
        log.info("Cheap check flagged %r (score %.2f, %s); asking the judge", cheap.topic, cheap.score, cheap.model)
        passages = self.knowledge.search(query, ANSWER_PASSAGES)
        try:
            verdict = await self.provider.judge(s.models.judge, lines, passages, st.flagged_topics)
        except LLMError as exc:
            log.warning("Judge failed (%s); no alert for this sentence", exc)
            return
        if not verdict.is_issue:
            return

        record = ctx.store.get(ctx.meeting_id)
        posted = sum(not a.gated for a in record.alerts) if record else 0
        gate = check_gate(verdict.confidence, segment.t_end, st.last_alert_t, posted, s.gate)
        muted = self._muted(ctx)
        topic = _truncate(verdict.topic or cheap.topic or "a disputed fact", 60)
        finding = verdict.finding or "the documents may disagree."
        said = [lines[i] for i in verdict.segment_indexes if 0 <= i < len(lines)] or [segment]
        alert_id = new_id("alr")
        models = [cheap.model, verdict.model]
        await ctx.bus.publish(ctx.meeting_id, "alert", Alert(
            alert_id=alert_id, kind=verdict.kind, topic=topic, claim=verdict.claim or segment.text,
            said_by=verdict.said_by or [segment.speaker_name], segment_ids=[l.segment_id for l in said],
            finding=finding, evidence=_evidence(passages, verdict.passage_indexes),
            reasoning=verdict.reasoning or "(no reasoning returned)", confidence=verdict.confidence,
            gated=not gate.passed, gate_reason=gate.reason, delivered_to_chat=gate.passed and not muted,
            models_used=list(dict.fromkeys(models)), canned=cheap.canned or verdict.canned))
        if not gate.passed:
            log.info("Alert %r gated: %s", topic, gate.reason)
            return
        st.last_alert_t = segment.t_end
        st.flagged_topics.append(topic)
        tail = " Details in the dashboard."
        if cheap.canned or verdict.canned:
            tail += " (CANNED)"
        head = f"Because you mentioned {topic}: "
        body = _truncate(finding, CHAT_LIMIT - 1 - len(head) - len(tail))
        await ctx.bus.publish(ctx.meeting_id, "chat.post", ChatPost(
            chat_id=new_id("chat"), reason="alert", ref_id=alert_id,
            status="suppressed_muted" if muted else "pending", text=f"{head}{body}{tail}"))

    @staticmethod
    def _continues_recent_alert(st: _MeetingState, topic: str, now_t: float, cooldown: int) -> bool:
        """True when a flagged sentence is the room still talking about the dispute just alerted
        (e.g. "I'm not sure that's right" right after the claim): sharing two or more content
        words with an alerted topic, inside the cooldown. Saves a judge call and a noise alert."""
        if st.last_alert_t is None or now_t - st.last_alert_t >= cooldown:
            return False
        words = set(tokenize(topic))
        return any(len(words & set(tokenize(t))) >= 2 for t in st.flagged_topics)

    # ================= meeting end =================
    async def _summarize(self, ctx: MeetingContext, st: _MeetingState) -> None:
        self._clear_pending(st)
        waiting = [t for t in st.tasks if not t.done() and t is not asyncio.current_task()]
        try:
            if st.detect_queue is not None:
                await asyncio.wait_for(st.detect_queue.join(), END_DRAIN_SECONDS)
            if waiting:
                await asyncio.wait(waiting, timeout=END_DRAIN_SECONDS)
        except asyncio.TimeoutError:
            log.warning("Summary: some checks were still running after %s s; summarising anyway", END_DRAIN_SECONDS)
        record = ctx.store.get(ctx.meeting_id)
        alerts = [a for a in record.alerts if not a.gated]
        try:
            draft = await self.provider.summarize(ctx.settings.models.summary, record.segments, alerts)
            key_topics, takeaways, items, unsettled, canned = (
                draft.key_topics, draft.takeaways, draft.action_items, draft.unsettled_alert_ids, draft.canned)
        except LLMError as exc:
            log.warning("Summary model failed (%s)", exc)
            key_topics = [a.topic for a in alerts]
            takeaways = [f"SUMMARY UNAVAILABLE: the summary model could not be reached ({exc}). "
                         f"The transcript and alerts are complete."]
            items, unsettled, canned = [], [a.alert_id for a in alerts], False

        follow_ups = [FollowUp(follow_up_id=new_id("fu"), text=i.text, owner=i.owner, status="outstanding")
                      for i in items]
        for a in alerts:
            if a.alert_id in unsettled:
                follow_ups.append(FollowUp(
                    follow_up_id=new_id("fu"), text=f"Settle: {a.topic} - {a.finding}",
                    owner=a.said_by[0] if a.said_by else None, source_alert_id=a.alert_id,
                    status="outstanding"))
        for fu in follow_ups:
            await ctx.bus.publish(ctx.meeting_id, "follow_up", fu)
        await ctx.bus.publish(ctx.meeting_id, "meeting.summary", MeetingSummary(
            key_topics=key_topics, takeaways=takeaways, follow_up_count=len(follow_ups),
            alert_count=len(alerts), canned=canned))
        self._forget(ctx.meeting_id)

    # ================= housekeeping =================
    def _state(self, meeting_id: str) -> _MeetingState:
        if meeting_id not in self._states:
            self._states[meeting_id] = _MeetingState()
        return self._states[meeting_id]

    def _forget(self, meeting_id: str) -> None:
        st = self._states.pop(meeting_id, None)
        if st and st.detect_worker is not None:
            st.detect_worker.cancel()

    def _spawn(self, st: _MeetingState, coro) -> asyncio.Task:
        task = asyncio.get_running_loop().create_task(coro)
        st.tasks.add(task)

        def done(t: asyncio.Task) -> None:
            st.tasks.discard(t)
            if not t.cancelled() and t.exception() is not None:
                log.error("Engine task failed", exc_info=t.exception())

        task.add_done_callback(done)
        return task

    @staticmethod
    def _muted(ctx: MeetingContext) -> bool:
        record = ctx.store.get(ctx.meeting_id)
        return bool(record.muted) if record is not None else ctx.muted
