"""
WHY THIS EXISTS
The two raw vendor calls, and nothing else: "send this prompt to Gemini and
give me back its JSON" and "send this prompt to Claude and make it fill in
this form (a tool call)". Keys come from the environment (.env, loaded by
settings.py) and are only ever put in a request header - never logged,
printed or put in an error message (CLAUDE.md rule 9).

FAILURE IT PREVENTS
- A slow vendor freezing the meeting: every call gives up after 8 seconds
  (DESIGN.md risk R5) and raises LLMError, which the pipeline treats as
  "skip this one", never as a crash.
- A retired model name silently breaking everything: if the chosen model is
  rejected as unknown (HTTP 404/400 mentioning the model), the call retries
  once with the fallback model from DESIGN.md §3.5.

DEPENDENCIES (CLAUDE.md rule 4)
httpx only (already in backend/requirements.txt). The official `anthropic`
and `google-genai` SDKs were deliberately not added: each is a large
dependency for what is here one POST request, and adding them means editing
a shared file. What would justify them: streaming answers (cut-list item 3)
or needing their automatic retries.
"""
from __future__ import annotations

import json
import os
import re

import httpx

from .base import LLMError

TIMEOUT_SECONDS = 8.0
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
FALLBACKS = {
    "gemini-3.5-flash-lite": "gemini-2.5-flash-lite",
    "claude-haiku-4-5-20251001": "claude-sonnet-5",
}


def gemini_key() -> str:
    return os.environ.get("GEMINI_API_KEY", "").strip()


def anthropic_key() -> str:
    return os.environ.get("ANTHROPIC_API_KEY", "").strip()


class VendorClient:
    """One shared HTTP connection pool. `transport` lets tests fake the network."""

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport
        self._client: httpx.AsyncClient | None = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=TIMEOUT_SECONDS, transport=self._transport)
        return self._client

    async def _post(self, url: str, headers: dict, body: dict) -> dict:
        try:
            r = await self._http().post(url, headers=headers, json=body)
        except httpx.TimeoutException as exc:
            raise LLMError(f"timed out after {TIMEOUT_SECONDS:.0f} s") from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"network error: {type(exc).__name__}") from exc
        except RuntimeError as exc:  # e.g. the client's event loop was closed between tests
            self._client = None
            raise LLMError(f"client error: {exc}") from exc
        if r.status_code != 200:
            # The body can echo request details; keep only a short, key-free snippet.
            snippet = re.sub(r"[A-Za-z0-9_\-]{30,}", "[redacted]", r.text[:300])
            raise LLMError(f"HTTP {r.status_code}: {snippet}")
        try:
            return r.json()
        except ValueError as exc:
            raise LLMError("reply was not JSON") from exc

    async def _with_fallback(self, model: str, call):
        try:
            return await call(model)
        except LLMError as exc:
            fallback = FALLBACKS.get(model)
            if fallback and re.search(r"HTTP (400|404)", str(exc)) and "model" in str(exc).lower():
                return await call(fallback)
            raise

    # ---------------- Gemini ----------------
    async def gemini_json(self, model: str, system: str, prompt: str) -> tuple[dict, str]:
        key = gemini_key()
        if not key:
            raise LLMError("GEMINI_API_KEY is not set")

        async def call(m: str):
            body = {
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0, "responseMimeType": "application/json",
                                     "maxOutputTokens": 256},
            }
            data = await self._post(GEMINI_URL.format(model=m), {"x-goog-api-key": key}, body)
            return parse_gemini(data), m

        return await self._with_fallback(model, call)

    # ---------------- Claude ----------------
    async def claude_tool(self, model: str, system: str, prompt: str, tool_name: str,
                          schema: dict, max_tokens: int = 1024) -> tuple[dict, str]:
        key = anthropic_key()
        if not key:
            raise LLMError("ANTHROPIC_API_KEY is not set")

        async def call(m: str):
            body = {
                "model": m, "max_tokens": max_tokens, "temperature": 0, "system": system,
                "messages": [{"role": "user", "content": prompt}],
                "tools": [{"name": tool_name, "description": "Record your result.", "input_schema": schema}],
                "tool_choice": {"type": "tool", "name": tool_name},
            }
            headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
            data = await self._post(ANTHROPIC_URL, headers, body)
            return parse_claude_tool(data, tool_name), m

        return await self._with_fallback(model, call)


def parse_gemini(data: dict) -> dict:
    try:
        text = "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError("Gemini reply had no text") from exc
    return _json_object(text)


def parse_claude_tool(data: dict, tool_name: str) -> dict:
    for block in data.get("content") or []:
        if block.get("type") == "tool_use" and block.get("name") == tool_name:
            if isinstance(block.get("input"), dict):
                return block["input"]
    for block in data.get("content") or []:  # some replies put JSON in text instead
        if block.get("type") == "text":
            return _json_object(block.get("text", ""))
    raise LLMError("Claude reply had no tool result")


def _json_object(text: str) -> dict:
    text = text.strip()
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise LLMError("reply contained no JSON object")
    try:
        value = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise LLMError("reply JSON did not parse") from exc
    if not isinstance(value, dict):
        raise LLMError("reply JSON was not an object")
    return value
