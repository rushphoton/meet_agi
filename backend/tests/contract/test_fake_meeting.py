"""The fake meeting through the real receiver path, with the placeholder engine."""
from backend.app.dev.fake_meeting import load_script, timeline
from backend.tests.conftest import TOKEN, wait_for


def _replay(client, stop_after_line=None):
    m = client.post("/api/dev/meetings", json={"title": "fake"}).json()
    script = load_script()
    items = timeline(script, m["recall_bot_id"])
    if stop_after_line is not None:
        items = items[: 2 + stop_after_line + 1]
    for _, body in items:
        assert client.post(f"/webhooks/recall/{TOKEN}", json=body).status_code == 200
    return m, script


def _record(client, meeting_id):
    return client.get(f"/api/meetings/{meeting_id}").json()


def test_talking_about_the_wake_word_does_not_trigger_speech_mode(client):
    script = load_script()
    mention = script["planted"]["wake_mention_line"]
    m, _ = _replay(client, stop_after_line=mention)
    record = wait_for(lambda: (r := _record(client, m["meeting_id"]))["segments"]
                      and len(r["segments"]) == mention + 1 and r)
    assert record["answers"] == []


def test_fake_meeting_produces_exactly_one_alert_one_answer_one_summary_all_canned(client):
    m, script = _replay(client)
    record = wait_for(lambda: (r := _record(client, m["meeting_id"]))["summary"] and r)
    assert len(record["segments"]) == len(script["lines"])
    assert len(record["alerts"]) == 1 and record["alerts"][0]["canned"] is True
    assert record["alerts"][0]["said_by"] == ["Marcus Chen"]
    assert len(record["answers"]) == 1 and record["answers"][0]["canned"] is True
    assert record["summary"]["canned"] is True
    assert all("CANNED" in c["text"] for c in record["chat_posts"])
    assert [c["text"].split(":")[0] for c in record["chat_posts"]] == \
        ["Because you mentioned Q3 revenue was rising", "Because you asked"]
    names = {p["display_name"] for p in record["participants"]}
    assert names == {s["name"] for s in script["speakers"]}
    listed = client.get("/api/meetings").json()[0]
    assert listed["alert_count"] == 1 and listed["meeting_id"] == m["meeting_id"]


def test_manual_wake_button_gives_one_canned_answer(client):
    m = client.post("/api/dev/meetings", json={"title": "t"}).json()
    r = client.post(f"/api/meetings/{m['meeting_id']}/wake", json={"question": "What was Q3 revenue?"})
    assert r.status_code == 200 and r.json()["payload"]["trigger"] == "button"
    record = _record(client, m["meeting_id"])
    assert len(record["answers"]) == 1 and record["answers"][0]["canned"] is True


def test_muted_meeting_posts_nothing_to_chat(client):
    m = client.post("/api/dev/meetings", json={"title": "t"}).json()
    client.post(f"/api/meetings/{m['meeting_id']}/mute", json={"muted": True})
    client.post(f"/api/meetings/{m['meeting_id']}/wake", json={})
    record = _record(client, m["meeting_id"])
    assert record["muted"] is True
    assert all(c["status"] == "suppressed_muted" for c in record["chat_posts"])


def test_sending_a_real_bot_says_not_built_yet(client):
    r = client.post("/api/meetings", json={"meeting_url": "https://meet.google.com/abc-defg-hij"})
    assert r.status_code == 501 and "not built yet" in r.json()["detail"]


def test_dev_endpoints_are_off_without_dev_mode(make_client):
    with make_client(dev_mode=False) as client:
        assert client.post("/api/dev/meetings", json={}).status_code == 404


def test_health_lists_what_is_still_placeholder(client):
    health = client.get("/api/health").json()
    assert health["ok"] is True and any("engine" in p for p in health["placeholders"])
