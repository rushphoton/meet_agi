"""The contract itself: shapes, limits, frozen signatures, generated files in sync."""
import inspect
import json
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from backend.app.contract import events as ev
from backend.app.contract.context import MeetingContext

ROOT = Path(__file__).resolve().parents[3]


def test_chat_post_over_500_characters_is_rejected():
    with pytest.raises(ValidationError):
        ev.ChatPost(chat_id="chat_1", text="x" * 501, reason="alert", status="pending")
    ev.ChatPost(chat_id="chat_1", text="x" * 500, reason="alert", status="pending")


def test_alert_topic_longer_than_60_characters_is_rejected():
    with pytest.raises(ValidationError):
        ev.Alert(alert_id="a", kind="uncertainty", topic="t" * 61, claim="c", said_by=[], segment_ids=[],
                 finding="f", evidence=[], reasoning="r", confidence=0.5, gated=False,
                 delivered_to_chat=False, models_used=[])


def test_unknown_event_type_cannot_be_published(tmp_path):
    import asyncio
    from backend.app.core.bus import EventBus
    from backend.app.core.store import Store
    store = Store(tmp_path)
    m = store.create_meeting("t", "replay")
    with pytest.raises(ValueError):
        asyncio.run(EventBus(store).publish(m.meeting_id, "alert.v2", {}))
    with pytest.raises(ValueError):
        EventBus(store).subscribe("nonsense", lambda e: None)


def test_every_event_type_round_trips_and_appears_in_openapi_and_typescript():
    openapi = (ROOT / "contract" / "openapi.json").read_text(encoding="utf-8")
    ts = (ROOT / "frontend" / "src" / "contract" / "schema.d.ts").read_text(encoding="utf-8")
    adapter = TypeAdapter(ev.Event)
    required = {"transcript.segment", "alert", "spoken.answer", "chat.post", "stop", "wake", "mute",
                "follow_up", "meeting.summary"}
    assert required <= set(ev.EVENT_PAYLOADS)
    for type_name, (payload_model, envelope) in ev.EVENT_PAYLOADS.items():
        assert f'"{type_name}"' in openapi and f'"{type_name}"' in ts, type_name
        assert envelope.__name__ in openapi and payload_model.__name__ in ts
    sample = {"id": "evt_1", "meeting_id": "mtg_1", "seq": 1, "at": "2026-09-24T00:00:00Z",
              "type": "mute", "payload": {"muted": True}}
    assert adapter.validate_python(sample).model_dump(mode="json")["payload"] == {"muted": True, "by": "dashboard"}
    assert "MeetingRecord" in openapi and "MeetingRecord" in ts


def test_engine_entry_point_name_and_signature_are_frozen():
    from backend.app.pipeline import entry
    sig = inspect.signature(entry.process_segment)
    assert inspect.iscoroutinefunction(entry.process_segment)
    assert list(sig.parameters) == ["segment", "ctx"]
    assert sig.parameters["segment"].annotation in (ev.TranscriptSegment, "TranscriptSegment")
    assert sig.parameters["ctx"].annotation in (MeetingContext, "MeetingContext")
    assert sig.return_annotation in (None, "None")


def test_lane_register_functions_take_only_the_runtime():
    from backend.app.integrations import register as meeting
    from backend.app.pipeline import register as engine
    for module in (engine, meeting):
        assert list(inspect.signature(module.register).parameters) == ["rt"]


def test_committed_openapi_file_matches_the_code(tmp_path, monkeypatch):
    monkeypatch.setenv("MEETAGI_DATA_DIR", str(tmp_path))
    from backend.app.main import create_app
    generated = json.dumps(create_app().openapi(), indent=2, sort_keys=True) + "\n"
    committed = (ROOT / "contract" / "openapi.json").read_text(encoding="utf-8").replace("\r\n", "\n")
    assert committed == generated, "run: python scripts/export_openapi.py"
