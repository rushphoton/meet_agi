"""
WHY THIS EXISTS
Keeps every meeting in memory and writes one JSON file per meeting to
data/meetings/{meeting_id}.json after every event (write to a temp file, then
rename, so a crash never leaves a half-written file). On startup it reloads
those files, so the review screen survives a restart.

FAILURE IT PREVENTS
Losing a meeting when the laptop sleeps or the backend restarts mid-demo.

DEPENDENCIES (CLAUDE.md rule 4): standard library + pydantic. Postgres would
be justified once there is more than one user or meetings must be searched
across; not before (DESIGN.md §9).
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from pydantic import TypeAdapter

from ..contract.events import Event
from ..contract.records import MeetingListItem, MeetingRecord, Participant
from .ids import new_id

_events_adapter = TypeAdapter(list[Event])


class Store:
    def __init__(self, data_dir: Path) -> None:
        self.dir = Path(data_dir) / "meetings"
        self.dir.mkdir(parents=True, exist_ok=True)
        self._records: dict[str, MeetingRecord] = {}
        self._events: dict[str, list] = {}
        self._load()

    # ---------- reading ----------
    def get(self, meeting_id: str) -> MeetingRecord | None:
        return self._records.get(meeting_id)

    def all(self) -> list[MeetingRecord]:
        return sorted(self._records.values(), key=lambda r: r.started_at, reverse=True)

    def list_items(self) -> list[MeetingListItem]:
        return [
            MeetingListItem(
                meeting_id=r.meeting_id, title=r.title, started_at=r.started_at,
                participants=[p.display_name for p in r.participants],
                follow_ups_outstanding=sum(f.status == "outstanding" for f in r.follow_ups),
                follow_ups_resolved=sum(f.status == "resolved" for f in r.follow_ups),
                alert_count=sum(not a.gated for a in r.alerts),
            )
            for r in self.all()
        ]

    def events_since(self, meeting_id: str, since: int = 0) -> list:
        return [e for e in self._events.get(meeting_id, []) if e.seq > since]

    def meeting_for_bot(self, bot_id: str) -> MeetingRecord | None:
        for r in self._records.values():
            if r.recall_bot_id == bot_id:
                return r
        return None

    def live_meeting(self) -> MeetingRecord | None:
        for r in self._records.values():
            if r.ended_at is None:
                return r
        return None

    # ---------- writing ----------
    def create_meeting(self, title: str, source: str, meeting_url: str | None = None,
                       recall_bot_id: str | None = None) -> MeetingRecord:
        record = MeetingRecord(
            meeting_id=new_id("mtg"), title=title, meeting_url=meeting_url, source=source,
            recall_bot_id=recall_bot_id, started_at=datetime.now(timezone.utc),
        )
        self._records[record.meeting_id] = record
        self._events[record.meeting_id] = []
        self._save(record.meeting_id)
        return record

    def next_seq(self, meeting_id: str) -> int:
        record = self._records.get(meeting_id)
        if record is None:
            raise KeyError(f"No meeting {meeting_id}")
        return record.events_last_seq + 1

    def apply(self, event) -> None:
        """Fold one event into the meeting record, then persist."""
        r = self._records[event.meeting_id]
        p = event.payload
        t = event.type
        if t == "bot.status":
            r.bot_status = p.status
            if p.recall_bot_id:
                r.recall_bot_id = p.recall_bot_id
        elif t == "transcript.segment":
            if not any(s.segment_id == p.segment_id for s in r.segments):
                r.segments.append(p)
            if not any(x.speaker_id == p.speaker_id for x in r.participants):
                r.participants.append(Participant(speaker_id=p.speaker_id, display_name=p.speaker_name))
        elif t == "mute":
            r.muted = p.muted
        elif t == "alert":
            _upsert(r.alerts, p, "alert_id")
        elif t == "spoken.answer":
            _upsert(r.answers, p, "answer_id")
        elif t == "chat.post":
            _upsert(r.chat_posts, p, "chat_id")
        elif t == "follow_up":
            _upsert(r.follow_ups, p, "follow_up_id")
        elif t == "meeting.summary":
            r.summary = p
        elif t == "meeting.ended":
            r.ended_at = r.ended_at or event.at
        r.events_last_seq = event.seq
        self._events[event.meeting_id].append(event)
        self._save(event.meeting_id)

    # ---------- disk ----------
    def path_for(self, meeting_id: str) -> Path:
        return self.dir / f"{meeting_id}.json"

    def _save(self, meeting_id: str) -> None:
        data = {
            "record": self._records[meeting_id].model_dump(mode="json"),
            "events": [e.model_dump(mode="json") for e in self._events[meeting_id]],
        }
        path = self.path_for(meeting_id)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        os.replace(tmp, path)

    def _load(self) -> None:
        for path in self.dir.glob("*.json"):
            data = json.loads(path.read_text(encoding="utf-8"))
            record = MeetingRecord.model_validate(data["record"])
            self._records[record.meeting_id] = record
            self._events[record.meeting_id] = _events_adapter.validate_python(data["events"])


def _upsert(items: list, item, key: str) -> None:
    for i, existing in enumerate(items):
        if getattr(existing, key) == getattr(item, key):
            items[i] = item
            return
    items.append(item)
