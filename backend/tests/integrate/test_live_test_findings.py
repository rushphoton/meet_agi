"""
WHY THIS EXISTS
Findings from the first live Google Meet test (27 Sep 2026, via Attendee), each reproduced as a test
named after what was seen. The bot's-own-voice finding lives in the meeting lane's tests.
"""
from backend.app.contract.records import Settings
from backend.app.pipeline.phrases import detect_wake


def test_hey_agi_captioned_by_meet_as_hey_gi_did_not_wake_the_bot():
    v = Settings().wake.variants
    assert detect_wake("Hey GI!", v)
    assert detect_wake("Hey, GI, what was Q3 Revenue? According to the board deck.", v)
    assert detect_wake("If you say hey GI it answers", v) is None   # the positional guard still holds
