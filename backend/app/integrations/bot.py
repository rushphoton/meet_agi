"""
WHY THIS EXISTS
Sends the "Meet AGI" bot into a Google Meet and takes it out again, through
Recall.ai (DESIGN.md §2 rows 1, 2, 5, 7, 10, 11):

- launch(): creates the bot named "Meet AGI" with real-time transcription
  (finished lines only, one audio stream per person), the webhook address
  PUBLIC_BASE_URL/webhooks/recall/<token>, a half-second silent clip (Recall
  only lets a bot play audio later if it was given one at creation), and the
  consent notice posted to chat when it joins. Then records the meeting.
- status(): asks Recall where the bot is now.
- watch(): after launch, asks Recall every 3 s where the bot is and
  publishes bot.status whenever it changes, and meeting.ended when Recall
  says the call ended (review B item 2). Recall's status webhooks are set up
  once in Recall's dashboard, not per bot, so without this the dashboard
  would show "joining" forever and the meeting would never end by itself.
  A status that arrives both by webhook and by polling is published once
  (publish_status() compares with the meeting's current status).
- leave(): tells the bot to leave the call (used by the dashboard's End).
- end_all(): makes every bot we still think is in a call leave - the
  "panic button" after a crash or a rehearsal. Reach it from the Runtime
  with integrations.register.lane_for(rt).bot.end_all().

Missing settings (RECALL_API_KEY, PUBLIC_BASE_URL, RECALL_WEBHOOK_TOKEN) stop
launch() with a plain message naming the setting, never its value.

The same lifecycle also drives Attendee (DESIGN risk R1 fallback, selected by
BOT_PROVIDER=attendee in integrations/register.py). Everything that differs
between the two vendors - the create-bot body, how a bot's status reads, which
listed bots are ours, which key is needed - sits in a small "vendor" object:
RecallVendor below, AttendeeVendor in attendee_client.py.

FAILURE IT PREVENTS
A bot joining without transcription or without the ability to speak (both
must be requested at creation and cannot be added later); a bot left running
(and billing) in a meeting after the demo; a key leaking in an error.

DEPENDENCIES (CLAUDE.md rule 4): FastAPI's HTTPException, so the REST route
shows a clear error; no new packages.
"""
from __future__ import annotations

import asyncio
import base64
import logging
from typing import Awaitable, Callable

from fastapi import HTTPException

from ..contract.events import BotStatus, MeetingEnded
from ..contract.records import CreateMeetingRequest, MeetingRecord, Settings
from ..providers.voice import silent_mp3
from .recall_client import RecallError

log = logging.getLogger("meet_agi.bot")
POLL_SECONDS = 3.0
RECALL_WARNING = "meeting.recall"
ENDED_REASON = {"call_ended": "call_ended", "done": "call_ended", "fatal": "bot_left"}

# Recall status codes -> the contract's BotStatus.status (DESIGN.md §4.3)
STATUS_CODES = {
    "ready": "joining",
    "joining_call": "joining",
    "in_waiting_room": "waiting_room",
    "in_call_not_recording": "in_call",
    "in_call_recording": "in_call",
    "recording_permission_allowed": "in_call",
    "call_ended": "left",
    "done": "left",
    "fatal": "failed",
}


def webhook_url(public_base_url: str, token: str) -> str:
    return f"{public_base_url.rstrip('/')}/webhooks/recall/{token}"


def build_create_bot_payload(meeting_url: str, settings: Settings, hook_url: str) -> dict:
    """The body of POST /api/v1/bot/ (field names from docs.recall.ai, read 24 Sep 2026)."""
    return {
        "meeting_url": meeting_url,
        "bot_name": settings.bot_name or "Meet AGI",
        "recording_config": {
            "transcript": {
                "provider": {"recallai_streaming": {"mode": "prioritize_low_latency", "language_code": "en"}},
                "diarization": {"use_separate_streams_when_available": True},
            },
            # finalized lines only: transcript.partial_data is deliberately NOT requested
            "realtime_endpoints": [{"type": "webhook", "url": hook_url, "events": ["transcript.data"]}],
        },
        "automatic_audio_output": {
            "in_call_recording": {"data": {"kind": "mp3", "b64_data": base64.b64encode(silent_mp3()).decode()}},
        },
        "chat": {"on_bot_join": {"send_to": "everyone", "message": settings.consent_text}},
    }


class RecallVendor:
    """What BotLifecycle needs to know about Recall (the default vendor)."""

    name = "Recall"
    key_env = "RECALL_API_KEY"
    status_warning_key = RECALL_WARNING

    def check(self, config) -> list[str]:
        return []

    def create_payload(self, meeting_url: str, settings: Settings, hook_url: str) -> dict:
        return build_create_bot_payload(meeting_url, settings, hook_url)

    def read_status(self, bot: dict) -> tuple[str | None, str | None, str | None]:
        code, sub_code = latest_code(bot)
        return STATUS_CODES.get(code), sub_code, ENDED_REASON.get(code)

    def is_ours(self, bot: dict, bot_name: str) -> bool:
        return bot.get("bot_name") == bot_name


def latest_code(bot: dict) -> tuple[str, str | None]:
    changes = bot.get("status_changes") or []
    last = (changes[-1] or {}) if changes else (bot.get("status") or {})
    return str(last.get("code") or ""), last.get("sub_code")


def latest_status(bot: dict) -> str | None:
    return STATUS_CODES.get(latest_code(bot)[0])


async def publish_status(rt, meeting_id: str, status: str, detail: str | None, bot_id: str | None,
                         ended_reason: str | None) -> bool:
    """Publish bot.status only if it differs from the meeting's current status, then
    meeting.ended if the bot is gone. Shared by the webhook receiver and the poller,
    so a status arriving by both routes is published once. Returns True if published."""
    record = rt.store.get(meeting_id)
    if record is None:
        return False
    published = False
    if record.bot_status != status:
        await rt.bus.publish(meeting_id, "bot.status",
                             BotStatus(status=status, detail=detail, recall_bot_id=bot_id or None))
        published = True
    record = rt.store.get(meeting_id)
    if status in ("left", "failed") and ended_reason and record and record.ended_at is None:
        await rt.bus.publish(meeting_id, "meeting.ended", MeetingEnded(reason=ended_reason))
    return published


class BotLifecycle:
    def __init__(self, rt, client, api_key_set: bool, poll_seconds: float = POLL_SECONDS,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep, vendor=None,
                 note: str = "") -> None:
        self.rt = rt
        self.vendor = vendor or RecallVendor()
        self.note = note          # appended to the "cannot send" message (e.g. why a vendor was not chosen)
        self.client = client
        self.api_key_set = api_key_set
        self.poll_seconds = poll_seconds
        self.sleep = sleep
        self.on_launch = None   # optional async callback(record) - used to warm the filler bank
        self.watchers: dict[str, asyncio.Task] = {}

    def _missing(self) -> list[str]:
        missing = []
        if not self.api_key_set:
            missing.append(self.vendor.key_env)
        if not self.rt.config.public_base_url:
            missing.append("PUBLIC_BASE_URL")
        if not self.rt.config.recall_webhook_token:
            missing.append("RECALL_WEBHOOK_TOKEN")
        return missing + self.vendor.check(self.rt.config)

    async def launch(self, request: CreateMeetingRequest) -> MeetingRecord:
        missing = self._missing()
        if missing:
            raise HTTPException(503, f"Cannot send the bot: {', '.join(missing)} not set in .env."
                                     + (f" {self.note}" if self.note else ""))
        settings = self.rt.get_settings()
        payload = self.vendor.create_payload(
            request.meeting_url, settings,
            webhook_url(self.rt.config.public_base_url, self.rt.config.recall_webhook_token))
        try:
            bot = await self.client.create_bot(payload)
        except RecallError as exc:
            raise HTTPException(502, f"{self.vendor.name} did not create the bot: {exc}") from None
        bot_id = str(bot.get("id") or "")
        if not bot_id:
            raise HTTPException(502, f"{self.vendor.name} created no bot id.")
        record = self.rt.store.create_meeting(
            title=request.title or "Meeting", source="recall", meeting_url=request.meeting_url,
            recall_bot_id=bot_id)
        await self.rt.bus.publish(record.meeting_id, "bot.status",
                                  BotStatus(status="joining", detail="bot created", recall_bot_id=bot_id))
        if self.on_launch is not None:
            await self.on_launch(record)
        self.watchers[record.meeting_id] = asyncio.get_running_loop().create_task(
            self.watch(record.meeting_id, bot_id))
        return self.rt.store.get(record.meeting_id)

    async def watch(self, meeting_id: str, bot_id: str) -> None:
        """Poll the vendor until the bot has left or failed, or the meeting has ended."""
        while True:
            await self.sleep(self.poll_seconds)
            record = self.rt.store.get(meeting_id)
            if record is None or record.ended_at is not None:
                return
            try:
                bot = await self.client.get_bot(bot_id)
            except RecallError as exc:
                self.rt.warnings[self.vendor.status_warning_key] = (
                    f"{self.vendor.name} bot status check failing - status may be stale")
                log.warning("Bot status poll failed: %s", exc)
                continue
            self.rt.warnings.pop(self.vendor.status_warning_key, None)
            status, detail, ended_reason = self.vendor.read_status(bot)
            if status is None:
                continue
            try:
                await publish_status(self.rt, meeting_id, status, detail, bot_id, ended_reason)
            except Exception:
                log.exception("Publishing polled bot status failed for %s", meeting_id)
            if status in ("left", "failed"):
                return

    async def status(self, bot_id: str) -> str | None:
        try:
            return self.vendor.read_status(await self.client.get_bot(bot_id))[0]
        except RecallError as exc:
            log.warning("Could not read bot status: %s", exc)
            return None

    async def leave(self, record: MeetingRecord) -> None:
        """Slot rt.end_bot. Never raises: ending the meeting in the dashboard must always work."""
        if not record.recall_bot_id:
            return
        try:
            await self.client.leave_call(record.recall_bot_id)
        except RecallError as exc:
            log.warning("Bot %s did not confirm leaving: %s", record.recall_bot_id, exc)

    async def end_all(self) -> list[str]:
        """Make every "Meet AGI" bot leave: those our records think are live, plus any
        Recall still lists as in a call (e.g. after a crash lost our records). Live
        records are closed with meeting.ended. Returns the bot ids told to leave."""
        told: list[str] = []
        for record in self.rt.store.all():
            if record.source == "recall" and record.ended_at is None and record.recall_bot_id:
                await self.leave(record)
                told.append(record.recall_bot_id)
                await self.rt.bus.publish(record.meeting_id, "meeting.ended", MeetingEnded(reason="bot_left"))
        try:
            bots = await self.client.list_bots()
        except RecallError as exc:
            log.warning("Could not list bots at %s: %s", self.vendor.name, exc)
            bots = []
        name = self.rt.get_settings().bot_name or "Meet AGI"
        for bot in bots:
            bot_id = str(bot.get("id") or "")
            if bot_id and bot_id not in told and self.vendor.is_ours(bot, name) \
                    and self.vendor.read_status(bot)[0] not in ("left", "failed"):
                try:
                    await self.client.leave_call(bot_id)
                    told.append(bot_id)
                except RecallError as exc:
                    log.warning("Bot %s did not confirm leaving: %s", bot_id, exc)
        return told
