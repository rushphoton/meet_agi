"""
WHY THIS EXISTS
Test equipment for the meeting lane: a pretend Recall server, a pretend
Inworld server, a full backend wired to both, and a guard that makes any
real network call from these tests fail loudly.

FAILURE IT PREVENTS
A test quietly calling a real vendor (costing money, needing keys, flaky on
the Beijing network) - DESIGN.md §10 rule 4.
"""
from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app.integrations.fake_recall import FakeRecall

ROOT = Path(__file__).resolve().parents[3]
RECALL = ROOT / "fixtures" / "recall"
TOKEN = "m" * 64
FAKE_RECALL_KEY = "fake-recall-key-for-tests"
FAKE_INWORLD_KEY = "fake-inworld-key-for-tests"
INWORLD_AUDIO = (ROOT / "backend/app/providers/voice/assets/silence_half_second.mp3").read_bytes()


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch):
    async def refuse(self, request):
        raise AssertionError(f"meeting-lane test tried a REAL network call to {request.url.host}")
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", refuse)


def fixture_body(name: str, bot_id: str) -> dict:
    body = json.loads((RECALL / name).read_text(encoding="utf-8"))
    body["data"]["bot"]["id"] = bot_id
    return body


def transcript_body(bot_id: str, pid: int, name: str, text: str, t: float, step: float = 0.3) -> dict:
    body = fixture_body("transcript_data.json", bot_id)
    words = []
    for w in text.split():
        words.append({"text": w, "start_timestamp": {"relative": round(t, 2)},
                      "end_timestamp": {"relative": round(t + step, 2)}})
        t += step
    body["data"]["data"]["words"] = words
    body["data"]["data"]["participant"].update({"id": pid, "name": name})
    return body


class FakeInworld:
    def __init__(self, status: int = 200, body: dict | None = None, raise_timeout: bool = False) -> None:
        self.calls: list[dict] = []
        self.auth: list[str] = []
        self.status, self.raise_timeout = status, raise_timeout
        self.body = body if body is not None else {"audioContent": base64.b64encode(INWORLD_AUDIO).decode()}

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(json.loads(request.content))
        self.auth.append(request.headers.get("authorization", ""))
        if self.raise_timeout:
            raise httpx.ReadTimeout("fake timeout", request=request)
        return httpx.Response(self.status, json=self.body)


async def instant_sleep(seconds: float) -> None:
    await asyncio.sleep(0)


@pytest.fixture
def fake_recall():
    return FakeRecall()


@pytest.fixture
def fake_inworld():
    return FakeInworld()


@pytest.fixture
def lane_env(monkeypatch, tmp_path):
    monkeypatch.setenv("MEETAGI_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MEETAGI_KNOWLEDGE_DIR", str(tmp_path / "knowledge"))
    monkeypatch.setenv("RECALL_WEBHOOK_TOKEN", TOKEN)
    monkeypatch.setenv("DEV_MODE", "1")
    monkeypatch.setenv("OFFLINE", "0")
    monkeypatch.setenv("RECALL_API_KEY", FAKE_RECALL_KEY)
    monkeypatch.setenv("INWORLD_API_KEY", FAKE_INWORLD_KEY)
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://example.invalid")
    monkeypatch.setenv("RECALL_WORKSPACE_SECRET", "")
    return monkeypatch


@pytest.fixture
def lane_app(lane_env, fake_recall, fake_inworld):
    """A full backend whose meeting lane talks to the fake Recall and fake Inworld."""
    from backend.app import main
    from backend.app.integrations import register as reg

    built = {}

    def register_with_fakes(rt):
        built["lane"] = reg.install(rt, reg.MeetingLane(
            rt, recall_transport=fake_recall.transport, inworld_transport=fake_inworld.transport,
            sleep=instant_sleep))

    lane_env.setattr(main, "register_meeting_lane", register_with_fakes)

    def make():
        client = TestClient(main.create_app())
        return client, built["lane"]
    return make


def make_rt(tmp_path, offline: bool = False, token: str = TOKEN, public_base_url: str = "https://example.invalid",
            secret: str = "", settings=None):
    """A bare Runtime (bus + store + config) without the web app, for async unit tests."""
    from backend.app.contract.records import Settings
    from backend.app.core.bus import EventBus
    from backend.app.core.runtime import Runtime
    from backend.app.core.store import Store
    from backend.app.settings import Config

    store = Store(tmp_path / "data")
    config = Config(recall_webhook_token=token, recall_workspace_secret=secret, recall_region="us-west-2",
                    public_base_url=public_base_url, dev_mode=True, offline=offline,
                    data_dir=tmp_path / "data", knowledge_dir=tmp_path / "knowledge")
    current = settings or Settings()
    return Runtime(bus=EventBus(store), store=store, config=config, get_settings=lambda: current)


def events_of(rt, meeting_id: str, event_type: str) -> list:
    return [e for e in rt.store.events_since(meeting_id) if e.type == event_type]


async def settle(rounds: int = 50) -> None:
    """Let background workers run (they are asyncio tasks)."""
    for _ in range(rounds):
        await asyncio.sleep(0)
