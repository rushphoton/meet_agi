"""
WHY THIS EXISTS
The meeting lane's one hook-in point. main.py calls register(rt) once at
startup. It:

1. fills the Runtime slots the REST routes call:
   rt.recall_webhook (the transcript receiver), rt.launch_bot (send the bot),
   rt.end_bot (make it leave);
2. subscribes "the mouth" and the chat poster to the engine's events on the
   bus - listed in SUBSCRIPTIONS below so anyone can see them at a glance;
3. lists on /api/health whatever is still canned or not yet possible.

Which bot vendor to use is decided here from BOT_PROVIDER in .env:
- unset or "recall" (the default, DESIGN §2): Recall.ai, key RECALL_API_KEY;
- "attendee" (DESIGN risk R1 fallback while there is no Recall account):
  Attendee, keys ATTENDEE_API_KEY, ATTENDEE_BASE_URL (default
  https://app.attendee.dev) and, optionally, ATTENDEE_WEBHOOK_SECRET. Not
  used while OFFLINE=1, which means "no vendor calls": then the Recall path
  stays selected and, without its key, sending a bot answers 503.
Anything else falls back to Recall with a log line.

Which voice and which Recall connection to use is decided here from .env:
RECALL_API_KEY, INWORLD_API_KEY, INWORLD_VOICE_ID (read from the process
environment, which settings.load_config() has already filled from .env -
settings.py is integrate-owned, so this lane does not add fields to Config).
The fake (replay) meeting always uses the DRY RUN target and the canned
voice, so it never reaches a vendor.

FAILURE IT PREVENTS
The meeting lane editing main.py or api.py (CLAUDE.md rule 10), and a
subscription silently missing so the bot never speaks or never posts.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Awaitable, Callable

import httpx

from ..core.runtime import Runtime
from ..providers.voice import InworldVoice, VoiceService
from ..speech.audio_out import AudioOut
from ..speech.fillers import FillerBank
from .attendee_client import STOP_LIMIT_NOTE, AttendeeClient, AttendeeVendor
from .bot import POLL_SECONDS, BotLifecycle
from .chat import ChatPoster
from .receiver import RecallReceiver
from .recall_client import DryRunTarget, RecallClient, is_replay_bot

log = logging.getLogger("meet_agi.meeting_lane")


class MeetingLane:
    """Everything the meeting lane built, kept together so tests can reach it."""

    def __init__(self, rt: Runtime, *, recall_transport: httpx.AsyncBaseTransport | None = None,
                 inworld_transport: httpx.AsyncBaseTransport | None = None,
                 attendee_transport: httpx.AsyncBaseTransport | None = None,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                 poll_seconds: float = POLL_SECONDS) -> None:
        recall_key = os.environ.get("RECALL_API_KEY", "").strip()
        inworld_key = os.environ.get("INWORLD_API_KEY", "").strip()
        attendee_key = os.environ.get("ATTENDEE_API_KEY", "").strip()
        self.provider, self.provider_note = choose_provider(
            os.environ.get("BOT_PROVIDER", ""), offline=rt.config.offline)
        self.recall = RecallClient(recall_key, rt.config.recall_region, transport=recall_transport)
        self.attendee = AttendeeClient(attendee_key, os.environ.get("ATTENDEE_BASE_URL", "").strip(),
                                       transport=attendee_transport, warnings=rt.warnings)
        if self.provider == "attendee":
            self.client, vendor, key_set = self.attendee, AttendeeVendor(), bool(attendee_key)
        else:
            self.client, vendor, key_set = self.recall, None, bool(recall_key)
        self.dry_run = DryRunTarget()
        inworld = InworldVoice(inworld_key, transport=inworld_transport) if inworld_key else None
        self.voice = VoiceService(inworld, offline=rt.config.offline,
                                  fallback_voice_id=os.environ.get("INWORLD_VOICE_ID", "").strip(),
                                  warnings=rt.warnings)
        self.fillers = FillerBank(self.voice)
        self.receiver = RecallReceiver(rt)
        self.bot = BotLifecycle(rt, self.client, api_key_set=key_set, poll_seconds=poll_seconds,
                                vendor=vendor, note=self.provider_note)
        self.audio = AudioOut(rt.bus, rt.store, rt.get_settings, self.voice, self.fillers,
                              self.target_for, sleep=sleep)
        self.chat = ChatPoster(rt.bus, rt.store, self.target_for)
        self.bot.on_launch = self._warm_fillers
        self.recall_key_set = bool(recall_key)
        self.key_set = key_set
        self.rt = rt

    def target_for(self, record) -> tuple[object, str]:
        """Where audio and chat for this meeting go: the real bot (Recall or Attendee, per
        BOT_PROVIDER), or the dry run for the fake meeting."""
        bot_id = (record.recall_bot_id if record else None) or ""
        if record is None or record.source == "replay" or is_replay_bot(bot_id):
            return self.dry_run, bot_id
        return self.client, bot_id

    async def _warm_fillers(self, record) -> None:
        s = self.rt.get_settings()
        asyncio.get_running_loop().create_task(
            self.fillers.warm(s.fillers, s.voice.voice_id, s.voice.model, allow_vendor=True))


def choose_provider(value: str, offline: bool) -> tuple[str, str]:
    """("recall" | "attendee", a note for the "cannot send" message). Recall is the default."""
    wanted = (value or "").strip().lower()
    if wanted in ("", "recall"):
        return "recall", ""
    if wanted != "attendee":
        log.warning("BOT_PROVIDER=%s is not recall or attendee; using Recall", wanted)
        return "recall", f"(BOT_PROVIDER={wanted} is unknown; Recall is used.)"
    if offline:
        return "recall", "(BOT_PROVIDER=attendee is not used while OFFLINE=1: no vendor calls.)"
    return "attendee", ""


def subscriptions(lane: MeetingLane) -> list[tuple[str, Callable]]:
    """The meeting lane's bus subscriptions. The integrate step checks this list."""
    return [
        ("wake", lane.audio.on_wake),                     # play a cached filler line at once
        ("spoken.answer", lane.audio.on_spoken_answer),   # speak answers with status "queued"
        ("stop", lane.audio.on_stop),                     # discard queued audio, cut the clip playing
        ("mute", lane.audio.on_mute),                     # discard queued audio, then stay silent
        ("meeting.ended", lane.audio.on_meeting_ended),   # throw away anything left
        ("chat.post", lane.chat.on_chat_post),            # post chat lines with status "pending"
    ]


_LANES: dict[int, MeetingLane] = {}


def lane_for(rt: Runtime) -> MeetingLane:
    """The meeting lane registered on this Runtime. The integrate step's end-all script uses
    `await lane_for(rt).bot.end_all()` (make every "Meet AGI" bot leave)."""
    return _LANES[id(rt)]


def install(rt: Runtime, lane: MeetingLane) -> MeetingLane:
    """Fill the slots and subscribe. Tests call this with a lane wired to the fake Recall."""
    _LANES[id(rt)] = lane
    rt.recall_webhook = lane.receiver
    rt.launch_bot = lane.bot.launch
    rt.end_bot = lane.bot.leave
    for event_type, handler in subscriptions(lane):
        rt.bus.subscribe(event_type, handler)

    if lane.provider == "attendee":
        rt.placeholders.add(STOP_LIMIT_NOTE)
        if not lane.key_set:
            rt.placeholders.add("Attendee bot: no ATTENDEE_API_KEY - sending a real bot answers 503; "
                                "the fake meeting uses the DRY RUN target")
        if not os.environ.get("ATTENDEE_WEBHOOK_SECRET", "").strip():
            rt.placeholders.add("Attendee signature: not checked (no ATTENDEE_WEBHOOK_SECRET); "
                                "the webhook path token is enforced")
    elif lane.provider_note:
        rt.placeholders.add(f"Bot provider: Recall {lane.provider_note}")
    if lane.provider != "attendee" and not lane.recall_key_set:
        rt.placeholders.add("Recall bot: no RECALL_API_KEY yet - sending a real bot answers 503; "
                            "the fake meeting uses the DRY RUN target")
    if rt.config.offline:
        rt.placeholders.add("voice: CANNED sample clip (OFFLINE=1)")
    elif lane.voice.inworld is None:
        rt.placeholders.add("voice: no INWORLD_API_KEY - a real call gets chat answers only; "
                            "the fake meeting uses the CANNED clip")
    if rt.config.recall_workspace_secret:
        rt.placeholders.add("Recall signature: checked but not enforced (receiver gets parsed JSON, "
                            "not raw bytes)")
    return lane


def register(rt: Runtime) -> None:
    """Called once by main.py. Signature frozen by the contract test."""
    install(rt, MeetingLane(rt))
