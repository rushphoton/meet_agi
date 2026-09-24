"""
WHY THIS EXISTS
The one place that talks to Recall.ai's bot API (DESIGN.md §2): create the
"Meet AGI" bot, read its status, make it leave, play an MP3 into the call,
stop audio, and post to the meeting chat. Every call has an 8 s timeout and
turns any vendor problem into a RecallError with a short, key-free message.

It also holds DryRunTarget: the stand-in used for the fake (replay) meeting,
which has no real bot. It sends nothing anywhere and logs "DRY RUN" for every
clip and chat line, so replay output is never mistaken for a real delivery.

FAILURE IT PREVENTS
- Vendor errors or hangs crashing the meeting (risk R5).
- The API key leaking into logs or error messages (CLAUDE.md rule 9): the
  key is only ever placed in the Authorization header.
- The fake meeting accidentally calling a real Recall bot.

DEPENDENCIES (CLAUDE.md rule 4): httpx, already installed. Recall's own SDK
is not used: there is no official Python one, and we need six endpoints.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

log = logging.getLogger("meet_agi.recall")
TIMEOUT_SECONDS = 8.0


class RecallError(Exception):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class RecallClient:
    """Thin async client for https://{region}.recall.ai/api/v1."""

    is_dry_run = False

    def __init__(self, api_key: str, region: str = "us-west-2",
                 transport: httpx.AsyncBaseTransport | None = None,
                 timeout: float = TIMEOUT_SECONDS) -> None:
        self._key = api_key
        self.base_url = f"https://{region}.recall.ai/api/v1"
        self._transport = transport
        self._timeout = timeout

    async def _request(self, method: str, path: str, json: dict | None = None) -> Any:
        if not self._key:
            raise RecallError("RECALL_API_KEY is not set in .env")
        headers = {"Authorization": f"Token {self._key}", "Accept": "application/json"}
        try:
            async with httpx.AsyncClient(base_url=self.base_url, transport=self._transport,
                                         timeout=self._timeout) as client:
                r = await client.request(method, path, json=json, headers=headers)
        except httpx.HTTPError as exc:
            raise RecallError(f"Recall unreachable on {method} {path}: {type(exc).__name__}") from None
        if r.status_code >= 300:
            detail = r.text[:200].replace("\n", " ")
            raise RecallError(f"Recall answered HTTP {r.status_code} on {method} {path}: {detail}",
                              status=r.status_code)
        if not r.content:
            return {}
        try:
            return r.json()
        except ValueError:
            return {}

    # ----- bot lifecycle -----
    async def create_bot(self, payload: dict) -> dict:
        return await self._request("POST", "/bot/", json=payload)

    async def get_bot(self, bot_id: str) -> dict:
        return await self._request("GET", f"/bot/{bot_id}/")

    async def list_bots(self) -> list[dict]:
        data = await self._request("GET", "/bot/")
        return data.get("results", []) if isinstance(data, dict) else list(data or [])

    async def leave_call(self, bot_id: str) -> None:
        await self._request("POST", f"/bot/{bot_id}/leave_call/")

    # ----- audio and chat -----
    async def output_audio(self, bot_id: str, b64_mp3: str) -> None:
        await self._request("POST", f"/bot/{bot_id}/output_audio/", json={"kind": "mp3", "b64_data": b64_mp3})

    async def stop_audio(self, bot_id: str) -> None:
        await self._request("DELETE", f"/bot/{bot_id}/output_audio/")

    async def send_chat(self, bot_id: str, message: str) -> None:
        await self._request("POST", f"/bot/{bot_id}/send_chat_message/",
                            json={"to": "everyone", "message": message})


class DryRunTarget:
    """Delivery target for the fake meeting. Sends nothing; says so in the log."""

    is_dry_run = True

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def output_audio(self, bot_id: str, b64_mp3: str) -> None:
        self.calls.append(("output_audio", bot_id))
        log.info("DRY RUN (fake meeting %s): a clip would play now; nothing was sent", bot_id)

    async def stop_audio(self, bot_id: str) -> None:
        self.calls.append(("stop_audio", bot_id))
        log.info("DRY RUN (fake meeting %s): audio would stop now; nothing was sent", bot_id)

    async def send_chat(self, bot_id: str, message: str) -> None:
        self.calls.append(("send_chat", bot_id))
        log.info("DRY RUN (fake meeting %s): chat would say: %s", bot_id, message)


def is_replay_bot(bot_id: str | None) -> bool:
    return bool(bot_id) and bot_id.startswith("replay-")
