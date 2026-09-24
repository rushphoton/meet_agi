"""
WHY THIS EXISTS
Defines the long-lived shapes: the meeting record (what one JSON file on disk
holds), the sessions-list row, settings, documents, and the request/response
bodies of the REST API.

FAILURE IT PREVENTS
The review screen and the recorder disagreeing about what a meeting contains,
or settings silently losing a field between the dashboard and the backend.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .events import (
    Alert, ChatPost, FollowUp, MeetingSummary, SpokenAnswer, TranscriptSegment,
)

__all__ = [
    "Participant", "MeetingRecord", "MeetingListItem", "SpeakerMapping", "ModelSettings",
    "VoiceSettings", "GateSettings", "WakeSettings", "Settings", "DocumentInfo",
    "CreateMeetingRequest", "ReplayMeetingRequest", "WakeRequest", "MuteRequest",
    "FollowUpPatch", "Health", "Ok",
]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Participant(_Model):
    speaker_id: str
    recall_name: str | None = None
    display_name: str
    role: str | None = None


class MeetingRecord(_Model):
    meeting_id: str
    title: str
    meeting_url: str | None = None
    source: Literal["recall", "replay"]
    recall_bot_id: str | None = None
    bot_status: Literal["joining", "waiting_room", "in_call", "left", "failed"] | None = None
    muted: bool = False
    started_at: datetime
    ended_at: datetime | None = None
    participants: list[Participant] = []
    segments: list[TranscriptSegment] = []
    alerts: list[Alert] = []
    answers: list[SpokenAnswer] = []
    chat_posts: list[ChatPost] = []
    follow_ups: list[FollowUp] = []
    summary: MeetingSummary | None = None
    events_last_seq: int = 0


class MeetingListItem(_Model):
    """Field order is the sessions-list display order: follow-ups before alerts."""
    meeting_id: str
    title: str
    started_at: datetime
    participants: list[str]
    follow_ups_outstanding: int
    follow_ups_resolved: int
    alert_count: int


class SpeakerMapping(_Model):
    match_name: str
    display_name: str
    role: str | None = None


class ModelSettings(_Model):
    cheap_check: str = "gemini-3.5-flash-lite"
    judge: str = "claude-haiku-4-5-20251001"
    answer: str = "claude-haiku-4-5-20251001"
    summary: str = "claude-haiku-4-5-20251001"


class VoiceSettings(_Model):
    provider: Literal["inworld"] = "inworld"
    model: str = "inworld-tts-2"
    voice_id: str = "Grant"


class GateSettings(_Model):
    cheap_threshold: float = Field(default=0.5, ge=0, le=1)
    min_confidence: float = Field(default=0.75, ge=0, le=1)
    cooldown_seconds: int = Field(default=90, ge=0)
    max_alerts_per_meeting: int = Field(default=8, ge=0)


class WakeSettings(_Model):
    variants: list[str] = [
        "hey agi", "hey a g i", "hey aji", "hey age i", "hey a gi", "hey ag i", "hi agi", "hey agee",
    ]
    max_word_position: int = 3
    question_wait_seconds: int = 8


class Settings(_Model):
    bot_name: str = "Meet AGI"
    consent_text: str = Field(
        default='Meet AGI is listening to help with facts from our documents. It may post short '
                'notes here. Say "Hey AGI" to ask it something, or "AGI, stop talking" to stop it.',
        max_length=500,
    )
    speakers: list[SpeakerMapping] = []
    models: ModelSettings = ModelSettings()
    voice: VoiceSettings = VoiceSettings()
    gate: GateSettings = GateSettings()
    wake: WakeSettings = WakeSettings()
    stop_variants: list[str] = [
        "agi stop talking", "a g i stop talking", "aji stop talking", "agi stop", "stop talking agi",
    ]
    answer_max_words: int = 60
    fillers: list[str] = [
        "Sure, let me look that up.", "One moment, checking the documents.",
        "Good question, give me a second.",
    ]


class DocumentInfo(_Model):
    name: str
    size_bytes: int
    chunks: int
    added_at: datetime


class CreateMeetingRequest(_Model):
    meeting_url: str
    title: str | None = None


class ReplayMeetingRequest(_Model):
    title: str = "Fake meeting (replay)"


class WakeRequest(_Model):
    question: str | None = None


class MuteRequest(_Model):
    muted: bool


class FollowUpPatch(_Model):
    status: Literal["outstanding", "resolved"]


class Health(_Model):
    ok: bool
    offline: bool
    dev_mode: bool
    placeholders: list[str]


class Ok(_Model):
    ok: bool = True
