"""
WHY THIS EXISTS
Shared test setup: every test gets a fresh backend with its own temporary data
and knowledge folders and a known webhook token, so tests never touch real
meetings, real documents or real keys, and never call a vendor.
"""
import time

import pytest
from fastapi.testclient import TestClient

TOKEN = "t" * 64


@pytest.fixture
def make_client(tmp_path, monkeypatch):
    def _make(dev_mode: bool = True, token: str = TOKEN) -> TestClient:
        monkeypatch.setenv("MEETAGI_DATA_DIR", str(tmp_path / "data"))
        monkeypatch.setenv("MEETAGI_KNOWLEDGE_DIR", str(tmp_path / "knowledge"))
        monkeypatch.setenv("RECALL_WEBHOOK_TOKEN", token)
        monkeypatch.setenv("DEV_MODE", "1" if dev_mode else "0")
        from backend.app.main import create_app
        return TestClient(create_app())
    return _make


@pytest.fixture
def client(make_client):
    with make_client() as c:
        yield c


def wait_for(fn, timeout: float = 5.0):
    """Poll until fn() is truthy (the receiver answers first and works afterwards)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = fn()
        if value:
            return value
        time.sleep(0.02)
    raise AssertionError("condition not met within timeout")
