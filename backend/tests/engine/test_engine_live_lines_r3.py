"""
Round 3: Ray's REAL caption lines from the 27 Sep live test, replayed with real Gemini keys through the
round-2 code (orchestrator's replay). Each test is named after what went wrong; failure paths first.
The sentences are the exact caption lines. No vendor is called: a scripted provider, the canned
provider, or a fake network.
"""
import asyncio
import json

import httpx
import pytest

from backend.app.contract.records import Settings, WakeSettings
from backend.app.knowledge import Passage
from backend.app.pipeline.phrases import detect_direct_address, is_summons
from backend.app.providers.llm import CannedProvider, RealProvider
from backend.app.providers.llm.base import WakeCheck, says_not_in_docs
from backend.app.providers.llm.vendors import VendorClient
from backend.tests.engine.harness import Harness, LLMError, Scripted, run

RAY = "Ray Wan"
FAKE_KEY = "sk-test-FAKE-not-a-real-key-000000000000000000"
IM_HERE = "I'm here. What's your question?"


def wakes(h):
    return h.events("wake")


def answers(h):
    return h.record.answers


# ============================ 1. "Hey AJI, stop talking." became a wake ============================
@pytest.mark.parametrize("sentence", ["Hey AJI, stop talking.", "Hey AGI, stop talking."])
def test_hey_aji_stop_talking_became_a_wake_and_the_bot_said_it_would_stop(tmp_path, sentence):
    """Live: wake with question "stop talking", then the bot SPOKE "understood, I will stop talking
    right now." With nothing playing it must be silently ignored: no wake, no answer, no chat."""
    async def go():
        h = Harness(tmp_path, Scripted(wake=True))
        await h.say(RAY, sentence)
        await h.settle()
        assert wakes(h) == [] and answers(h) == [] and h.record.chat_posts == []
        assert h.events("stop") == []
        assert h.provider.calls["answer"] == 0 and h.provider.wake_calls == []
        assert h.provider.calls["cheap"] == 0          # not even a dispute check: nothing to say
    run(go())


@pytest.mark.parametrize("sentence", ["Hey AJI, stop talking.", "Hey AGI, stop talking."])
def test_hey_aji_stop_talking_while_an_answer_is_being_prepared_stops_it(tmp_path, sentence):
    async def go():
        h = Harness(tmp_path, Scripted(answer_delay=0.5))
        await h.say(RAY, "Hey AGI, what's the revenue for Q3?")
        await h.say(RAY, sentence)
        await h.settle()
        [stop] = h.events("stop")
        assert stop.payload.trigger == "phrase" and answers(h) == []
        assert len(wakes(h)) == 1                       # only the real question woke it
    run(go())


def test_hey_aji_stop_talking_while_the_answer_is_playing_stops_it(tmp_path):
    async def go():
        h = Harness(tmp_path, Scripted())
        await h.say(RAY, "Hey AGI, what's the revenue for Q3?")
        await h.settle()                                # answer queued = speaking
        await h.say(RAY, "Hey AJI, stop talking.")
        await h.settle()
        assert len(h.events("stop")) == 1 and len(wakes(h)) == 1 and len(answers(h)) == 1
    run(go())


def test_stop_phrase_after_a_sounds_like_wake_cancels_that_wake(tmp_path):
    async def go():
        h = Harness(tmp_path, Scripted(wake=True, wake_delay=0.2))
        await h.say(RAY, "Hey Aggie.", at=10)
        await h.say("Dana Lee", "Stop talking.", at=11)
        await h.settle()
        assert wakes(h) == [] and answers(h) == []
    run(go())


def test_long_sentence_with_a_stop_word_is_still_checked_for_disputes_but_never_wakes(tmp_path):
    async def go():
        h = Harness(tmp_path, Scripted(wake=True))
        await h.say(RAY, "Stop, Q3 revenue was rising, up three percent on Q2.")
        await h.settle()
        assert wakes(h) == [] and answers(h) == [] and h.provider.calls["cheap"] == 1
    run(go())


# ============================ 2. doubled "not in your documents" ============================
def _gemini(reply, seen):
    def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": json.dumps(reply)}]}}]})
    return RealProvider(VendorClient(transport=httpx.MockTransport(handler)))


def _answer(monkeypatch, reply, question="Are you here?"):
    monkeypatch.setenv("GEMINI_API_KEY", FAKE_KEY)
    seen = []
    p = _gemini(reply, seen)
    a = asyncio.run(p.answer("gemini-3.5-flash-lite", question, RAY,
                             [Passage("SAMPLE_board_deck_q3.md", "Q3 revenue was $41.2M.", "Revenue")], 120))
    return a, seen[0]["systemInstruction"]["parts"][0]["text"]


LIVE_DOUBLED = ("That is not in your documents, but generally speaking, yes, I am here and ready to help you "
                "with the meeting. What do you need?")


@pytest.mark.parametrize("reply", [
    {"spoken": LIVE_DOUBLED, "chat_line": "x", "passage_numbers": [], "from_documents": False},   # old shape
    {"spoken": LIVE_DOUBLED, "chat_line": "x", "passage_numbers": [], "source": "general", "from_documents": False},
    {"spoken": "That isn't in your docs, but SaaS margins usually run 70 to 80 percent.", "chat_line": "x",
     "passage_numbers": [], "source": "general", "from_documents": False},
])
def test_not_in_your_documents_was_said_twice_in_one_answer(monkeypatch, reply):
    a, _ = _answer(monkeypatch, reply)
    assert a.spoken == reply["spoken"]                 # already said: nothing added
    assert a.spoken.lower().count("in your") == 1


def test_general_answer_without_the_opening_still_gets_it_once(monkeypatch):
    a, system = _answer(monkeypatch, {"spoken": "SaaS margins usually run 70 to 80 percent.", "chat_line": "x",
                                      "passage_numbers": [0], "source": "general", "from_documents": False})
    assert a.spoken.startswith("That's not in your documents, but generally, ")
    assert a.spoken.lower().count("not in your documents") == 1
    assert a.passage_indexes == []
    assert "conversational" in system and "Do NOT write any" in system


@pytest.mark.parametrize("spoken, expected", [
    ("That's not in your documents, but generally, yes, I'm here and ready to help. What do you need?",
     "Yes, I'm here and ready to help. What do you need?"),
    ("Yes, I'm here. What's your question?", "Yes, I'm here. What's your question?"),
])
def test_are_you_here_got_a_not_in_your_documents_disclaimer(monkeypatch, spoken, expected):
    """Live: "Hey Giant, are you here?" -> "That's not in your documents, but generally, ... yes, I am here"."""
    a, _ = _answer(monkeypatch, {"spoken": spoken, "chat_line": "Yes, I'm here.", "passage_numbers": [0],
                                 "source": "conversational", "from_documents": False})
    assert a.spoken == expected and a.passage_indexes == [] and not says_not_in_docs(a.spoken)


def test_document_answer_is_left_alone(monkeypatch):
    a, _ = _answer(monkeypatch, {"spoken": "The board deck says Q3 revenue was 41.2 million dollars.",
                                 "chat_line": "x", "passage_numbers": [0], "source": "documents",
                                 "from_documents": True})
    assert a.spoken.startswith("The board deck says") and a.passage_indexes == [0]


# ============================ 3. "Agi wake up!" / "AGI, what's churn?" did not wake ============================
def test_agi_wake_up_did_not_wake_the_bot(tmp_path):
    async def go():
        h = Harness(tmp_path, Scripted(wake=False))    # exact "agi": no model call needed
        await h.say(RAY, "Agi wake up!", at=234)
        await h.settle()
        [wake] = wakes(h)
        assert wake.payload.question is None and h.provider.wake_calls == []
        assert [a.text for a in answers(h)] == [IM_HERE] and h.provider.calls["answer"] == 0
        assert [c for c in h.record.chat_posts if c.reason == "answer"] == []
        await h.say(RAY, "What was churn last quarter?", at=240)    # the question that follows is answered
        await h.settle()
        assert [a.question for a in answers(h)][1:] == ["What was churn last quarter?"]
    run(go())


def test_agi_wake_up_then_silence_does_not_also_say_sorry(tmp_path):
    async def go():
        h = Harness(tmp_path, Scripted(), Settings(wake=WakeSettings(question_wait_seconds=1)))
        await h.say(RAY, "Agi wake up!", at=234)
        await asyncio.sleep(1.3)
        await h.settle()
        assert [a.text for a in answers(h)] == [IM_HERE]
    run(go())


def test_agi_whats_churn_without_hey_did_not_wake_the_bot(tmp_path):
    async def go():
        h = Harness(tmp_path, Scripted(wake=False))
        await h.say(RAY, "AGI, what's churn?")
        await h.settle()
        [wake] = wakes(h)
        assert wake.payload.question == "what's churn?" and h.provider.wake_calls == []
        assert [a.question for a in answers(h)] == ["what's churn?"]
    run(go())


@pytest.mark.parametrize("sentence", ["AGI is a big topic", "AGI is a big topic.", "AGI will change finance."])
def test_talking_about_agi_at_the_start_of_a_sentence_does_not_wake(tmp_path, sentence):
    async def go():
        h = Harness(tmp_path, Scripted(wake=True))     # even a yes-saying model is never asked
        await h.say(RAY, sentence)
        await h.settle()
        assert wakes(h) == [] and answers(h) == [] and h.provider.wake_calls == []
        assert h.provider.calls["cheap"] == 1           # an ordinary sentence: dispute-checked as usual
    run(go())


def test_unclear_agi_opening_goes_to_the_model_and_its_no_is_final(tmp_path):
    async def go():
        h = Harness(tmp_path, Scripted(wake=False))
        await h.say(RAY, "AGI, as a concept, is overhyped.")
        await h.settle()
        assert len(h.provider.wake_calls) == 1 and wakes(h) == []
    run(go())


def test_sounds_like_agi_opening_is_confirmed_by_the_model(tmp_path):
    async def go():
        h = Harness(tmp_path, Scripted(wake=WakeCheck(True, "what's churn?", "m")))
        await h.say(RAY, "Aggie, what's churn?")
        await h.settle()
        [call] = h.provider.wake_calls
        assert call[2] == "aggie"
        [wake] = wakes(h)
        assert "direct address" in wake.payload.matched_variant and wake.payload.question == "what's churn?"
    run(go())


def test_sounds_like_agi_opening_does_not_wake_when_the_check_fails(tmp_path):
    async def go():
        h = Harness(tmp_path, Scripted(wake=LLMError("HTTP 503")))
        await h.say(RAY, "Gi, what's churn?")          # "gi" is strong after "hey", not on its own
        await h.settle()
        assert wakes(h) == []
    run(go())


@pytest.mark.parametrize("sentence, exact, clear", [
    ("Agi wake up!", True, True), ("AGI, what's churn?", True, True), ("a g i what was churn", True, True),
    ("Okay AGI tell me the margin", True, True), ("AGI!", True, True),
    ("AGI, as a concept, is overhyped.", True, False), ("Aggie, what's churn?", False, True),
])
def test_direct_address_shapes(sentence, exact, clear):
    d = detect_direct_address(sentence)
    assert d is not None and (d.exact, d.clear) == (exact, clear)


@pytest.mark.parametrize("sentence", ["AGI is a big topic", "Joe, wake up.", "Again, what was it?",
                                      "Agreed, what's next?", "Giant steps were taken.", "Hey AGI",
                                      "We talked about AGI, what's next?"])
def test_not_a_direct_address(sentence):
    assert detect_direct_address(sentence) is None


def test_wake_up_is_a_call_not_a_question():
    assert is_summons("wake up!") and is_summons("Wake up please") and is_summons("hello?")
    assert not is_summons("are you here?") and not is_summons("what's churn?") and not is_summons(None)


# ============================ 4. what already worked keeps working ============================
def test_hey_giant_are_you_here_still_wakes_and_is_answered(tmp_path):
    async def go():
        h = Harness(tmp_path, Scripted())
        await h.say(RAY, "Hey Giant, are you here?")
        await h.settle()
        [wake] = wakes(h)
        assert wake.payload.question == "are you here?" and h.provider.calls["answer"] == 1
    run(go())


def test_hey_gi_whats_the_revenue_for_q3_still_wakes_twice(tmp_path):
    async def go():
        h = Harness(tmp_path, Scripted())
        await h.say(RAY, "Hey, GI, what's the revenue for Q3?", at=25)
        await h.settle()
        await h.say(RAY, "Hey, GI, what's the revenue for Q3?", at=41)
        await h.settle()
        assert [w.payload.question for w in wakes(h)] == ["what's the revenue for Q3?"] * 2
    run(go())


def test_stop_talking_agi_still_stops(tmp_path):
    async def go():
        h = Harness(tmp_path, Scripted(answer_delay=0.5))
        await h.say(RAY, "Hey AGI!", at=7)
        await h.say(RAY, "Stop talking AGI!", at=17)
        await h.settle()
        assert len(h.events("stop")) == 1 and answers(h) == []
    run(go())


def test_q3_rising_then_no_wait_thats_wrong_still_gives_one_alert(tmp_path):
    async def go():
        h = Harness(tmp_path, CannedProvider("running under tests"))
        await h.say(RAY, "Q3 revenue was rising, up three percent. No wait, that's wrong.", at=207)
        await h.say(RAY, "Revenue for Q3 is wrong.", at=221)
        await h.settle()
        assert len(h.record.alerts) == 1 and not h.record.alerts[0].gated
    run(go())


@pytest.mark.parametrize("addressed, woke", [(False, False), (True, True)])
def test_hey_joe_wake_up_wakes_only_if_the_model_says_it_was_addressed(tmp_path, addressed, woke):
    async def go():
        h = Harness(tmp_path, Scripted(wake=addressed))
        await h.say(RAY, "Hey Joe, wake up.")
        await h.settle()
        assert len(h.provider.wake_calls) == 1 and bool(wakes(h)) is woke
        if woke:   # "wake up" is a call, not a question
            assert wakes(h)[0].payload.question is None and [a.text for a in answers(h)] == [IM_HERE]
    run(go())
