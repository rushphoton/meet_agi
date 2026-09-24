"""
WHY THIS EXISTS
Recognises the two spoken commands: "Hey AGI" (wake up and answer a
question) and "AGI, stop talking". Speech-to-text writes the wake word many
ways ("hey a g i", "hey aji", "Hey AGI."), so the accepted spellings are a
setting (wake.variants, stop_variants).

FAILURE IT PREVENTS
The bot butting in when people merely talk ABOUT it. A "positional guard"
(DESIGN.md §4.5) only accepts the wake phrase at the very start of a
sentence: within the first 3 words, with nothing before it except fillers
like "ok", "so", "um". So "If you say hey AGI it answers" and "The hey AGI
thing is cool" do nothing, while "Okay, hey AGI, what was Q3 revenue?" wakes
it.

No AI model is involved: this must be instant and must never cost money.
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


def detect_stop(text: str, variants: list[str]) -> str | None:
    """Stop phrases may appear anywhere in the sentence (the caller checks the bot is speaking)."""
    padded = f" {normalize(text)} "
    for variant in sorted({normalize(v) for v in variants if normalize(v)}, key=len, reverse=True):
        if f" {variant} " in padded:
            return variant
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
