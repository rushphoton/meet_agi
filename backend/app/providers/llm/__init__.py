"""
WHY THIS EXISTS
Decides, in one place, whether the engine talks to the real AI models or to
the canned stand-in - and says which, and why, so the dashboard's health
check can show "engine: canned" instead of pretending.

FAILURE IT PREVENTS
- A test (or the automatic test hook) spending money or failing because a
  vendor is down: under pytest, with OFFLINE=1, or when a key is missing,
  the canned provider is used - even if real keys sit in .env.
- A half-configured laptop crashing at the first sentence: a missing key
  means "canned, and it says so", not an exception mid-meeting.

Owner: lane-engine.
"""
from __future__ import annotations

import os

from .base import LLMError, LLMProvider
from .canned import CannedProvider
from .real import RealProvider
from .vendors import anthropic_key, gemini_key

__all__ = ["choose_provider", "canned_reason", "LLMError", "LLMProvider", "CannedProvider", "RealProvider"]


def canned_reason(offline: bool) -> str | None:
    """Why the canned provider must be used, or None when the real one may be."""
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return "running under tests"
    if offline:
        return "OFFLINE=1"
    missing = [name for name, value in (("GEMINI_API_KEY", gemini_key()), ("ANTHROPIC_API_KEY", anthropic_key()))
               if not value]
    if missing:
        return f"missing {' and '.join(missing)}"
    return None


def choose_provider(offline: bool) -> LLMProvider:
    reason = canned_reason(offline)
    return CannedProvider(reason) if reason else RealProvider()
