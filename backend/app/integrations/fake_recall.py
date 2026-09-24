"""
WHY THIS EXISTS
A pretend Recall.ai server that lives inside the test process. Tests plug
its transport into RecallClient, so every bot, audio and chat call is
answered locally and written down for the test to check. It can also be
told to fail a given endpoint, to prove the failure paths.

This is test equipment: it is never used by the running app, and it never
touches the network (DESIGN.md §10 rule 4: never call a live vendor from a
test).

FAILURE IT PREVENTS
Tests that quietly call the real Recall (costing money, needing a key we do
not have yet - risk R1), and bugs in failure handling that only show up when
the real vendor misbehaves during the demo.

DEPENDENCIES (CLAUDE.md rule 4): httpx.MockTransport, part of httpx.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

import httpx


@dataclass
class FakeRecall:
    calls: list[tuple[str, str, dict | None]] = field(default_factory=list)
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

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, path, body))
        self.auth_headers.append(request.headers.get("authorization", ""))
        for suffix, status in self.fail.items():
            if path.endswith(suffix):
                return httpx.Response(status, json={"detail": f"fake failure on {suffix}"})
        parts = [p for p in path.split("/") if p]          # api, v1, bot, {id}, action
        if request.method == "POST" and parts[-1] == "bot":
            self._n += 1
            bot_id = f"bot_fake_{self._n:04d}"
            self.bots[bot_id] = {"id": bot_id, "meeting_url": body.get("meeting_url"),
                                 "bot_name": body.get("bot_name"),
                                 "status_changes": [{"code": "ready", "sub_code": None}]}
            return httpx.Response(201, json=self.bots[bot_id])
        if request.method == "GET" and parts[-1] == "bot":
            return httpx.Response(200, json={"results": list(self.bots.values()), "next": None})
        bot_id = parts[3] if len(parts) > 3 else ""
        if bot_id not in self.bots:
            return httpx.Response(404, json={"detail": "Not found."})
        action = parts[4] if len(parts) > 4 else ""
        if action == "" and request.method == "GET":
            return httpx.Response(200, json=self.bots[bot_id])
        if action == "leave_call":
            self.bots[bot_id]["status_changes"].append({"code": "call_ended", "sub_code": "bot_left_call"})
            return httpx.Response(200, json=self.bots[bot_id])
        if action == "output_audio":
            return httpx.Response(204) if request.method == "DELETE" else httpx.Response(200, json={})
        if action == "send_chat_message":
            return httpx.Response(200, json={})
        return httpx.Response(404, json={"detail": "unknown fake endpoint"})
