"""
The lane's proof: the scripted fake meeting through the REAL backend (webhook
receiver -> bus -> engine -> store) with the canned AI provider, and the
sample board deck as the only document. It must produce exactly one alert,
one wake, one spoken answer, one summary - and nothing else.
"""
import shutil

from backend.app.dev.fake_meeting import load_script, timeline
from backend.tests.conftest import TOKEN, wait_for
from backend.tests.engine.harness import SAMPLE_DOC


def _run_fake_meeting(make_client, tmp_path):
    (tmp_path / "knowledge").mkdir(parents=True, exist_ok=True)
    shutil.copy(SAMPLE_DOC, tmp_path / "knowledge" / SAMPLE_DOC.name)
    with make_client() as client:
        m = client.post("/api/dev/meetings", json={"title": "fake"}).json()
        for _, body in timeline(load_script(), m["recall_bot_id"]):
            assert client.post(f"/webhooks/recall/{TOKEN}", json=body).status_code == 200
        record = wait_for(lambda: (r := client.get(f"/api/meetings/{m['meeting_id']}").json())["summary"] and r)
        events = client.app.state.runtime.store.events_since(m["meeting_id"])
        health = client.get("/api/health").json()
    return record, events, health


def test_fake_meeting_produces_exactly_one_alert_one_answer_one_summary(make_client, tmp_path):
    script = load_script()
    record, events, health = _run_fake_meeting(make_client, tmp_path)
    counts = {}
    for e in events:
        counts[e.type] = counts.get(e.type, 0) + 1
    summary = record["summary"]

    # exactly one of each, and nothing else
    assert counts == {
        "bot.status": 3, "transcript.segment": len(script["lines"]), "alert": 1, "chat.post": 2,
        "wake": 1, "spoken.answer": 1, "meeting.ended": 1,
        "follow_up": summary["follow_up_count"], "meeting.summary": 1,
    }, counts

    # the alert: Marcus's claim, contradicted by the board deck, posted to chat
    [alert] = record["alerts"]
    planted = script["lines"][script["planted"]["contradiction_line"]]["text"]
    assert alert["claim"] == planted and alert["said_by"] == ["Marcus Chen"]
    assert not alert["gated"] and alert["delivered_to_chat"] and alert["canned"]
    assert alert["evidence"][0]["document"] == "SAMPLE_board_deck_q3.md" and "down 4%" in alert["evidence"][0]["passage"]

    # the wake: only the real "Hey AGI" line, never the line that talks about it
    wake = next(e for e in events if e.type == "wake")
    wake_line = script["lines"][script["planted"]["wake_question_line"]]["text"]
    wake_seg = next(s for s in record["segments"] if s["segment_id"] == wake.payload.segment_id)
    assert wake_seg["text"] == wake_line and wake.payload.question.startswith("what was Q3 revenue")

    # the answer: grounded in the board deck
    [answer] = record["answers"]
    assert answer["asked_by"] == "Tom Walsh" and "41.2M" in answer["text"] and answer["canned"]
    assert len(answer["text"].split()) <= 60

    # the two chat posts, in order, both under 500 characters and marked canned
    texts = [c["text"] for c in record["chat_posts"]]
    assert texts[0].startswith("Because you mentioned Q3 revenue was rising:")
    assert texts[1].startswith("Because you asked:")
    assert all(len(t) < 500 and "CANNED" in t for t in texts)

    # the summary: counts match what happened; the settled dispute is not a follow-up
    assert summary["alert_count"] == 1 and summary["canned"] and summary["key_topics"]
    assert summary["follow_up_count"] == len(record["follow_ups"]) == 2
    assert {f["owner"] for f in record["follow_ups"]} == {"Priya Nair", "Dana Lee"}
    assert all(f["source_alert_id"] is None for f in record["follow_ups"])

    # and the health check admits the engine is canned here
    assert any(p.startswith("engine: canned AI provider") for p in health["placeholders"])
