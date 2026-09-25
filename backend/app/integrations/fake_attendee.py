"""
WHY THIS EXISTS
A pretend Attendee server that lives inside the test process, the Attendee
twin of fake_recall.py. Tests plug its transport into AttendeeClient, so every
create-bot, status, leave, audio and chat call is answered locally and written
down for the test to check. It can be told to fail a given endpoint, to prove
the failure paths. Its answers follow the shapes in Attendee's open-source code
(see the RESEARCH block in attendee_client.py).

This is test equipment: the running app never uses it and it never touches
the network (DESIGN.md §10 rule 4: never call a live vendor from a test).

FAILURE IT PREVENTS
Tests quietly creating a real Attendee bot (it bills per hour and needs a
real meeting), and failure handling that only shows up when the real vendor
misbehaves in front of the room.

DEPENDENCIES (CLAUDE.md rule 4): httpx.MockTransport, part of httpx.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

import httpx

PLAYABLE = {"joined_recording", "joined_not_recording", "joined_recording_paused",
            "joined_recording_permission_denied"}


@dataclass
class FakeAttendee:
    calls: list[tuple[str, str, dict | None]] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)
    bots: dict[str, dict] = field(default_factory=dict)
    fail: dict[str, int] = field(default_factory=dict)      # path suffix -> HTTP status to answer
    auth_headers: list[str] = field(default_factory=list)
    _n: int = 0

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def paths(self, method: str | None = None) -> list[str]:
        return [p for m, p, _ in self.calls if method is None or m == method]

    def bodies(self, suffix: str) -> list[dict | None]:
        return [b for _, p, b in self.calls if p.endswith(suffix)]

    def set_state(self, bot_id: str, state: str, event_type: str | None = None,
                  sub_type: str | None = None) -> None:
        bot = self.bots[bot_id]
        bot["state"] = state
        if event_type:
            bot["events"].append({"type": event_type, "sub_type": sub_type, "created_at": "2026-09-25T10:00:00Z"})

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, path, body))
        self.queries.append(request.url.query.decode("ascii"))
        self.auth_headers.append(request.headers.get("authorization", ""))
        for suffix, status in self.fail.items():
            if path.endswith(suffix):
                return httpx.Response(status, json={"error": f"fake failure on {suffix}"})
        parts = [p for p in path.split("/") if p]          # api, v1, bots, {id}, action
        if parts[-1] == "bots" and request.method == "POST":
            self._n += 1
            bot_id = f"bot_fakeAttendee{self._n:04d}"
            self.bots[bot_id] = {"id": bot_id, "meeting_url": body.get("meeting_url"),
                                 "metadata": body.get("metadata") or {}, "state": "joining",
                                 "events": [{"type": "join_requested", "sub_type": None,
                                             "created_at": "2026-09-25T10:00:00Z"}],
                                 "transcription_state": "not_started", "recording_state": "not_started",
                                 "join_at": None, "deduplication_key": None}
            return httpx.Response(201, json=self.bots[bot_id])
        if parts[-1] == "bots" and request.method == "GET":
            wanted = set(request.url.params.get_list("states"))
            results = [b for b in self.bots.values() if not wanted or b["state"] in wanted]
            return httpx.Response(200, json={"next": None, "previous": None, "results": results})
        bot_id = parts[3] if len(parts) > 3 else ""
        if bot_id not in self.bots:
            return httpx.Response(404, json={"error": "Bot not found"})
        bot = self.bots[bot_id]
        action = parts[4] if len(parts) > 4 else ""
        if action == "" and request.method == "GET":
            return httpx.Response(200, json=bot)
        if action == "leave" and request.method == "POST":
            self.set_state(bot_id, "leaving", "leave_requested")
            return httpx.Response(200, json=bot)
        if action in ("output_audio", "send_chat_message") and request.method == "POST":
            if bot["state"] not in PLAYABLE:
                return httpx.Response(400, json={"error": f"Bot is in state {bot['state']} and cannot play media"})
            if action == "send_chat_message" and any(ord(c) > 0xFFFF for c in (body or {}).get("message", "")):
                return httpx.Response(400, json={"message": ["Message cannot contain emojis or rare script characters."]})
            return httpx.Response(200)
        return httpx.Response(404, json={"error": "Not found"})
