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

from fastapi import FastAPI

from .api import build_router
from .core.bus import EventBus
from .core.runtime import Runtime
from .core.store import Store
from .integrations.register import register as register_meeting_lane
from .pipeline.register import register as register_engine_lane
from .settings import SettingsStore, load_config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


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
    app.include_router(build_router(rt, settings_store))
    return app


app = create_app()
