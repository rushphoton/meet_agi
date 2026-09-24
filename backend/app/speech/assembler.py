"""
WHY THIS EXISTS
Recall sends finished pieces of speech ("utterances"), and a piece does not
always line up with a sentence: someone can pause mid-sentence, or talk for
a minute without stopping. The assembler glues pieces from the same speaker
into finished sentences, each with the speaker's name, before the thinker
(engine) sees them.

A sentence is handed on when ANY of these happens (DESIGN.md §3.3 step 4):
  - a pause: the end of a finished Recall utterance that ends in . ? or !,
    or a gap of 1.2 s or more between two words (then it goes even without
    punctuation);
  - maximum length: 40 words (cut after the last . ? ! inside, if any);
  - a different speaker starts talking;
  - nothing more arrives for a while (the receiver calls flush()).

Several sentences inside one unbroken utterance stay together as one line,
because the speaker did not pause between them (see the assumption in the
lane report). That keeps the fake meeting at exactly 25 transcript lines.

FAILURE IT PREVENTS
The engine judging half-sentences ("Q3 revenue was") and missing disputes or
wake phrases, or waiting forever for a monologue to end.

DEPENDENCIES (CLAUDE.md rule 4): standard library only.
"""
from __future__ import annotations

from dataclasses import dataclass, field

PAUSE_SECONDS = 1.2
MAX_WORDS = 40
_TRAILING = "\"')]}»”’"


@dataclass
class Word:
    text: str
    start: float
    end: float


@dataclass
class Sentence:
    speaker_id: str
    speaker_name: str
    text: str
    t_start: float
    t_end: float


def ends_sentence(text: str) -> bool:
    return text.rstrip(_TRAILING).endswith((".", "?", "!", "…"))


@dataclass
class SentenceAssembler:
    pause_seconds: float = PAUSE_SECONDS
    max_words: int = MAX_WORDS
    _speaker: tuple[str, str] | None = None
    _words: list[Word] = field(default_factory=list)

    @property
    def has_pending(self) -> bool:
        return bool(self._words)

    def add(self, speaker_id: str, speaker_name: str, words: list[Word]) -> list[Sentence]:
        """Feed one finished utterance; returns the sentences it completes (maybe none)."""
        out: list[Sentence] = []
        words = [w for w in words if w.text.strip()]
        if not words:
            return out
        if self._words and self._speaker and self._speaker[0] != speaker_id:
            out += self.flush()
        self._speaker = (speaker_id, speaker_name)
        for word in words:
            if self._words and word.start - self._words[-1].end >= self.pause_seconds:
                out += self.flush()
                self._speaker = (speaker_id, speaker_name)
            self._words.append(word)
            if len(self._words) >= self.max_words:
                out += self._flush_long()
        if self._words and ends_sentence(self._words[-1].text):
            out += self.flush()
        return out

    def flush(self) -> list[Sentence]:
        """Hand on whatever is buffered as one sentence (used on pause, speaker change, end)."""
        if not self._words or self._speaker is None:
            self._words = []
            return []
        words, self._words = self._words, []
        return [self._sentence(words)]

    def _flush_long(self) -> list[Sentence]:
        cut = max((i for i, w in enumerate(self._words) if ends_sentence(w.text)), default=-1)
        if cut == -1:
            return self.flush()
        head, self._words = self._words[:cut + 1], self._words[cut + 1:]
        return [self._sentence(head)]

    def _sentence(self, words: list[Word]) -> Sentence:
        assert self._speaker is not None
        return Sentence(speaker_id=self._speaker[0], speaker_name=self._speaker[1],
                        text=" ".join(w.text.strip() for w in words),
                        t_start=words[0].start, t_end=words[-1].end)
