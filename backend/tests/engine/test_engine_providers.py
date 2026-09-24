"""
AI providers: provider choice, and parsing of vendor replies through a FAKE
network (httpx.MockTransport). No request ever leaves this machine.
"""
import asyncio
import json

import httpx
import pytest

from backend.app.contract.events import TranscriptSegment
from backend.app.knowledge import Passage
from backend.app.providers.llm import CannedProvider, LLMError, RealProvider, canned_reason, choose_provider
from backend.app.providers.llm.vendors import VendorClient

FAKE_KEY = "sk-test-FAKE-not-a-real-key-000000000000000000"


def seg(name, text, i=0):
    return TranscriptSegment(segment_id=f"seg_{i}", speaker_id=name, speaker_name=name, text=text,
                             t_start=i, t_end=i + 1, source="replay")


LINES = [seg("Dana Lee", "Total bookings were fifty two million.", 0),
         seg("Marcus Chen", "Q3 revenue was rising, up about three percent.", 1)]
PASSAGES = [Passage("SAMPLE_board_deck_q3.md", "Q3 revenue was $41.2M, down 4% from $42.9M in Q2.", "Revenue summary")]


def provider_with(handler):
    return RealProvider(VendorClient(transport=httpx.MockTransport(handler)))


@pytest.fixture
def keys(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", FAKE_KEY)
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)


def claude_reply(tool, data):
    return httpx.Response(200, json={"content": [{"type": "tool_use", "name": tool, "input": data}]})


# ---------------- choosing a provider ----------------
def test_tests_never_reach_a_live_vendor_even_when_keys_are_set(keys):
    assert canned_reason(offline=False) == "running under tests"
    assert isinstance(choose_provider(offline=False), CannedProvider)


def test_missing_key_means_canned_and_says_which(monkeypatch):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    monkeypatch.setenv("GEMINI_API_KEY", "")
    assert canned_reason(offline=False) == "missing GEMINI_API_KEY"


def test_offline_flag_means_canned(monkeypatch, keys):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    assert canned_reason(offline=True) == "OFFLINE=1"
    assert canned_reason(offline=False) is None


# ---------------- vendor failures ----------------
def test_vendor_error_does_not_leak_the_key_into_the_message(keys):
    def handler(request):
        return httpx.Response(401, text=f"invalid x-api-key {FAKE_KEY}")
    with pytest.raises(LLMError) as err:
        asyncio.run(provider_with(handler).judge("claude-haiku-4-5-20251001", LINES, PASSAGES, []))
    assert FAKE_KEY not in str(err.value) and "401" in str(err.value)


def test_vendor_error_message_is_readable_not_raw_json(keys):
    def handler(request):
        return httpx.Response(400, json={"type": "error", "error": {
            "type": "invalid_request_error", "message": "Your credit balance is too low to access the Anthropic API."}})
    with pytest.raises(LLMError) as err:
        asyncio.run(provider_with(handler).judge("claude-haiku-4-5-20251001", LINES, PASSAGES, []))
    assert str(err.value) == "HTTP 400: Your credit balance is too low to access the Anthropic API."


def test_slow_vendor_raises_llm_error_instead_of_hanging(keys):
    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)
    with pytest.raises(LLMError, match="timed out"):
        asyncio.run(provider_with(handler).cheap_check("gemini-3.5-flash-lite", LINES, PASSAGES, []))


def test_gemini_reply_without_json_raises_llm_error(keys):
    def handler(request):
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "Sure! Looks fine."}]}}]})
    with pytest.raises(LLMError):
        asyncio.run(provider_with(handler).cheap_check("gemini-3.5-flash-lite", LINES, PASSAGES, []))


def test_claude_reply_without_a_tool_result_raises_llm_error(keys):
    def handler(request):
        return httpx.Response(200, json={"content": [{"type": "text", "text": "I cannot help."}]})
    with pytest.raises(LLMError):
        asyncio.run(provider_with(handler).answer("claude-haiku-4-5-20251001", "q?", None, PASSAGES, 60))


def test_missing_key_raises_llm_error_before_any_request(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    def handler(request):
        raise AssertionError("no request should be made")
    with pytest.raises(LLMError, match="ANTHROPIC_API_KEY"):
        asyncio.run(provider_with(handler).judge("m", LINES, PASSAGES, []))


def test_retired_model_name_falls_back_to_the_backup_model(keys):
    seen = []
    def handler(request):
        model = request.url.path.split("/")[-1].split(":")[0]
        seen.append(model)
        if model == "gemini-3.5-flash-lite":
            return httpx.Response(404, json={"error": {"message": "models/gemini-3.5-flash-lite is not found"}})
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [
            {"text": '{"worth_a_look": true, "score": 0.8, "topic": "Q3 revenue"}'}]}}]})
    result = asyncio.run(provider_with(handler).cheap_check("gemini-3.5-flash-lite", LINES, PASSAGES, []))
    assert seen == ["gemini-3.5-flash-lite", "gemini-2.5-flash-lite"]
    assert result.worth_a_look and result.model == "gemini-2.5-flash-lite"


# ---------------- replies that are almost right ----------------
def test_out_of_range_model_values_are_clamped(keys):
    def handler(request):
        return claude_reply("record_verdict", {
            "is_issue": True, "kind": "big-problem", "topic": "Q3 revenue was rising", "claim": "rising",
            "said_by": ["Marcus Chen", "Someone Not In The Meeting"], "line_numbers": [1, 7, -2, "x"],
            "finding": "The board deck says it fell 4%.", "reasoning": "r", "confidence": 1.7,
            "passage_numbers": [0, 3]})
    v = asyncio.run(provider_with(handler).judge("claude-haiku-4-5-20251001", LINES, PASSAGES, []))
    assert v.confidence == 1.0 and v.kind == "uncertainty"
    assert v.said_by == ["Marcus Chen"] and v.segment_indexes == [1] and v.passage_indexes == [0]


def test_requests_carry_the_key_only_in_a_header_and_ask_for_the_right_model(keys):
    captured = {}
    def handler(request):
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content)
        return claude_reply("record_answer", {"spoken": "The board deck says Q3 revenue fell 4 percent.",
                                              "chat_line": "Q3 revenue fell 4% (board deck).",
                                              "passage_numbers": [0]})
    a = asyncio.run(provider_with(handler).answer("claude-haiku-4-5-20251001", "What was Q3 revenue?",
                                                  "Tom Walsh", PASSAGES, 60))
    assert FAKE_KEY not in captured["url"] and captured["headers"]["x-api-key"] == FAKE_KEY
    assert captured["body"]["model"] == "claude-haiku-4-5-20251001"
    assert "41.2M" in captured["body"]["messages"][0]["content"]   # the passages went in
    assert a.spoken.startswith("The board deck") and a.passage_indexes == [0] and not a.canned


# ---------------- canned provider ----------------
def test_canned_provider_says_it_is_canned_everywhere():
    p = CannedProvider("running under tests")
    v = asyncio.run(p.judge("x", LINES, PASSAGES, []))
    a = asyncio.run(p.answer("x", "What was Q3 revenue?", None, PASSAGES, 60))
    empty = asyncio.run(p.answer("x", "What was Q3 revenue?", None, [], 60))
    assert v.canned and "CANNED" in v.reasoning
    assert a.canned and "CANNED" in a.spoken and "CANNED" in a.chat_line
    assert "CANNED" in empty.spoken and "couldn't find" in empty.spoken


# ---------------- review B item 1: moving jobs to Gemini from Settings ----------------
def test_judge_answer_and_summary_run_on_gemini_when_settings_name_a_gemini_model(keys):
    """Anthropic had no credit (24 Sep); every careful job failed. With a gemini-... model in
    Settings those jobs must go to Gemini, and Anthropic must not be called at all."""
    replies = {
        "record_verdict": {"is_issue": True, "kind": "contradiction", "topic": "Q3 revenue was rising",
                           "claim": "rising", "said_by": ["Marcus Chen"], "line_numbers": [1],
                           "finding": "The board deck says it fell 4%.", "reasoning": "r",
                           "confidence": 0.9, "passage_numbers": [0]},
        "record_answer": {"spoken": "The board deck says Q3 revenue fell 4 percent.",
                          "chat_line": "Q3 revenue fell 4% (board deck).", "passage_numbers": [0]},
        "record_summary": {"key_topics": ["Q3 revenue"], "takeaways": ["Revenue fell."],
                           "action_items": [{"text": "Send the bridge", "owner": "Dana Lee"}],
                           "unsettled_alert_ids": []},
    }
    hosts = []

    def handler(request):
        hosts.append(request.url.host)
        assert request.url.host == "generativelanguage.googleapis.com", "Anthropic must not be called"
        system = json.loads(request.content)["systemInstruction"]["parts"][0]["text"]
        tool = next(t for t in replies if t in system)
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": json.dumps(replies[tool])}]}}]})

    p = provider_with(handler)
    v = asyncio.run(p.judge("gemini-2.5-flash", LINES, PASSAGES, []))
    a = asyncio.run(p.answer("gemini-2.5-flash", "What was Q3 revenue?", "Tom Walsh", PASSAGES, 60))
    s = asyncio.run(p.summarize("gemini-2.5-flash", LINES, []))
    assert set(hosts) == {"generativelanguage.googleapis.com"} and len(hosts) == 3
    assert v.is_issue and v.model == "gemini-2.5-flash" and v.said_by == ["Marcus Chen"]
    assert a.spoken.startswith("The board deck") and a.model == "gemini-2.5-flash"
    assert s.action_items[0].owner == "Dana Lee" and s.model == "gemini-2.5-flash"


def test_cheap_check_can_move_to_claude_the_same_way(keys):
    def handler(request):
        assert request.url.host == "api.anthropic.com"
        return claude_reply("record_check", {"worth_a_look": True, "score": 0.7, "topic": "Q3 revenue"})
    c = asyncio.run(provider_with(handler).cheap_check("claude-haiku-4-5-20251001", LINES, PASSAGES, []))
    assert c.worth_a_look and c.score == 0.7


def test_models_are_told_which_lines_are_new_after_a_skipped_backlog(keys):
    seen = {}
    def handler(request):
        seen["prompt"] = json.loads(request.content)["contents"][0]["parts"][0]["text"]
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [
            {"text": '{"worth_a_look": false, "score": 0, "topic": ""}'}]}}]})
    asyncio.run(provider_with(handler).cheap_check("gemini-3.5-flash-lite", LINES, PASSAGES, [], new_lines=2))
    assert "the last 2 lines are new" in seen["prompt"]
