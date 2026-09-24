"""
WHY THIS EXISTS
The meeting lane's own hook-in point. main.py calls register(rt) once; this
module fills the Runtime slots the REST routes call: the Recall webhook
receiver, and (later) sending and removing the real bot.

FAILURE IT PREVENTS
The meeting lane editing main.py or api.py (CLAUDE.md rule 10).

PLACEHOLDER (milestone 0): only the placeholder receiver is plugged in;
launch_bot and end_bot stay empty, so POST /api/meetings answers 501
"not built yet". The meeting lane replaces this body, keeping the signature.
"""
from __future__ import annotations

from ..core.runtime import Runtime
from .receiver import PlaceholderReceiver


def register(rt: Runtime) -> None:
    rt.placeholders.add("meeting receiver (no sentence assembly, no signature check)")
    rt.placeholders.add("bot launch/leave (not built)")
    rt.placeholders.add("voice + chat delivery (not built)")
    rt.recall_webhook = PlaceholderReceiver(rt)
