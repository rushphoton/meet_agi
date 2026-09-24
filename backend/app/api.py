"""
WHY THIS EXISTS
Every REST endpoint and the live event stream, in one file owned by the
integrate step. Endpoints that depend on a lane call that lane's slot on the
Runtime; if the lane has not filled it yet, the endpoint says so plainly
(HTTP 501 "not built yet") instead of pretending.

FAILURE IT PREVENTS
Lanes adding or renaming endpoints, which would change the API description
the dashboard's types are generated from (CLAUDE.md rule 3).
"""
from __future__ import annotations

import asyncio
import json
import math
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse

from .contract.events import Event, FollowUp, Mute, MeetingEnded, Stop, Wake
from .contract.records import (
    CreateMeetingRequest, DocumentInfo, FollowUpPatch, Health, MeetingListItem, MeetingRecord,
    MuteRequest, Ok, ReplayMeetingRequest, Settings, WakeRequest,
)
from .core.ids import new_id
from .core.runtime import Runtime

ALLOWED_DOCS = {".md", ".txt", ".pdf"}


def build_router(rt: Runtime, settings_store) -> APIRouter:
    r = APIRouter()

    def meeting_or_404(meeting_id: str) -> MeetingRecord:
        record = rt.store.get(meeting_id)
        if record is None:
            raise HTTPException(404, f"No meeting {meeting_id}")
        return record

    @r.get("/api/health", response_model=Health)
    async def health() -> Health:
        return Health(ok=True, offline=rt.config.offline, dev_mode=rt.config.dev_mode,
                      placeholders=sorted(rt.placeholders))

    # ---------------- meetings ----------------
    @r.get("/api/meetings", response_model=list[MeetingListItem])
    async def list_meetings():
        return rt.store.list_items()

    @r.post("/api/meetings", response_model=MeetingRecord)
    async def create_meeting(body: CreateMeetingRequest):
        if rt.store.live_meeting():
            raise HTTPException(409, "A meeting is already live; end it first (one at a time).")
        if rt.launch_bot is None:
            raise HTTPException(501, "Sending a real bot is not built yet (meeting lane).")
        return await rt.launch_bot(body)

    @r.get("/api/meetings/{meeting_id}", response_model=MeetingRecord)
    async def get_meeting(meeting_id: str):
        return meeting_or_404(meeting_id)

    @r.post("/api/meetings/{meeting_id}/end", response_model=MeetingRecord)
    async def end_meeting(meeting_id: str):
        record = meeting_or_404(meeting_id)
        if record.ended_at is None:
            if rt.end_bot is not None and record.source == "recall":
                await rt.end_bot(record)
            await rt.bus.publish(meeting_id, "meeting.ended", MeetingEnded(reason="dashboard"))
        return meeting_or_404(meeting_id)

    @r.post("/api/meetings/{meeting_id}/wake", response_model=Event)
    async def wake(meeting_id: str, body: WakeRequest):
        meeting_or_404(meeting_id)
        return await rt.bus.publish(meeting_id, "wake", Wake(trigger="button", question=body.question))

    @r.post("/api/meetings/{meeting_id}/stop", response_model=Event)
    async def stop(meeting_id: str):
        meeting_or_404(meeting_id)
        return await rt.bus.publish(meeting_id, "stop", Stop(trigger="button"))

    @r.post("/api/meetings/{meeting_id}/mute", response_model=Event)
    async def mute(meeting_id: str, body: MuteRequest):
        meeting_or_404(meeting_id)
        return await rt.bus.publish(meeting_id, "mute", Mute(muted=body.muted))

    @r.patch("/api/meetings/{meeting_id}/follow-ups/{follow_up_id}", response_model=FollowUp)
    async def patch_follow_up(meeting_id: str, follow_up_id: str, body: FollowUpPatch):
        record = meeting_or_404(meeting_id)
        current = next((f for f in record.follow_ups if f.follow_up_id == follow_up_id), None)
        if current is None:
            raise HTTPException(404, f"No follow-up {follow_up_id}")
        updated = current.model_copy(update={"status": body.status})
        await rt.bus.publish(meeting_id, "follow_up", updated)
        return updated

    @r.get(
        "/api/meetings/{meeting_id}/events",
        response_class=StreamingResponse,
        responses={200: {"description": "Server-Sent Events; each `data:` line is one Event "
                                        "(schema: GET /api/contract/events).",
                         "content": {"text/event-stream": {}}}},
    )
    async def events(meeting_id: str, request: Request, since: int = 0):
        meeting_or_404(meeting_id)
        queue = rt.bus.listen(meeting_id)  # listen first so nothing slips between history and live

        async def stream():
            last = since
            try:
                for event in rt.store.events_since(meeting_id, since):
                    last = event.seq
                    yield _sse(event)
                while not await request.is_disconnected():
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=15)
                    except asyncio.TimeoutError:
                        yield ": keep-alive\n\n"
                        continue
                    if event.seq <= last:
                        continue
                    last = event.seq
                    yield _sse(event)
            finally:
                rt.bus.unlisten(meeting_id, queue)

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # ---------------- settings & documents ----------------
    @r.get("/api/settings", response_model=Settings)
    async def get_settings():
        return settings_store.get()

    @r.put("/api/settings", response_model=Settings)
    async def put_settings(body: Settings):
        return settings_store.put(body)

    @r.get("/api/documents", response_model=list[DocumentInfo])
    async def list_documents():
        folder = rt.config.knowledge_dir
        return [_doc_info(p) for p in sorted(folder.glob("*"))
                if p.is_file() and p.suffix.lower() in ALLOWED_DOCS and p.name != "README.md"]

    @r.post("/api/documents", response_model=DocumentInfo)
    async def upload_document(file: UploadFile):
        name = Path(file.filename or "").name
        if not name or Path(name).suffix.lower() not in ALLOWED_DOCS:
            raise HTTPException(400, "Only .md, .txt or .pdf files are accepted.")
        target = rt.config.knowledge_dir / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(await file.read())
        return _doc_info(target)

    @r.delete("/api/documents/{name}", response_model=Ok)
    async def delete_document(name: str):
        target = rt.config.knowledge_dir / Path(name).name
        if not target.is_file():
            raise HTTPException(404, f"No document {name}")
        target.unlink()
        return Ok()

    # ---------------- contract exposure ----------------
    @r.get("/api/contract/events", response_model=Event)
    async def contract_events():
        """Exists so the Event union appears in the API description (for generated types)."""
        raise HTTPException(404, "Schema exposure only; events arrive on /api/meetings/{id}/events.")

    # ---------------- dev (fake meeting) ----------------
    @r.post("/api/dev/meetings", response_model=MeetingRecord)
    async def create_replay_meeting(body: ReplayMeetingRequest):
        if not rt.config.dev_mode:
            raise HTTPException(404, "Dev endpoints are off (set DEV_MODE=1).")
        if rt.store.live_meeting():
            live = rt.store.live_meeting()
            await rt.bus.publish(live.meeting_id, "meeting.ended", MeetingEnded(reason="dashboard"))
        return rt.store.create_meeting(title=body.title, source="replay",
                                       recall_bot_id=f"replay-{new_id('bot')}")

    # ---------------- Recall webhook receiver ----------------
    @r.post("/webhooks/recall/{token}", response_model=Ok)
    async def recall_webhook(token: str, request: Request):
        if rt.recall_webhook is None:
            raise HTTPException(501, "Webhook receiver not built yet (meeting lane).")
        try:
            body = await request.json()
        except (json.JSONDecodeError, ValueError):
            raise HTTPException(400, "Body must be JSON.")
        status = await rt.recall_webhook(token, dict(request.headers), body)
        if status != 200:
            raise HTTPException(status, "Rejected.")
        return Ok()

    return r


def _sse(event) -> str:
    return f"id: {event.seq}\nevent: {event.type}\ndata: {event.model_dump_json()}\n\n"


def _doc_info(path: Path) -> DocumentInfo:
    words = len(path.read_bytes().split()) if path.suffix.lower() != ".pdf" else 0
    return DocumentInfo(
        name=path.name, size_bytes=path.stat().st_size,
        chunks=math.ceil(words / 120) if words else 0,  # rough; the knowledge lane replaces this
        added_at=datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc),
    )
