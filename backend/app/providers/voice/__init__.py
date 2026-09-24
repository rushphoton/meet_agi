"""
WHY THIS EXISTS
Turns text into an MP3 clip the bot can play into the meeting. Two voices:

- Inworld (the real voice, DESIGN.md §3.5), called over HTTPS with an 8 s
  timeout (risk R5).
- A CANNED sample clip (assets/canned_sample_clip.mp3). Its audio literally
  says "This is a canned sample clip, not the real voice. The voice service
  is unavailable, so the answer is in the chat." It is used when OFFLINE=1,
  when there is no INWORLD_API_KEY, when the meeting is the fake (replay)
  meeting, and whenever Inworld fails.

VoiceService.synthesize() never raises: a broken voice vendor must not
silence the chat answer or crash the meeting.

FAILURE IT PREVENTS
The bot going quiet (or crashing) mid-demo because the voice vendor timed
out, and canned audio being mistaken for the real thing (CLAUDE.md rule 6).

DEPENDENCIES (CLAUDE.md rule 4): httpx only (already installed for tests).
The canned clip was made once with `ffmpeg -f lavfi -i "flite=text='...'"`
(free, local, no vendor); ffmpeg is NOT needed at run time.
"""
from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from pathlib import Path

import httpx

from .mp3 import mp3_duration

log = logging.getLogger("meet_agi.voice")

ASSETS = Path(__file__).resolve().parent / "assets"
CANNED_CLIP_TEXT = ("This is a canned sample clip, not the real voice. The voice service is "
                    "unavailable, so the answer is in the chat.")
INWORLD_URL = "https://api.inworld.ai/tts/v1/voice"
INWORLD_TEXT_LIMIT = 2000
VENDOR_TIMEOUT_SECONDS = 8.0


@dataclass(frozen=True)
class VoiceClip:
    mp3: bytes
    duration: float
    canned: bool
    provider: str          # "inworld" or "canned"
    note: str = ""         # why a canned clip was used, if it was

    @property
    def b64(self) -> str:
        return base64.b64encode(self.mp3).decode("ascii")


def _load(name: str) -> bytes:
    return (ASSETS / name).read_bytes()


def canned_clip(reason: str) -> VoiceClip:
    data = _load("canned_sample_clip.mp3")
    log.warning("CANNED voice: playing the sample clip that says it is canned (%s)", reason)
    return VoiceClip(mp3=data, duration=mp3_duration(data), canned=True, provider="canned", note=reason)


def silent_mp3() -> bytes:
    """Half a second of silence. Recall needs *some* clip in automatic_audio_output
    at bot creation, or it refuses to play output_audio later (DESIGN.md §2 row 7)."""
    return _load("silence_half_second.mp3")


class VoiceError(Exception):
    pass


class InworldVoice:
    """Inworld TTS: POST /tts/v1/voice -> {"audioContent": base64 MP3}."""

    def __init__(self, api_key: str, transport: httpx.AsyncBaseTransport | None = None,
                 timeout: float = VENDOR_TIMEOUT_SECONDS) -> None:
        self._key = api_key
        self._transport = transport
        self._timeout = timeout

    async def synthesize(self, text: str, voice_id: str, model_id: str) -> bytes:
        body = {"text": text[:INWORLD_TEXT_LIMIT], "voiceId": voice_id, "modelId": model_id,
                "audioConfig": {"audioEncoding": "MP3"}}
        headers = {"Authorization": f"Basic {self._key}", "Content-Type": "application/json"}
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=self._timeout) as client:
                r = await client.post(INWORLD_URL, json=body, headers=headers)
        except httpx.HTTPError as exc:
            raise VoiceError(f"Inworld unreachable: {type(exc).__name__}") from None
        if r.status_code != 200:
            raise VoiceError(f"Inworld answered HTTP {r.status_code}")
        try:
            audio = base64.b64decode(r.json()["audioContent"])
        except (ValueError, KeyError, TypeError):
            raise VoiceError("Inworld answer had no usable audioContent") from None
        if not audio:
            raise VoiceError("Inworld returned empty audio")
        return audio


class VoiceService:
    """Picks the real voice when allowed, and falls back to the canned clip (which says so)."""

    def __init__(self, inworld: InworldVoice | None, offline: bool, fallback_voice_id: str = "") -> None:
        self.inworld = inworld
        self.offline = offline
        self.fallback_voice_id = fallback_voice_id

    @property
    def real_voice_available(self) -> bool:
        return self.inworld is not None and not self.offline

    async def synthesize(self, text: str, *, voice_id: str, model_id: str, allow_vendor: bool) -> VoiceClip:
        if self.offline:
            return canned_clip("OFFLINE=1")
        if self.inworld is None:
            return canned_clip("no INWORLD_API_KEY")
        if not allow_vendor:
            return canned_clip("fake meeting: no real audio destination")
        try:
            audio = await self.inworld.synthesize(text, voice_id or self.fallback_voice_id, model_id)
        except VoiceError as exc:
            return canned_clip(str(exc))
        return VoiceClip(mp3=audio, duration=mp3_duration(audio), canned=False, provider="inworld")
