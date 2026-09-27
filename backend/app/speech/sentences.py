"""
WHY THIS EXISTS
Cuts a spoken answer into sentences, so the bot can speak it as a chain of
short clips instead of one long clip (Ray's live test, 27 Sep 2026).

Why that matters: Attendee has no "stop the audio" call. Once a clip is
handed over it plays to the end. With one clip per answer, "stop talking"
let the whole answer (up to ~40 s at 120 words) finish. With one clip per
sentence, only the sentence already playing finishes (a few seconds); the
rest is never sent.

Rules:
- A sentence ends at . ? ! (plus any closing quote or bracket) followed by a
  space and a word that starts with a capital letter, a digit or a quote.
- Never a split inside a number ("41.2 million", "$1.5M" - no space after
  the dot), after a known abbreviation ("Mr.", "e.g.", "vs."), after dotted
  letters ("U.S.", "U.K."), after a single initial ("J. Smith"), or before a
  lowercase word ("in Q3. revenue then..." is one sentence).
- A piece shorter than MIN_WORDS words ("Yes.", "Sure thing.") is joined to
  its neighbor, so the room never hears a clip that is a single word.

FAILURE IT PREVENTS
The bot talking for 20-40 s after being told to stop (Attendee), and
numbers or abbreviations being read as two broken clips ("41." / "2 million").

DEPENDENCIES (CLAUDE.md rule 4): standard library (re) only. A sentence
tokenizer library (nltk punkt, pysbd) would be justified if answers ever
arrive in languages other than English.
"""
from __future__ import annotations

import re

MIN_WORDS = 4

# Words that end in "." without ending a sentence (compared lowercase, without the final dot).
ABBREVIATIONS = {
    "mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "vs", "etc", "e.g", "i.e", "inc", "ltd",
    "co", "corp", "no", "approx", "est", "dept", "fig", "vol", "jan", "feb", "mar", "apr", "jun",
    "jul", "aug", "sep", "sept", "oct", "nov", "dec", "mon", "tue", "wed", "thu", "fri", "sat", "sun",
}
_DOTTED_LETTERS = re.compile(r"^(?:[A-Za-z]\.){2,}$")          # U.S.  U.K.  e.g.  a.m.
_SINGLE_INITIAL = re.compile(r"^[A-Z]\.$")                      # J.
# a candidate end: sentence punctuation, optional closing quotes/brackets, then whitespace
_END = re.compile(r"[.?!]+[\"')\]”’]*\s+")


def _is_abbreviation(token: str) -> bool:
    if not token.endswith("."):
        return False
    if _DOTTED_LETTERS.match(token) or _SINGLE_INITIAL.match(token):
        return True
    return token[:-1].lower().lstrip("(\"'") in ABBREVIATIONS


def _starts_sentence(rest: str) -> bool:
    ch = rest[:1]
    return bool(ch) and (ch.isupper() or ch.isdigit() or ch in "\"'(“‘$")


def split_raw(text: str) -> list[str]:
    """Sentences as written, before short ones are merged."""
    text = " ".join((text or "").split())
    if not text:
        return []
    pieces, start = [], 0
    for match in _END.finditer(text):
        end = match.end()
        before = text[start:match.start() + 1].split()
        last_token = before[-1] if before else ""
        if match.group(0).lstrip()[:1] == "." and _is_abbreviation(last_token):
            continue
        if not _starts_sentence(text[end:]):
            continue
        pieces.append(text[start:end].strip())
        start = end
    tail = text[start:].strip()
    if tail:
        pieces.append(tail)
    return pieces


def split_sentences(text: str, min_words: int = MIN_WORDS) -> list[str]:
    """Sentences to speak one clip each. Joining them with spaces gives back the
    (whitespace-normalised) text, so nothing is ever dropped or reworded."""
    pieces = split_raw(text)
    merged: list[str] = []
    carry = ""
    for piece in pieces:
        piece = f"{carry} {piece}".strip() if carry else piece
        if len(piece.split()) < min_words:
            carry = piece           # too short on its own: join it to the next sentence
            continue
        merged.append(piece)
        carry = ""
    if carry:                       # a short last piece joins the sentence before it
        if merged:
            merged[-1] = f"{merged[-1]} {carry}"
        else:
            merged.append(carry)
    return merged
