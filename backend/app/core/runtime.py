"""
WHY THIS EXISTS
The Runtime is the one object handed to each lane's register() function. It
carries the bus, the store, settings access, and a few named "slots" a lane
fills in (for example the meeting lane plugs in the Recall webhook receiver).
The REST routes call those slots, so no lane ever edits the entry file.

FAILURE IT PREVENTS
Lanes editing main.py or each other's code to hook themselves in, which is
where parallel branches collide at merge time (CLAUDE.md rule 10).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Awaitable, Callable

from ..contract.records import CreateMeetingRequest, MeetingRecord, Settings
from ..settings import Config
from .bus import EventBus
from .store import Store

# (token_from_url, request_headers, json_body) -> HTTP status code to return
WebhookHandler = Callable[[str, dict, dict], Awaitable[int]]
LaunchBot = Callable[[CreateMeetingRequest], Awaitable[MeetingRecord]]
EndBot = Callable[[MeetingRecord], Awaitable[None]]


@dataclass
class Runtime:
    bus: EventBus
    store: Store
    config: Config
    get_settings: Callable[[], Settings]
    recall_webhook: WebhookHandler | None = None   # slot: meeting lane
    launch_bot: LaunchBot | None = None            # slot: meeting lane
    end_bot: EndBot | None = None                  # slot: meeting lane
    placeholders: set[str] = field(default_factory=set)  # names of parts still canned
    # Live problems, keyed by who owns them ("engine.judge", "meeting.voice", ...). A lane sets
    # its key when a vendor call fails and removes it when one succeeds; /api/health lists them.
    warnings: dict[str, str] = field(default_factory=dict)
    last_webhook_at: datetime | None = None  # set by the meeting lane's receiver on every webhook
