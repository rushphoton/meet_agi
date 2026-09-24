"""
WHY THIS EXISTS
Loads .md / .txt / .pdf files from the knowledge folder, splits each into
passages of about 120 words (20 words of overlap, so a fact is never cut in
half at a boundary), remembers the nearest heading above each passage (so
evidence can say "Revenue summary" rather than "somewhere in the file"), and
ranks passages for a query with BM25 - the classic keyword-relevance formula
search engines used before AI. DESIGN.md §8 decision 2.

FAILURE IT PREVENTS
- Answers without a source: every passage carries its document name.
- A stale index: before each search it checks the folder's file list and
  modification times and rebuilds if anything changed (cheap for a handful
  of documents).
- One unreadable file breaking all search: a file that cannot be read is
  logged and skipped.

DEPENDENCIES (CLAUDE.md rule 4)
Standard library only. No vector database: it costs setup, money and a new
failure point, and keyword search is enough for a few board-deck-sized
documents. What would justify one: dozens of long documents, or questions
phrased with words the documents never use (synonyms), measured as misses at
milestone 4.
PDF text uses the optional `pypdf` package if it is installed. It is NOT in
backend/requirements.txt (a shared file this lane may not edit); without it
PDFs are skipped with a log line. Cost of adding it: one pure-Python package,
free. Justified as soon as Ray uploads a PDF he expects the bot to read.
"""
from __future__ import annotations

import logging
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("meet_agi.knowledge")

ALLOWED = {".md", ".txt", ".pdf"}
CHUNK_WORDS = 120
OVERLAP_WORDS = 20
_TOKEN = re.compile(r"[a-z0-9]+(?:\.[0-9]+)?")
_STOP = set(
    "a an the and or but if of to in on at by for with from as is are was were be been being it its "
    "this that these those i you he she we they me him her us them my your our their what which who "
    "whom how when where why do does did so than then there here about into over under up down not no "
    "can could would should will just very really also any some according".split()
)
# "up" and "down" are stop words for ranking only; the detectors see the raw sentence.


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOP]


@dataclass(frozen=True)
class Passage:
    document: str
    text: str
    locator: str | None
    score: float = 0.0


@dataclass
class _Chunk:
    document: str
    text: str
    locator: str | None
    tokens: list[str] = field(default_factory=list)


class KnowledgeBase:
    """Search over the files in one folder. Thread-safe enough for one event loop."""

    def __init__(self, folder: Path) -> None:
        self.folder = Path(folder)
        self._signature: tuple = ()
        self._chunks: list[_Chunk] = []
        self._df: Counter = Counter()
        self._avg_len = 0.0

    # ---------- public ----------
    def search(self, query: str, k: int = 5) -> list[Passage]:
        self._refresh()
        q = tokenize(query)
        if not q or not self._chunks:
            return []
        n = len(self._chunks)
        k1, b = 1.5, 0.75
        scored = []
        for chunk in self._chunks:
            tf = Counter(chunk.tokens)
            length = len(chunk.tokens) or 1
            score = 0.0
            for term in set(q):
                if term not in tf:
                    continue
                idf = math.log(1 + (n - self._df[term] + 0.5) / (self._df[term] + 0.5))
                f = tf[term]
                score += idf * f * (k1 + 1) / (f + k1 * (1 - b + b * length / self._avg_len))
            if score > 0:
                scored.append((score, chunk))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [Passage(c.document, c.text, c.locator, round(s, 3)) for s, c in scored[:k]]

    def documents(self) -> list[str]:
        self._refresh()
        return sorted({c.document for c in self._chunks})

    def chunk_count(self, document: str) -> int:
        self._refresh()
        return sum(c.document == document for c in self._chunks)

    # ---------- building ----------
    def _files(self) -> list[Path]:
        if not self.folder.is_dir():
            return []
        return sorted(p for p in self.folder.iterdir()
                      if p.is_file() and p.suffix.lower() in ALLOWED and p.name != "README.md")

    def _refresh(self) -> None:
        files = self._files()
        signature = tuple((p.name, p.stat().st_mtime_ns, p.stat().st_size) for p in files)
        if signature == self._signature:
            return
        chunks: list[_Chunk] = []
        for path in files:
            try:
                chunks.extend(chunk_document(path.name, read_text(path)))
            except Exception as exc:  # one bad file must not break search for the others
                log.warning("Skipping unreadable document %s: %s", path.name, exc)
        for c in chunks:
            c.tokens = tokenize(c.text)
        self._chunks = chunks
        self._df = Counter(t for c in chunks for t in set(c.tokens))
        self._avg_len = (sum(len(c.tokens) for c in chunks) / len(chunks)) if chunks else 1.0
        self._signature = signature
        log.info("Knowledge index: %d documents, %d passages", len(files), len(chunks))


def read_text(path: Path) -> str:
    if path.suffix.lower() == ".pdf":
        try:
            from pypdf import PdfReader  # optional, see module docstring
        except ImportError as exc:
            raise RuntimeError("pypdf is not installed, so PDFs cannot be read yet") from exc
        return "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)
    return path.read_text(encoding="utf-8-sig", errors="replace")


def chunk_document(name: str, text: str) -> list[_Chunk]:
    """Split at Markdown headings first (so a passage never mixes two sections), then
    cut each section into ~120-word windows with 20 words of overlap."""
    sections: list[tuple[str | None, list[str]]] = [(None, [])]
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            sections.append((stripped.lstrip("#").strip() or None, []))
            continue
        if stripped:
            sections[-1][1].append(stripped)
    chunks: list[_Chunk] = []
    step = CHUNK_WORDS - OVERLAP_WORDS
    for heading, lines in sections:
        words = " \n ".join(lines).split(" ")
        real = [i for i, w in enumerate(words) if w and w != "\n"]
        start = 0
        while start < len(real):
            end = min(start + CHUNK_WORDS, len(real))
            piece = " ".join(words[real[start]: real[end - 1] + 1]).replace(" \n ", "\n").strip()
            chunks.append(_Chunk(document=name, text=piece, locator=heading))
            if end == len(real):
                break
            start += step
    return chunks
