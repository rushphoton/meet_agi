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
from .bot import BotLifecycle
from .chat import ChatPoster
from .receiver import RecallReceiver
from .recall_client import DryRunTarget, RecallClient, is_replay_bot

log = logging.getLogger("meet_agi.meeting_lane")


class MeetingLane:
    """Everything the meeting lane built, kept together so tests can reach it."""

    def __init__(self, rt: Runtime, *, recall_transport: httpx.AsyncBaseTransport | None = None,
                 inworld_transport: httpx.AsyncBaseTransport | None = None,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> None:
        recall_key = os.environ.get("RECALL_API_KEY", "").strip()
        inworld_key = os.environ.get("INWORLD_API_KEY", "").strip()
        self.recall = RecallClient(recall_key, rt.config.recall_region, transport=recall_transport)
        self.dry_run = DryRunTarget()
        inworld = InworldVoice(inworld_key, transport=inworld_transport) if inworld_key else None
        self.voice = VoiceService(inworld, offline=rt.config.offline,
                                  fallback_voice_id=os.environ.get("INWORLD_VOICE_ID", "").strip())
        self.fillers = FillerBank(self.voice)
        self.receiver = RecallReceiver(rt)
        self.bot = BotLifecycle(rt, self.recall, api_key_set=bool(recall_key))
        self.audio = AudioOut(rt.bus, rt.store, rt.get_settings, self.voice, self.fillers,
                              self.target_for, sleep=sleep)
        self.chat = ChatPoster(rt.bus, rt.store, self.target_for)
        self.bot.on_launch = self._warm_fillers
        self.recall_key_set = bool(recall_key)
        self.rt = rt

    def target_for(self, record) -> tuple[object, str]:
        """Where audio and chat for this meeting go: the real bot, or the dry run for the fake meeting."""
        bot_id = (record.recall_bot_id if record else None) or ""
        if record is None or record.source == "replay" or is_replay_bot(bot_id):
            return self.dry_run, bot_id
        return self.recall, bot_id

    async def _warm_fillers(self, record) -> None:
        s = self.rt.get_settings()
        asyncio.get_running_loop().create_task(
            self.fillers.warm(s.fillers, s.voice.voice_id, s.voice.model, allow_vendor=True))


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


def install(rt: Runtime, lane: MeetingLane) -> MeetingLane:
    """Fill the slots and subscribe. Tests call this with a lane wired to the fake Recall."""
    rt.recall_webhook = lane.receiver
    rt.launch_bot = lane.bot.launch
    rt.end_bot = lane.bot.leave
    for event_type, handler in subscriptions(lane):
        rt.bus.subscribe(event_type, handler)

    if not lane.recall_key_set:
        rt.placeholders.add("Recall bot: no RECALL_API_KEY yet - sending a real bot answers 503; "
                            "the fake meeting uses the DRY RUN target")
    if not lane.voice.real_voice_available:
        rt.placeholders.add("voice: CANNED sample clip (OFFLINE=1 or no INWORLD_API_KEY)")
    if rt.config.recall_workspace_secret:
        rt.placeholders.add("Recall signature: checked but not enforced (receiver gets parsed JSON, "
                            "not raw bytes)")
    return lane


def register(rt: Runtime) -> None:
    """Called once by main.py. Signature frozen by the contract test."""
    install(rt, MeetingLane(rt))
