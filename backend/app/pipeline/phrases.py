"""
WHY THIS EXISTS
Recognises the two spoken commands: "Hey AGI" (wake up and answer a
question) and "stop talking". Speech-to-text writes the wake word many ways
("hey a g i", "Hey GI!", "hey giant", "Hey GI Joe", "hey AJ"), so:
- the exact spellings are a setting (wake.variants, stop_variants);
- a FUZZY layer also spots a greeting ("hey", "hi", "ok", "yo", ...) followed
  closely by a word that SOUNDS like "AGI" ("aggie", "ajee", "giant"...). That
  is only a CANDIDATE: the engine asks the cheap model once whether the
  speaker is really talking to the assistant (Ray, 27 Sep: "whenever it says
  hey AGI or similar, it has to know").

FAILURE IT PREVENTS
- The bot butting in when people merely talk ABOUT it. A "positional guard"
  (DESIGN.md §4.5) only accepts the wake phrase at the very start of a
  sentence: within the first 3 words, with nothing before it except fillers
  like "ok", "so", "um". So "If you say hey AGI it answers" and "The hey AGI
  thing is cool" do nothing, while "Okay, hey AGI, what was Q3 revenue?"
  wakes it. The fuzzy layer obeys the same guard.
- The bot ignoring "Hey GI" because the captions misspelled it.
- Ordinary words stopping the bot: bare stop words ("stop", "enough", "hold
  on") only count when they ARE the sentence or open it ("Stop.", "Okay
  stop.", "Stop, thanks") - never "Stop the recording please" or "Fair
  enough". The engine also only listens for them while the bot is speaking.

No AI model is involved in this file: it must be instant and never cost money.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

OPENERS = {"ok", "okay", "so", "um", "uh", "and", "alright"}
BLOCKERS = {"say", "said", "called", "word"}
_WORD = re.compile(r"[a-z0-9]+")


def normalize(text: str) -> str:
    """Lowercase, punctuation to spaces, single spaces (DESIGN.md §4.5)."""
    return " ".join(_WORD.findall(text.lower()))


def _words_with_spans(text: str) -> list[tuple[str, int, int]]:
    return [(m.group(0), m.start(), m.end()) for m in _WORD.finditer(text.lower())]


@dataclass(frozen=True)
class WakeMatch:
    variant: str
    question: str | None   # rest of the sentence after the wake phrase, original wording; None if empty


def detect_wake(text: str, variants: list[str], max_word_position: int = 3) -> WakeMatch | None:
    words = _words_with_spans(text)
    tokens = [w for w, _, _ in words]
    # Longest variants first, so "hey a g i" is not mistaken for a shorter variant.
    candidates = sorted({normalize(v) for v in variants if normalize(v)}, key=lambda v: -len(v.split()))
    for start in range(min(max_word_position, len(tokens))):
        before = tokens[:start]
        if any(w not in OPENERS for w in before) or any(w in BLOCKERS for w in before):
            return None  # something other than a filler word came first: they are talking ABOUT it
        for variant in candidates:
            v = variant.split()
            if tokens[start:start + len(v)] == v:
                end_char = words[start + len(v) - 1][2]
                rest = text[end_char:].strip().lstrip(",.!?;:-– ").strip()
                return WakeMatch(variant=variant, question=rest if _WORD.search(rest.lower()) else None)
    return None


def looks_like_wake_attempt(text: str, max_word_position: int = 3) -> bool:
    """True when a sentence opens like a wake ("Hey Aggie, ...", "Hi AJ ...") - "hey" or "hi"
    within the first words, after fillers only. Used only to LOG likely misses during
    rehearsal, so the spellings speech-to-text really produces can be added in Settings."""
    tokens = normalize(text).split()
    for start in range(min(max_word_position, len(tokens))):
        if tokens[start] in ("hey", "hi"):
            return True
        if tokens[start] not in OPENERS:
            return False
    return False


# ======================= fuzzy wake (Ray, 27 Sep 2026) =======================
GREETINGS = {"hey", "hi", "hay", "ok", "okay", "yo"}   # plus "a", but only as the first word
_CLOSE_PREFIXES = ("ag", "aj", "gi", "ji", "edg", "eg")
_CLOSE_WORDS = {"giant", "gi", "joe", "ajay"}
_CLOSE_TARGETS = ("agi", "agee", "ajee", "aji")         # "a g i" is joined to "agi" before comparing
# Words so close to "AGI" that, if the confirming model call fails or times out, the bot wakes anyway.
STRONG_TOKENS = {"gi", "giant", "aj", "ajay", "edgy", "agee", "agi", "aji", "gijoe"}


def _edit_distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def sounds_like_agi(token: str) -> bool:
    """A single (joined) word that captions might have written for "AGI"."""
    if not token:
        return False
    if token in _CLOSE_WORDS or token.startswith(_CLOSE_PREFIXES):
        return True
    return min(_edit_distance(token, t) for t in _CLOSE_TARGETS) <= 1


@dataclass(frozen=True)
class WakeCandidate:
    variant: str           # e.g. "hey aggie" - what was heard, normalised
    token: str             # the AGI-like word(s), joined: "aggie", "agi" (from "a g i"), "gijoe"
    strong: bool           # wake even if the confirming model call fails
    question: str | None   # rest of the sentence after it, original wording


def fuzzy_wake_candidate(text: str, max_word_position: int = 3) -> WakeCandidate | None:
    """A greeting within the first `max_word_position` words (after fillers only), followed within
    2 words by something that sounds like "AGI". Not a wake by itself: the engine confirms it."""
    words = _words_with_spans(text)
    tokens = [w for w, _, _ in words]
    for g in range(min(max_word_position, len(tokens))):
        before = tokens[:g]
        if any(w not in OPENERS and w not in GREETINGS for w in before) or any(w in BLOCKERS for w in before):
            return None
        greeting = tokens[g]
        if greeting not in GREETINGS and not (greeting == "a" and g == 0):
            continue
        for j in (g + 1, g + 2):
            for span in (3, 2, 1):
                part = tokens[j:j + span]
                if len(part) < span or (span > 1 and any(len(w) > 2 for w in part)):
                    continue  # only spelled-out letters are joined: "a g i", "a gi", "g i"
                joined = "".join(part)
                if not sounds_like_agi(joined):
                    continue
                end = j + span
                if joined == "gi" and end < len(tokens) and tokens[end] == "joe":
                    joined, end = "gijoe", end + 1   # "Hey GI Joe"
                rest = text[words[end - 1][2]:].strip().lstrip(",.!?;:-– ").strip()
                return WakeCandidate(
                    variant=" ".join(tokens[g:end]), token=joined, strong=joined in STRONG_TOKENS,
                    question=rest if _WORD.search(rest.lower()) else None)
    return None


# ======================= stop without "AGI" (Ray, 27 Sep 2026) =======================
# A stop variant containing one of these words may appear anywhere in the sentence; any other
# ("stop", "enough", "hold on", "okay stop") must be the sentence or open it.
_STRONG_STOP_WORDS = {"talking", "quiet", "shut", "agi", "aji"}
_STOP_LEAD_OK = OPENERS | {"please", "oh", "hey", "hmm", "hi"}
_STOP_TRAIL_OK = {"thanks", "thank", "you", "please", "now", "it", "that", "there", "agi", "aji", "a", "g",
                  "i", "sec", "second", "right", "ok", "okay", "bot"}
_BREAK = re.compile(r"\s*[,.!?;:\-–—]")


def _is_strong_stop(variant_tokens: list[str]) -> bool:
    return any(w in _STRONG_STOP_WORDS for w in variant_tokens) or "a g i" in " ".join(variant_tokens)


def detect_stop(text: str, variants: list[str]) -> str | None:
    """The stop variant heard, or None. The caller checks the bot is (about to be) speaking.
    - Distinctive phrases ("stop talking", "be quiet", "AGI stop") count anywhere.
    - Bare ones ("stop", "enough", "hold on") only as the whole sentence or its opening:
      "Stop.", "Okay stop.", "Stop, thanks" count; "Stop the recording please" does not."""
    words = _words_with_spans(text)
    tokens = [w for w, _, _ in words]
    padded = f" {' '.join(tokens)} "
    ordered = sorted({normalize(v) for v in variants if normalize(v)}, key=lambda v: -len(v.split()))
    for variant in ordered:
        vt = variant.split()
        if _is_strong_stop(vt):
            if f" {variant} " in padded:
                return variant
            continue
        for p in range(len(tokens) - len(vt) + 1):
            if any(w not in _STOP_LEAD_OK for w in tokens[:p]):
                break
            if tokens[p:p + len(vt)] != vt:
                continue
            after = tokens[p + len(vt):]
            end_char = words[p + len(vt) - 1][2]
            if all(w in _STOP_TRAIL_OK for w in after) or _BREAK.match(text, end_char):
                return variant
    return None


# ======================= "is this clearly a question?" =======================
_QUESTION_WORDS = {"what", "whats", "how", "why", "when", "where", "who", "whose", "which", "can", "could",
                   "would", "will", "is", "are", "was", "were", "do", "does", "did", "should", "tell",
                   "give", "explain", "remind"}
_QUESTION_LEAD_OK = OPENERS | {"but", "hey", "hi", "then", "sorry", "oh", "hmm", "well"}


def looks_like_question(text: str) -> bool:
    """A question mark, or a sentence opening with a question word ("What was...", "Can you...").
    Used to accept a re-asked question after "Sorry, I didn't catch a question"."""
    if "?" in text:
        return True
    tokens = [t for t in normalize(text).split()]
    for t in tokens:
        if t in _QUESTION_LEAD_OK:
            continue
        return t in _QUESTION_WORDS
    return False
