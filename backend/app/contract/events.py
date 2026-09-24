"""
WHY THIS EXISTS
Defines the closed list of events that flow through the in-process event bus
and out to the dashboard's live stream. Every event has the same envelope
(id, meeting_id, seq, at, type) plus one typed payload.

FAILURE IT PREVENTS
A lane inventing its own event shape that the dashboard or the recorder does
not understand. Adding an event type is a contract change (DESIGN.md §4.1).
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "Evidence", "BotStatus", "TranscriptSegment", "Wake", "Stop", "Mute", "Alert",
    "SpokenAnswer", "ChatPost", "FollowUp", "MeetingSummary", "MeetingEnded",
    "Event", "EVENT_PAYLOADS", "CHAT_LIMIT",
    "BotStatusEvent", "TranscriptSegmentEvent", "WakeEvent", "StopEvent", "MuteEvent",
    "AlertEvent", "SpokenAnswerEvent", "ChatPostEvent", "FollowUpEvent",
    "MeetingSummaryEvent", "MeetingEndedEvent",
]

CHAT_LIMIT = 500  # Google Meet chat limit, verified in Recall docs (DESIGN.md §2 row 9)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Evidence(_Model):
    document: str
    passage: str = Field(max_length=400)
    locator: str | None = None


class BotStatus(_Model):
    status: Literal["joining", "waiting_room", "in_call", "left", "failed"]
    detail: str | None = None
    recall_bot_id: str | None = None


class TranscriptSegment(_Model):
    segment_id: str
    speaker_id: str
    speaker_name: str
    text: str
    t_start: float
    t_end: float
    source: Literal["recall", "replay", "manual"]


class Wake(_Model):
    trigger: Literal["phrase", "button"]
    segment_id: str | None = None
    matched_variant: str | None = None
    question: str | None = None


class Stop(_Model):
    trigger: Literal["phrase", "button"]
    segment_id: str | None = None


class Mute(_Model):
    muted: bool
    by: Literal["dashboard"] = "dashboard"


class Alert(_Model):
    alert_id: str
    kind: Literal["contradiction", "disagreement", "uncertainty"]
    topic: str = Field(max_length=60)
    claim: str
    said_by: list[str]
    segment_ids: list[str]
    finding: str
    evidence: list[Evidence]
    reasoning: str
    confidence: float = Field(ge=0, le=1)
    gated: bool
    gate_reason: str | None = None
    delivered_to_chat: bool
    models_used: list[str]
    canned: bool = False


class SpokenAnswer(_Model):
    answer_id: str
    question: str
    asked_by: str | None = None
    text: str
    evidence: list[Evidence]
    status: Literal["queued", "playing", "played", "stopped", "muted", "failed"]
    canned: bool = False


class ChatPost(_Model):
    chat_id: str
    text: str = Field(max_length=CHAT_LIMIT)
    reason: Literal["alert", "answer", "consent", "system"]
    ref_id: str | None = None
    status: Literal["pending", "sent", "suppressed_muted", "failed"]


class FollowUp(_Model):
    follow_up_id: str
    text: str
    owner: str | None = None
    source_alert_id: str | None = None
    status: Literal["outstanding", "resolved"]


class MeetingSummary(_Model):
    key_topics: list[str]
    takeaways: list[str]
    follow_up_count: int
    alert_count: int
    canned: bool = False


class MeetingEnded(_Model):
    reason: Literal["bot_left", "call_ended", "dashboard", "replay_finished"]


class _Envelope(_Model):
    id: str
    meeting_id: str
    seq: int = Field(ge=1)
    at: datetime


class BotStatusEvent(_Envelope):
    type: Literal["bot.status"] = "bot.status"
    payload: BotStatus


class TranscriptSegmentEvent(_Envelope):
    type: Literal["transcript.segment"] = "transcript.segment"
    payload: TranscriptSegment


class WakeEvent(_Envelope):
    type: Literal["wake"] = "wake"
    payload: Wake


class StopEvent(_Envelope):
    type: Literal["stop"] = "stop"
    payload: Stop


class MuteEvent(_Envelope):
    type: Literal["mute"] = "mute"
    payload: Mute


class AlertEvent(_Envelope):
    type: Literal["alert"] = "alert"
    payload: Alert


class SpokenAnswerEvent(_Envelope):
    type: Literal["spoken.answer"] = "spoken.answer"
    payload: SpokenAnswer


class ChatPostEvent(_Envelope):
    type: Literal["chat.post"] = "chat.post"
    payload: ChatPost


class FollowUpEvent(_Envelope):
    type: Literal["follow_up"] = "follow_up"
    payload: FollowUp


class MeetingSummaryEvent(_Envelope):
    type: Literal["meeting.summary"] = "meeting.summary"
    payload: MeetingSummary


class MeetingEndedEvent(_Envelope):
    type: Literal["meeting.ended"] = "meeting.ended"
    payload: MeetingEnded


Event = Annotated[
    Union[
        BotStatusEvent, TranscriptSegmentEvent, WakeEvent, StopEvent, MuteEvent, AlertEvent,
        SpokenAnswerEvent, ChatPostEvent, FollowUpEvent, MeetingSummaryEvent, MeetingEndedEvent,
    ],
    Field(discriminator="type"),
]

# type string -> (payload model, envelope model). The closed list of event types.
EVENT_PAYLOADS: dict[str, tuple[type[BaseModel], type[_Envelope]]] = {
    "bot.status": (BotStatus, BotStatusEvent),
    "transcript.segment": (TranscriptSegment, TranscriptSegmentEvent),
    "wake": (Wake, WakeEvent),
    "stop": (Stop, StopEvent),
    "mute": (Mute, MuteEvent),
    "alert": (Alert, AlertEvent),
    "spoken.answer": (SpokenAnswer, SpokenAnswerEvent),
    "chat.post": (ChatPost, ChatPostEvent),
    "follow_up": (FollowUp, FollowUpEvent),
    "meeting.summary": (MeetingSummary, MeetingSummaryEvent),
    "meeting.ended": (MeetingEnded, MeetingEndedEvent),
}
