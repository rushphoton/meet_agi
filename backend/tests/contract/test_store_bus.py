"""Event bus and store: ordering, resume, isolation of broken subscribers, JSON files on disk."""
import asyncio
import json

from backend.app.contract.events import Mute, TranscriptSegment
from backend.app.contract.records import MeetingListItem
from backend.app.core.bus import EventBus
from backend.app.core.store import Store


def _segment(i):
    return TranscriptSegment(segment_id=f"seg_{i}", speaker_id="1", speaker_name="A", text=f"line {i}",
                             t_start=float(i), t_end=float(i) + 1, source="replay")


def test_a_failing_subscriber_does_not_stop_the_others(tmp_path):
    store = Store(tmp_path)
    bus = EventBus(store)
    m = store.create_meeting("t", "replay")
    got = []

    async def broken(event):
        raise RuntimeError("lane bug")

    async def healthy(event):
        got.append(event.seq)

    bus.subscribe("transcript.segment", broken)
    bus.subscribe("*", healthy)
    asyncio.run(bus.publish(m.meeting_id, "transcript.segment", _segment(1)))
    assert got == [1]


def test_seq_is_strictly_increasing_and_events_since_resumes_without_gaps(tmp_path):
    store = Store(tmp_path)
    bus = EventBus(store)
    m = store.create_meeting("t", "replay")

    async def run():
        for i in range(5):
            await bus.publish(m.meeting_id, "transcript.segment", _segment(i))
    asyncio.run(run())
    assert [e.seq for e in store.events_since(m.meeting_id)] == [1, 2, 3, 4, 5]
    assert [e.seq for e in store.events_since(m.meeting_id, since=3)] == [4, 5]


def test_live_listener_receives_published_events(tmp_path):
    store = Store(tmp_path)
    bus = EventBus(store)
    m = store.create_meeting("t", "replay")

    async def run():
        queue = bus.listen(m.meeting_id)
        await bus.publish(m.meeting_id, "mute", Mute(muted=True))
        event = queue.get_nowait()
        bus.unlisten(m.meeting_id, queue)
        return event
    event = asyncio.run(run())
    assert event.type == "mute" and event.payload.muted is True


def test_store_writes_one_json_file_per_meeting_and_reloads_after_restart(tmp_path):
    store = Store(tmp_path)
    bus = EventBus(store)
    a = store.create_meeting("a", "replay")
    b = store.create_meeting("b", "replay")
    asyncio.run(bus.publish(a.meeting_id, "transcript.segment", _segment(1)))
    files = sorted(p.name for p in (tmp_path / "meetings").glob("*.json"))
    assert files == sorted([f"{a.meeting_id}.json", f"{b.meeting_id}.json"])
    on_disk = json.loads((tmp_path / "meetings" / f"{a.meeting_id}.json").read_text(encoding="utf-8"))
    assert on_disk["record"]["segments"][0]["text"] == "line 1" and len(on_disk["events"]) == 1

    restarted = Store(tmp_path)  # a fresh process reading the same folder
    assert restarted.get(a.meeting_id).segments[0].text == "line 1"
    assert restarted.next_seq(a.meeting_id) == 2
    assert not list((tmp_path / "meetings").glob("*.tmp"))


def test_sessions_list_shows_follow_up_counts_before_alert_count():
    fields = list(MeetingListItem.model_fields)
    assert fields.index("follow_ups_outstanding") < fields.index("alert_count")
    assert fields.index("follow_ups_resolved") < fields.index("alert_count")
