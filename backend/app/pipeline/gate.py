"""
WHY THIS EXISTS
The last check before an alert reaches the meeting chat. Even when the
careful model is sure something is wrong, the bot stays quiet if:
- it is not confident enough (gate.min_confidence, default 0.75);
- it alerted less than gate.cooldown_seconds ago (default 90 s, measured in
  meeting time, so a replay at any speed behaves the same);
- it has already alerted gate.max_alerts_per_meeting times (default 8).
All three numbers are settings the dashboard can change.

FAILURE IT PREVENTS
Alert spam: the room learning to ignore (or mute) the bot (DESIGN.md risk
R8). A blocked alert is not thrown away: it is still recorded, marked
gated with the reason, and shown on the dashboard only - never in chat.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..contract.records import GateSettings


@dataclass(frozen=True)
class GateResult:
    passed: bool
    reason: str | None


def check_gate(confidence: float, now_t: float, last_alert_t: float | None, alerts_so_far: int,
               gate: GateSettings) -> GateResult:
    if confidence < gate.min_confidence:
        return GateResult(False, f"confidence {confidence:.2f} is below the {gate.min_confidence:.2f} minimum")
    if last_alert_t is not None and now_t - last_alert_t < gate.cooldown_seconds:
        return GateResult(False, f"cooldown: the last alert was {now_t - last_alert_t:.0f} s ago "
                                 f"(wait {gate.cooldown_seconds} s between alerts)")
    if alerts_so_far >= gate.max_alerts_per_meeting:
        return GateResult(False, f"cap reached: {alerts_so_far} alerts already posted this meeting "
                                 f"(limit {gate.max_alerts_per_meeting})")
    return GateResult(True, None)
