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
- wake check (a sentence that only SOUNDED like "Hey AGI"): yes only when the
  AGI-like word is one of the strong ones ("gi", "giant", "aj", "ajay",
  "edgy", "agee", "agi", "aji") - the same rule the engine falls back to when
  the real check fails. "Hey Jim", "Hi Gina" and "Hey Aggie" are "no".
- cheap check: a sentence that states a direction ("rising", "fell", ...)
  about a business metric ("revenue", "bookings", ...) is worth a look; so is
  a correction ("no wait, that's wrong") or a doubt ("not sure that's
  correct") that follows such a claim (see "self-correction" below).
- judge, rule 1: someone says Q3 revenue was rising/up/grew -> contradiction
  with the sample board deck (the fake meeting's one planted dispute).
- judge, rule 2 (self-correction and doubt, Ray 27 Sep): a line with a
  correction marker ("no wait", "that's wrong", "not correct", "my mistake"),
  after a metric claim BY THE SAME SPEAKER in the last 4 lines (or in the
  same line) -> "contradiction"; a line with a doubt marker ("not sure",
  "unsure", "I don't think that's right") after a metric claim by ANYONE in
  the last 4 lines -> "uncertainty". The alert quotes what the documents
  say about that metric when they mention it, and otherwise says the
  documents don't settle it. It never fires for a metric that is already in
  an alerted topic - which is why the fake meeting (Priya's "I'm not sure
  that's right" right after the alerted Q3 claim) still has exactly one alert.
  Everything else is "no issue".
- answer: reads back the one document line sharing most words with the
  question; when nothing matches, its first sentence (after the "CANNED
  ANSWER:" label every canned answer carries) is "That's not in your
  documents, but generally ..." and it says no model was called.
- summary: topics from the alerts; follow-ups from "I'll ..." and
  "..., please ..." sentences; an alert counts as settled if someone later
  says "my mistake", "you're right" or "fair enough".
"""
from __future__ import annotations

import re

from ...contract.events import Alert, TranscriptSegment
from ...knowledge import Passage
from ...knowledge.index import tokenize
from ...pipeline.phrases import STRONG_TOKENS
from .base import NOT_IN_DOCS, ActionItem, AnswerDraft, CheapCheck, SummaryDraft, Verdict, WakeCheck

MODEL = "canned"
_METRIC = re.compile(r"\b(revenue|bookings|margin|churn|pipeline|sales|profit|arr)\b")
_DIRECTION = re.compile(r"\b(rising|rose|grew|growing|up|increased|fell|falling|down|dropped|declined|decreased)\b")
_Q3_UP = re.compile(r"\bq3 revenue\b.*\b(rising|rose|grew|growing|up|increased)\b")
_CORRECTION = re.compile(r"\b(no wait|wait no|that's wrong|that is wrong|thats wrong|that's not right|"
                         r"that is not right|not correct|isn't correct|is not correct|my mistake|scratch that|"
                         r"sorry,? i meant|this is wrong|that's incorrect|that is incorrect)\b")
_DOUBT = re.compile(r"\b(not sure|unsure|don't think that's right|dont think thats right|"
                    r"not certain|i doubt|can't remember if|might be wrong)\b")
_LOOKBACK = 4
_SETTLED = re.compile(r"\b(my mistake|you're right|you are right|fair enough)\b")
_WILL = re.compile(r"^(i'll|i will)\b", re.I)
_PLEASE = re.compile(r"\bplease\b", re.I)


class CannedProvider:
    name = "canned"
    canned = True

    def __init__(self, reason: str = "canned provider") -> None:
        self.reason = reason

    async def confirm_wake(self, model, text: str, heard: str, lines) -> WakeCheck:
        return WakeCheck(addressed=heard in STRONG_TOKENS, question=None, model=MODEL, canned=True)

    async def cheap_check(self, model, lines: list[TranscriptSegment], passages, already_flagged,
                          new_lines: int = 1) -> CheapCheck:
        n = max(1, min(new_lines, len(lines)))
        for i in reversed(range(len(lines) - n, len(lines))):
            text = lines[i].text.lower()
            if _METRIC.search(text) and _DIRECTION.search(text):
                return CheapCheck(worth_a_look=True, score=0.9, topic=_METRIC.search(text).group(1),
                                  model=MODEL, canned=True)
            found = _self_correction(lines, i)
            if found:
                return CheapCheck(worth_a_look=True, score=0.9, topic=found[2], model=MODEL, canned=True)
        return CheapCheck(worth_a_look=False, score=0.0, topic="", model=MODEL, canned=True)

    async def judge(self, model, lines: list[TranscriptSegment], passages: list[Passage],
                    already_flagged, new_lines: int = 1) -> Verdict:
        n = max(1, min(new_lines, len(lines)))
        hits = [i for i in range(len(lines) - n, len(lines)) if _Q3_UP.search(lines[i].text.lower())]
        if not hits or any(t.lower().startswith("q3 revenue") for t in already_flagged):
            for i in reversed(range(len(lines) - n, len(lines))):
                found = _self_correction(lines, i)
                if found and not any(found[2] in t.lower() for t in already_flagged):
                    return self._self_correction_verdict(lines, passages, i, *found)
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

    def _self_correction_verdict(self, lines, passages: list[Passage], i: int, claim_i: int, kind: str,
                                 metric: str) -> Verdict:
        speaker = lines[i].speaker_name
        claim = lines[claim_i]
        both = claim_i != i or kind == "contradiction"
        topic = (f"{metric} ({speaker} said it both ways)" if kind == "contradiction"
                 else f"{metric} ({speaker} was unsure)")
        doc_hits = [(j, line) for j, p in enumerate(passages) for line in p.text.splitlines()
                    if metric in line.lower() and re.search(r"[$%]", line)]
        if doc_hits:
            j, line = doc_hits[0]
            finding = f"{_doc_title(passages[j].document)} says: {line.strip('- ').strip()}"
            where, pidx, conf = f"{passages[j].document} settles it", [j], 0.9
        else:
            finding = (f"{speaker} said {metric} both ways" if kind == "contradiction"
                       else f"{speaker} was unsure about {metric}") + "; the documents don't settle it."
            where, pidx, conf = "no document mentions it", [], 0.8
        return Verdict(
            is_issue=True, kind=kind, topic=topic, claim=claim.text, said_by=[speaker],
            segment_indexes=sorted({claim_i, i}) if both else [i], finding=finding,
            reasoning=(f"CANNED REASONING (no model was called; {self.reason}). Canned rule 2 "
                       f"(self-correction / doubt): {claim.speaker_name} said {claim.text!r}, then "
                       f"{speaker} said {lines[i].text!r}. {where}."),
            confidence=conf, passage_indexes=pidx, model=MODEL, canned=True)

    async def answer(self, model, question: str, asked_by, passages: list[Passage], max_words: int,
                     context=None) -> AnswerDraft:
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
            # "CANNED ANSWER:" stays in front, as on every canned answer (other lanes' tests key on it);
            # the first real sentence is the not-in-your-documents one.
            spoken = (f"CANNED ANSWER: {NOT_IN_DOCS}, I can't say more: no model was called to answer "
                      f"from general knowledge.")
            return AnswerDraft(spoken=spoken, chat_line="Not in your documents; no general answer (CANNED).",
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


def _self_correction(lines: list[TranscriptSegment], i: int) -> tuple[int, str, str] | None:
    """(index of the claim, kind, metric) when line i corrects or doubts a metric claim."""
    text = lines[i].text.lower()
    correction, doubt = _CORRECTION.search(text), _DOUBT.search(text)
    if not (correction or doubt):
        return None
    for j in range(i, max(-1, i - _LOOKBACK - 1), -1):
        prior = lines[j].text.lower()
        if j == i:
            prior = prior[:(correction or doubt).start()]   # the claim must come BEFORE the marker
        if not (_METRIC.search(prior) and (_DIRECTION.search(prior) or re.search(r"\d|million|percent", prior))):
            continue
        if correction and lines[j].speaker_id == lines[i].speaker_id:
            return j, "contradiction", _METRIC.search(prior).group(1)
        if doubt:
            return j, "uncertainty", _METRIC.search(prior).group(1)
    return None


def _doc_title(name: str) -> str:
    return name.rsplit(".", 1)[0].replace("SAMPLE_", "").replace("_", " ")
