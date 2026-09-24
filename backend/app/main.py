"""
WHY THIS EXISTS
The single entry file: builds the store, bus and settings, lets each lane
register itself, and mounts the API. Only the integrate step edits it
(CLAUDE.md rule 10); lanes hook in through their own register.py.

FAILURE IT PREVENTS
Three parallel branches all editing the same wiring file and colliding.

Run: python scripts/serve.py   (or: python -m uvicorn backend.app.main:app --port 8000)
"""
from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .api import build_router
from .core.bus import EventBus
from .core.runtime import Runtime
from .core.store import Store
from .integrations.register import register as register_meeting_lane
from .pipeline.register import register as register_engine_lane
from .settings import SettingsStore, load_config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("meet_agi.main")

# TUNNEL GUARD (review B item 5, P0). The backend listens on 127.0.0.1 only, but the ngrok tunnel
# forwards the whole port to the internet so Recall can reach the webhook. Without this guard,
# anyone with the ngrok address could read transcripts, change settings, upload documents or end
# the meeting (DESIGN §8 decision 8 assumes localhost only). Rule: a request whose Host is not a
# local name (i.e. it came through the tunnel, which sets Host to the ngrok domain) may only POST
# to /webhooks/recall/...; everything else gets 404. "testserver" is the name the test client uses.
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]", "testserver"}


def _host_name(host: str) -> str:
    host = host.strip().lower()
    if host.startswith("["):  # IPv6 literal, e.g. [::1]:8000
        return host.split("]")[0] + "]"
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


def is_public_request(request: Request) -> bool:
    for header in ("host", "x-forwarded-host"):
        value = request.headers.get(header)
        if value and any(_host_name(v) not in LOCAL_HOSTS for v in value.split(",")):
            return True
    return False


def create_app() -> FastAPI:
    config = load_config()
    store = Store(config.data_dir)
    settings_store = SettingsStore(config.data_dir)
    rt = Runtime(bus=EventBus(store), store=store, config=config, get_settings=settings_store.get)

    register_engine_lane(rt)
    register_meeting_lane(rt)

    app = FastAPI(title="Meet AGI", version="0.1.0-milestone-0",
                  description="Google Meet copilot backend. Contract: DESIGN.md §4.")
    app.state.runtime = rt

    @app.middleware("http")
    async def tunnel_guard(request: Request, call_next):
        if is_public_request(request) and not (
                request.method == "POST" and request.url.path.startswith("/webhooks/recall/")):
            log.warning("Tunnel guard: refused %s %s from outside localhost", request.method, request.url.path)
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        return await call_next(request)

    app.include_router(build_router(rt, settings_store))
    return app


app = create_app()
