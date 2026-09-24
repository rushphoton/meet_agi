"""
WHY THIS EXISTS
The filler bank: short lines like "Sure, let me look that up." that the bot
says the moment someone calls "Hey AGI", so the room knows it heard while the
real answer is being prepared. The lines come from Settings.fillers and are
synthesised once and kept in memory, so playing one costs no vendor call and
no waiting (DESIGN.md §8 decision 6).

For the fake meeting (or OFFLINE=1) the cached clip is the canned sample
clip, whose audio says it is canned (CLAUDE.md rule 6). For a real call a
failing voice caches nothing, and the audio queue skips the filler.

FAILURE IT PREVENTS
Several seconds of silence after "Hey AGI" (people repeat themselves or talk
over the answer), and paying for the same three sentences every meeting.

DEPENDENCIES (CLAUDE.md rule 4): standard library + the voice provider.
"""
from __future__ import annotations

import asyncio
import itertools

from ..providers.voice import VoiceClip, VoiceService


class FillerBank:
    def __init__(self, voice: VoiceService) -> None:
        self.voice = voice
        self._cache: dict[tuple[str, str, str, bool], VoiceClip] = {}
        self._turn = itertools.count()

    async def clip(self, text: str, voice_id: str, model_id: str, allow_vendor: bool) -> VoiceClip:
        key = (text, voice_id, model_id, allow_vendor)
        if key in self._cache:
            return self._cache[key]
        # raises VoiceError for a real call whose voice is failing; a failure is never cached
        clip = await self.voice.synthesize(text, voice_id=voice_id, model_id=model_id, allow_vendor=allow_vendor)
        self._cache[key] = clip
        return clip

    def next_line(self, lines: list[str]) -> str:
        lines = [line for line in lines if line.strip()] or ["One moment."]
        return lines[next(self._turn) % len(lines)]

    async def warm(self, lines: list[str], voice_id: str, model_id: str, allow_vendor: bool) -> None:
        """Synthesise every filler ahead of time (called when a real bot is sent)."""
        await asyncio.gather(*(self.clip(line, voice_id, model_id, allow_vendor) for line in lines),
                             return_exceptions=True)   # a failing voice is reported via rt.warnings

    def cached(self) -> int:
        return len(self._cache)
