"""
WHY THIS EXISTS
Ray has no Recall.ai account (Recall refused his Gmail sign-up), so the bot
could not join a real Google Meet. DESIGN.md risk R1 names the fallback:
Attendee (attendee.dev, open source, a Recall-like API) behind the same
integrations boundary. This module is that fallback. With BOT_PROVIDER=attendee
in .env, the "Meet AGI" bot is created, watched, heard, voiced and removed
through Attendee instead of Recall. Everything downstream (the sentence
assembler, the engine's single door process_segment(), the audio queue, the
chat poster, the dashboard) is unchanged and does not know which vendor ran.

It holds four things:
- AttendeeClient: the HTTP calls (create bot, read status, list, leave, play
  an MP3, post chat). Same method names as RecallClient, so the audio queue
  and chat poster use either one. Errors become AttendeeError (a RecallError,
  so the existing failure handling catches it) with a key-free message.
- AttendeeVendor: what bot.py needs to know about Attendee: the create-bot
  body, how Attendee's bot states map onto the contract's bot.status, and
  which listed bots are ours (for end-all).
- is_attendee_payload() / parse_*(): how the shared webhook receiver tells an
  Attendee webhook from a Recall one (by shape) and reads it.
- attendee_signature_valid(): Attendee's own webhook signature.

FAILURE IT PREVENTS
No real-meeting test being possible until a Recall account exists; an
Attendee payload being mistaken for a Recall one (or the reverse) and
breaking the working Recall path or the replay; the key leaking into a log.

DEPENDENCIES (CLAUDE.md rule 4): httpx (already installed) and the standard
library. Attendee has no official Python SDK; we need six endpoints.
Cost: Attendee's hosted service bills per bot hour (see their pricing page);
nothing here runs unless a real bot is sent. Future condition that would
justify removing this module: a Recall account exists and BOT_PROVIDER=recall.

----------------------------------------------------------------------------
RESEARCH (25 Sep 2026). Ground truth = the open-source code at
github.com/attendee-labs/attendee, commit f51c968 (24 Sep 2026), plus
docs.attendee.dev. "VERIFIED" means read in that code or docs; nothing below
has been exercised against a live bot yet (no meeting URL, and creating a bot
costs money). The one live call made: GET /api/v1/bots with our key ->
HTTP 200, {"next": null, "previous": null, "results": []} (no bots yet).

VERIFIED
- Auth header "Authorization: Token <key>", JSON bodies. Base URL
  https://app.attendee.dev, paths /api/v1/bots... with NO trailing slash.
  (docs/openapi.yml; bots/bots_api_urls.py)
- Create: POST /api/v1/bots {meeting_url, bot_name, metadata (object of
  string values), bot_chat_message {to:"everyone", message} = "the chat
  message the bot sends after it joins" (our consent notice, DESIGN §8.4),
  webhooks [{url (must be https), triggers[]}] (max 2 per bot; bot-level
  webhooks replace project-level ones for that bot), transcription_settings}.
  201 -> Bot {id "bot_...", state, events[{type, sub_type, created_at}],
  metadata, meeting_url, transcription_state, recording_state}. The Bot
  object has NO bot_name field, so end-all recognises our bots by metadata.
  (openapi.yml CreateBotRequest, Bot; docs/webhooks.md "Bot-Level Webhooks")
- Transcription on Google Meet: with no transcription_settings Attendee uses
  Google Meet's own closed captions (no third-party key needed); we ask for
  it explicitly: {"meeting_closed_captions": {"google_meet_language":
  "en-US"}}. (bots/serializers.py validate_transcription_settings;
  bots/utils.py transcription_provider_from_bot_creation_data)
- Only FINAL captions are sent (CaptionEntry.only_save_final_captions=True),
  but a caption edited after it was final is saved again under the same
  source id and re-sent as a new webhook. (bots/bot_controller/
  closed_caption_manager.py)
- Webhook envelope: {idempotency_key (uuid per delivery, same on retry),
  bot_id, bot_metadata, trigger, data}. Recall's envelope is {event, data}.
  That difference is how the receiver tells them apart.
  (bots/tasks/deliver_webhook_task.py)
- transcript.update data: {speaker_name, speaker_uuid, speaker_user_uuid,
  speaker_is_host, timestamp_ms (epoch ms when the caption began),
  duration_ms, transcription: {transcript} or null}. NO word timings are
  sent by the code, even though docs/webhooks.md lists "words".
  (bots/webhook_payloads.py utterance_webhook_payload)
- bot.state_change data: {new_state, old_state, created_at, event_type,
  event_sub_type, event_metadata}. States: ready, joining, joined_not_recording,
  joined_recording, leaving, post_processing, fatal_error, waiting_room, ended,
  data_deleted, scheduled, staged, joined_recording_paused,
  joining_breakout_room, leaving_breakout_room,
  joined_recording_permission_denied (+ connecting/connected/disconnecting for
  app sessions). Event types include meeting_ended, left_meeting,
  could_not_join_meeting, fatal_error. (bots/models.py BotStates,
  BotEventTypes; BotEventManager.create_event)
- Signature: header X-Webhook-Signature = base64(HMAC-SHA256(base64-decoded
  project secret, json.dumps(payload, sort_keys=True, ensure_ascii=False,
  separators=(",", ":")))). It is computed over the PARSED payload, so unlike
  Recall's it can be checked from the parsed JSON our receiver slot gets.
  Secret: Attendee dashboard, Settings -> Webhooks. (docs/webhooks.md
  "Verifying Webhooks"; bots/webhook_utils.py sign_payload)
- Retries: non-2xx or >10 s -> retried up to 3 times. (docs/webhooks.md)
- Play audio: POST /api/v1/bots/{id}/output_audio {type:"audio/mp3", data:
  base64}. 200 = queued. Attendee plays ONE audio request at a time and
  queues the rest server-side. 400 if the bot is not in a call.
  (bots/bots_api_views.py OutputAudioView; bot_controller
  take_action_based_on_audio_media_requests_in_db)
- NO endpoint stops or cuts audio. There is no DELETE on output_audio and no
  "stop" route. (bots/bots_api_urls.py - full route list read)
- Chat: POST /api/v1/bots/{id}/send_chat_message {to:"everyone", message}.
  Attendee's own limit is 10,000 characters and it REJECTS characters outside
  the Basic Multilingual Plane (emoji). Google Meet's own 500-character limit
  still applies (fit_chat() in chat.py). 400 if the bot is not in a call.
  (bots/serializers.py BotChatMessageRequestSerializer)
- Google Meet supports both audio output and chat through Attendee
  (bots/google_meet_bot_adapter/google_meet_bot_adapter.py send_chat_message;
  web_bot_adapter.py send_raw_audio).
- Leave: POST /api/v1/bots/{id}/leave -> 200 Bot (state "leaving"); 400 if
  not in a state that can leave. List: GET /api/v1/bots?states=..&states=..
  cursor-paginated {next, previous, results}.
- Defaults that matter: the bot leaves by itself after 900 s in the waiting
  room, or 60 s alone in the meeting (automatic_leave_settings).

ASSUMED (not verifiable without a live bot)
- Google Meet caption text arrives punctuated, so the assembler's ". ? !"
  rule ends sentences. If not, the 1.2 s gap / 40-word / speaker-change rules
  still end them.
- Words inside one caption are spread evenly over its duration_ms (Attendee
  sends no word timings); only the gap BETWEEN captions matters for pauses.
- The first final version of a caption is kept; a later edited re-send of the
  same caption (same speaker, same timestamp_ms) is dropped as a duplicate.
- Meeting-relative times are measured from our meeting record's start (when
  the bot was sent), since Attendee's timestamp_ms is wall-clock epoch time.
----------------------------------------------------------------------------
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
from typing import Any

import httpx

from .recall_client import RecallError

log = logging.getLogger("meet_agi.attendee")
TIMEOUT_SECONDS = 8.0
DEFAULT_BASE_URL = "https://app.attendee.dev"
KEY_ENV = "ATTENDEE_API_KEY"
METADATA = {"created_by": "meet-agi"}     # how end-all recognises our bots (Bot has no bot_name)
WEBHOOK_TRIGGERS = ["bot.state_change", "transcript.update"]
GOOGLE_MEET_CAPTION_LANGUAGE = "en-US"
LIST_PAGE_LIMIT = 20

AUDIO_WARNING_KEY = "meeting.attendee.audio"
AUDIO_WARNING = "Attendee refusing audio - answers go to chat only"
CHAT_WARNING_KEY = "meeting.attendee.chat"
CHAT_WARNING = "Attendee refusing chat - alerts and answers on the dashboard only"
STOP_LIMIT_NOTE = ("Bot provider Attendee (DESIGN R1 fallback): 'stop talking' drops the rest of the answer "
                   "but cannot cut a clip already playing (Attendee has no stop-audio call), so the current "
                   "sentence finishes")

# Attendee bot state -> the contract's BotStatus.status (DESIGN §4.3). None = no change to report.
STATE_MAP: dict[str, str | None] = {
    "ready": "joining", "scheduled": "joining", "staged": "joining", "joining": "joining",
    "connecting": "joining",
    "waiting_room": "waiting_room",
    "joined_not_recording": "in_call", "joined_recording": "in_call",
    "joined_recording_paused": "in_call", "joined_recording_permission_denied": "in_call",
    "joining_breakout_room": "in_call", "leaving_breakout_room": "in_call", "connected": "in_call",
    "leaving": None, "disconnecting": None,        # still on the way out; "left" follows
    "post_processing": "left", "ended": "left", "data_deleted": "left",
    "fatal_error": "failed",
}
LIVE_STATES = [s for s, v in STATE_MAP.items() if v in ("joining", "waiting_room", "in_call")
               and s not in ("connecting", "connected")] + ["leaving"]


class AttendeeError(RecallError):
    """An Attendee call failed. A RecallError, so the audio queue and chat poster handle it unchanged."""


def _bmp_only(text: str) -> str:
    """Attendee rejects characters outside the Basic Multilingual Plane (emoji) with HTTP 400."""
    return "".join(ch for ch in text if ord(ch) <= 0xFFFF)


class AttendeeClient:
    """Thin async client for {ATTENDEE_BASE_URL}/api/v1. Same method names as RecallClient."""

    is_dry_run = False
    vendor = "Attendee"

    def __init__(self, api_key: str, base_url: str = DEFAULT_BASE_URL,
                 transport: httpx.AsyncBaseTransport | None = None,
                 timeout: float = TIMEOUT_SECONDS, warnings: dict[str, str] | None = None) -> None:
        self._key = api_key
        self.base_url = f"{(base_url or DEFAULT_BASE_URL).rstrip('/')}/api/v1"
        self._transport = transport
        self._timeout = timeout
        self.warnings = warnings if warnings is not None else {}
        self.stop_requests = 0

    async def _request(self, method: str, path: str, json_body: dict | None = None,
                       params: list[tuple[str, str]] | None = None) -> Any:
        if not self._key:
            raise AttendeeError(f"{KEY_ENV} is not set in .env")
        headers = {"Authorization": f"Token {self._key}", "Content-Type": "application/json",
                   "Accept": "application/json"}
        try:
            async with httpx.AsyncClient(base_url=self.base_url, transport=self._transport,
                                         timeout=self._timeout) as client:
                r = await client.request(method, path, json=json_body, headers=headers, params=params)
        except httpx.HTTPError as exc:
            raise AttendeeError(f"Attendee unreachable on {method} {path}: {type(exc).__name__}") from None
        if r.status_code >= 300:
            detail = r.text[:200].replace("\n", " ")
            raise AttendeeError(f"Attendee answered HTTP {r.status_code} on {method} {path}: {detail}",
                                status=r.status_code)
        if not r.content:
            return {}
        try:
            return r.json()
        except ValueError:
            return {}

    # ----- bot lifecycle -----
    async def create_bot(self, payload: dict) -> dict:
        return await self._request("POST", "/bots", json_body=payload)

    async def get_bot(self, bot_id: str) -> dict:
        return await self._request("GET", f"/bots/{bot_id}")

    async def list_bots(self) -> list[dict]:
        """Bots Attendee still has in a live state (following the cursor, at most 20 pages)."""
        out: list[dict] = []
        states = [("states", s) for s in LIVE_STATES]
        params = states
        for _ in range(LIST_PAGE_LIMIT):
            data = await self._request("GET", "/bots", params=params)
            if not isinstance(data, dict):
                break
            out += [b for b in data.get("results") or [] if isinstance(b, dict)]
            try:
                cursor = httpx.URL(str(data.get("next") or "")).params.get("cursor")
            except (httpx.InvalidURL, TypeError):
                cursor = None
            if not cursor:
                break
            params = states + [("cursor", cursor)]
        return out

    async def leave_call(self, bot_id: str) -> None:
        await self._request("POST", f"/bots/{bot_id}/leave")

    # ----- audio and chat -----
    async def output_audio(self, bot_id: str, b64_mp3: str) -> None:
        try:
            await self._request("POST", f"/bots/{bot_id}/output_audio",
                                json_body={"type": "audio/mp3", "data": b64_mp3})
        except AttendeeError:
            self.warnings[AUDIO_WARNING_KEY] = AUDIO_WARNING
            raise
        self.warnings.pop(AUDIO_WARNING_KEY, None)

    async def stop_audio(self, bot_id: str) -> None:
        """Attendee has no call that stops or cuts audio (see RESEARCH). The audio queue has
        already dropped everything not yet sent, and it sends one SENTENCE clip at a time
        (speech/sentences.py), so at most the sentence already playing (a few seconds)
        finishes. Recorded, not faked."""
        self.stop_requests += 1
        log.info("Attendee cannot cut audio for bot %s: queued answers were dropped; "
                 "a clip already playing finishes", bot_id)

    async def send_chat(self, bot_id: str, message: str) -> None:
        try:
            await self._request("POST", f"/bots/{bot_id}/send_chat_message",
                                json_body={"to": "everyone", "message": _bmp_only(message)})
        except AttendeeError:
            self.warnings[CHAT_WARNING_KEY] = CHAT_WARNING
            raise
        self.warnings.pop(CHAT_WARNING_KEY, None)


class AttendeeVendor:
    """What bot.py needs to know about Attendee."""

    name = "Attendee"
    key_env = KEY_ENV
    status_warning_key = "meeting.attendee"

    def check(self, config) -> list[str]:
        """Setting problems that stop launch() before any call (named, never valued)."""
        url = config.public_base_url or ""
        if url and not url.lower().startswith("https://"):
            return ["PUBLIC_BASE_URL (Attendee only accepts https:// webhook addresses)"]
        return []

    def create_payload(self, meeting_url: str, settings, hook_url: str) -> dict:
        body = {
            "meeting_url": meeting_url,
            "bot_name": settings.bot_name or "Meet AGI",
            "metadata": dict(METADATA),
            "webhooks": [{"url": hook_url, "triggers": list(WEBHOOK_TRIGGERS)}],
            "transcription_settings": {
                "meeting_closed_captions": {"google_meet_language": GOOGLE_MEET_CAPTION_LANGUAGE}},
        }
        consent = _bmp_only((settings.consent_text or "").strip())
        if consent:
            body["bot_chat_message"] = {"to": "everyone", "message": consent}
        return body

    def read_status(self, bot: dict) -> tuple[str | None, str | None, str | None]:
        """(contract status, detail, meeting.ended reason) from a GET /bots/{id} answer."""
        state = str(bot.get("state") or "")
        events = [e for e in bot.get("events") or [] if isinstance(e, dict)]
        last = events[-1] if events else {}
        event_type = str(last.get("type") or "") or None
        detail = last.get("sub_type") or event_type
        return state_to_status(state, event_type, detail)

    def is_ours(self, bot: dict, bot_name: str) -> bool:
        meta = bot.get("metadata") if isinstance(bot.get("metadata"), dict) else {}
        return meta.get("created_by") == METADATA["created_by"]


def state_to_status(state: str, event_type: str | None, detail: str | None
                    ) -> tuple[str | None, str | None, str | None]:
    status = STATE_MAP.get(state)
    ended = None
    if status == "failed":
        ended = "bot_left"
    elif status == "left":
        ended = "call_ended" if event_type == "meeting_ended" else "bot_left"
    return status, (str(detail) if detail else None), ended


# ---------------- webhooks: shape detection, parsing, signature ----------------
def is_attendee_payload(body: Any) -> bool:
    """Attendee: {idempotency_key, bot_id, trigger, data}. Recall: {event, data:{bot:{id}}}."""
    return (isinstance(body, dict) and "trigger" in body and "event" not in body
            and ("bot_id" in body or "idempotency_key" in body))


def parse_state_change(data: dict) -> tuple[str | None, str | None, str | None]:
    event_type = data.get("event_type")
    detail = data.get("event_sub_type") or event_type
    return state_to_status(str(data.get("new_state") or ""), event_type, detail)


def parse_utterance(data: dict) -> tuple[str, str, str, int, int] | None:
    """(speaker_uuid, speaker_name, text, timestamp_ms, duration_ms), or None if unusable."""
    transcription = data.get("transcription") if isinstance(data.get("transcription"), dict) else {}
    text = str(transcription.get("transcript") or "").strip()
    speaker = data.get("speaker_uuid")
    try:
        start_ms = int(data.get("timestamp_ms"))
        duration_ms = max(0, int(data.get("duration_ms") or 0))
    except (TypeError, ValueError):
        return None
    if not text or speaker in (None, ""):
        return None
    return str(speaker), str(data.get("speaker_name") or "").strip(), text, start_ms, duration_ms


def sign(body: dict, secret_b64: str) -> str:
    """Attendee's signature (bots/webhook_utils.py sign_payload), used by the check and by tests."""
    canonical = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    digest = hmac.new(base64.b64decode(secret_b64), canonical.encode("utf-8"), hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def attendee_signature_valid(body: dict, headers: dict, secret_b64: str) -> bool:
    given = {k.lower(): v for k, v in headers.items()}.get("x-webhook-signature") or ""
    if not (given and secret_b64):
        return False
    try:
        expected = sign(body, secret_b64)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(given.encode(), expected.encode())
