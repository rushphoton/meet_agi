"""
Ray's feedback after the first live Google Meet test (27 Sep 2026), each test named after
what he saw. Failure paths first. The caption lines are the real ones Meet wrote.
No vendor is called: a scripted provider, the canned provider, or a fake network.
"""
import asyncio
import json

import httpx
import pytest

from backend.app.contract.records import Settings, WakeSettings
from backend.app.pipeline import engine as engine_mod
from backend.app.providers.llm import CannedProvider, RealProvider
from backend.app.providers.llm.base import WakeCheck
from backend.app.providers.llm.vendors import VendorClient
from backend.tests.engine.harness import Harness, LLMError, Scripted, flag_when, run, verdict

RAY = "Ray Wan"
FAKE_KEY = "sk-test-FAKE-not-a-real-key-000000000000000000"


def wakes(h):
    return h.events("wake")


def answers(h):
    return h.record.answers


# ============================ 1. wake: "it has to know" ============================
@pytest.mark.parametrize("sentence", ["Hey guys, let's start.", "the bot answers when you say hey AGI",
                                      "if you say hey GI it answers", "The hey GI thing is cool."])
def test_greetings_and_talk_about_the_wake_word_never_wake_the_bot(tmp_path, sentence):
    async def go():
        h = Harness(tmp_path, Scripted(wake=True))   # even a model that says yes is never asked
        await h.say(RAY, sentence)
        await h.settle()
        assert wakes(h) == [] and answers(h) == [] and h.provider.wake_calls == []
    run(go())


@pytest.mark.parametrize("sentence", ["Hey Jim, can you share your screen?", "Hi Gina, how are you"])
def test_ordinary_greeting_that_sounds_like_agi_does_not_wake_when_the_model_says_no(tmp_path, sentence):
    async def go():
        h = Harness(tmp_path, Scripted(wake=False))
        await h.say(RAY, sentence)
        await h.settle()
        assert len(h.provider.wake_calls) == 1          # asked once ...
        assert wakes(h) == [] and answers(h) == []      # ... said no: nothing happens
        assert h.provider.calls["cheap"] == 1           # and the sentence is still dispute-checked
    run(go())


def test_wake_check_outage_wakes_only_for_the_strong_spellings(tmp_path):
    async def go():
        h = Harness(tmp_path, Scripted(wake=LLMError("HTTP 503")))
        h.settings.models.cheap_check = "gemini-3.5-flash-lite"
        await h.say(RAY, "Hey Aggie, what was Q3 revenue?", at=10)   # weak spelling: no wake
        await h.settle()
        assert wakes(h) == []
        await h.say(RAY, "Okay giant, what was churn?", at=40)        # strong spelling: wakes anyway
        await h.settle()
        [wake] = wakes(h)
        assert wake.payload.question == "what was churn?" and "check failed" in wake.payload.matched_variant
        warning = h.engine.warnings["engine.wake_check"]
        assert warning.startswith("Gemini wake check failing (HTTP 503)") and len(warning) < 120
    run(go())


def test_slow_wake_check_gives_up_after_its_timeout(tmp_path, monkeypatch):
    monkeypatch.setattr(engine_mod, "WAKE_CHECK_SECONDS", 0.1)   # 8 s in real life
    async def go():
        h = Harness(tmp_path, Scripted(wake=False, wake_delay=2))
        await h.say(RAY, "Yo AJ, what was gross margin?")
        await h.settle()
        [wake] = wakes(h)
        assert wake.payload.question == "what was gross margin?"
        assert "timed out" in h.engine.warnings["engine.wake_check"]
    run(go())


@pytest.mark.parametrize("sentence, question", [
    ("Hey GI!", None),
    ("Hey, GI, what was Q3 Revenue? According to the board deck.",
     "what was Q3 Revenue? According to the board deck."),
    ("Hey giant, what was churn?", "what was churn?"),
    ("Hey GI Joe what's the pipeline", "what's the pipeline"),
    ("Hey AGI!", None),
])
def test_hey_agi_captioned_as_gi_giant_or_gi_joe_did_not_wake_the_bot(tmp_path, sentence, question):
    async def go():
        h = Harness(tmp_path, Scripted(wake=False))   # exact spellings need no model call
        await h.say(RAY, sentence)
        await h.settle()
        [wake] = wakes(h)
        assert wake.payload.question == question and h.provider.wake_calls == []
    run(go())


def test_hey_aggie_wakes_when_the_cheap_model_confirms_it_was_addressed(tmp_path):
    async def go():
        h = Harness(tmp_path, Scripted(wake=WakeCheck(True, "What was Q3 revenue per the board deck?", "m")))
        h.settings.models.cheap_check = "gemini-3.5-flash-lite"
        await h.say(RAY, "Hey Aggie, what was Q3 revenue? According to the bortech.")
        await h.settle()
        [call] = h.provider.wake_calls
        assert call[0] == "gemini-3.5-flash-lite" and call[2] == "aggie"   # the cheap_check model
        [wake] = wakes(h)
        assert "sounds like hey agi; confirmed" in wake.payload.matched_variant
        [answer] = answers(h)
        assert answer.question == "What was Q3 revenue per the board deck?"   # the model's cleaned question
        assert "engine.wake_check" not in h.engine.warnings
    run(go())


def test_question_said_while_the_wake_check_is_running_is_not_lost(tmp_path):
    async def go():
        h = Harness(tmp_path, Scripted(wake=True, wake_delay=0.2, cheap=flag_when("revenue"), verdict=verdict()))
        await h.say(RAY, "Hey Aggie.", at=100)
        await h.say(RAY, "What was Q3 revenue?", at=102)   # arrives before the check returns
        await h.settle()
        [answer] = answers(h)
        assert answer.question == "What was Q3 revenue?" and h.record.alerts == []   # a question, not a claim
    run(go())


def test_canned_wake_check_says_yes_only_to_strong_spellings():
    p = CannedProvider("running under tests")
    assert asyncio.run(p.confirm_wake("x", "Hey Jim", "jim", [])).addressed is False
    assert asyncio.run(p.confirm_wake("x", "Hey Aggie", "aggie", [])).addressed is False
    assert asyncio.run(p.confirm_wake("x", "Okay giant", "giant", [])).addressed is True


# ============================ 2. question capture ============================
def test_question_that_came_24_s_after_hey_agi_got_sorry_instead_of_an_answer(tmp_path):
    """Live, 27 Sep: "Hey AGI!", then 24 s later "What was Q3 Revenue? According to the bortech."
    and the bot had already said "Sorry, I didn't catch a question"."""
    async def go():
        h = Harness(tmp_path, Scripted())             # default settings: 15 s wait
        await h.say(RAY, "Hey AGI!", at=100)          # line ends at 103
        await h.say(RAY, "What was Q3 Revenue? According to the bortech.", at=127)
        await h.settle()
        [answer] = answers(h)
        assert answer.question == "What was Q3 Revenue? According to the bortech." and answer.asked_by == RAY
    run(go())


def test_re_asked_question_after_didnt_catch_a_question_is_answered(tmp_path):
    async def go():
        h = Harness(tmp_path, Scripted(), Settings(wake=WakeSettings(question_wait_seconds=1)))
        await h.say(RAY, "Hey AGI!", at=100)          # ends at 103
        await asyncio.sleep(1.3)                      # nothing within the wait: "didn't catch"
        assert [a.text for a in answers(h)] == ["Sorry, I didn't catch a question."]
        await h.say("Dana Lee", "What was churn?", at=110)             # someone else: not a re-ask
        await h.say(RAY, "What was Q3 Revenue? According to the bortech.", at=115)   # 12 s after: answered
        await h.settle()
        assert [a.question for a in answers(h)][1:] == ["What was Q3 Revenue? According to the bortech."]
    run(go())


def test_statement_or_late_question_after_didnt_catch_is_not_taken_as_a_question(tmp_path):
    async def go():
        h = Harness(tmp_path, Scripted(), Settings(wake=WakeSettings(question_wait_seconds=1)))
        await h.say(RAY, "Hey AGI!", at=100)
        await asyncio.sleep(1.3)
        await h.say(RAY, "Let's review Q3.", at=110)                    # not a question
        await h.say(RAY, "What was churn?", at=150)                     # a question, but 47 s later
        await h.settle()
        assert len(answers(h)) == 1 and h.provider.calls["answer"] == 0
    run(go())


def test_question_twelve_seconds_after_hey_agi_is_now_inside_the_wait(tmp_path):
    async def go():
        h = Harness(tmp_path, Scripted())
        await h.say(RAY, "Hey AGI.", at=100)
        await h.say(RAY, "Total bookings, what did they come in at", at=115)   # 12 s after the line ended
        await h.settle()
        [answer] = answers(h)
        assert answer.question == "Total bookings, what did they come in at"
    run(go())


def test_answer_gets_the_recent_transcript_so_it_answers_what_was_asked(tmp_path):
    async def go():
        h = Harness(tmp_path, Scripted())
        await h.say(RAY, "Let's review Q3. Total bookings came in at.", at=10)
        await h.say(RAY, "52 million.", at=14)
        await h.say(RAY, "Hey GI, is that ahead of plan?", at=20)
        await h.settle()
        [context] = h.provider.answer_context
        assert [s.text for s in context][:2] == ["Let's review Q3. Total bookings came in at.", "52 million."]
    run(go())


# ============================ 3. stop without "AGI" ============================
@pytest.mark.parametrize("sentence", ["Stop talking.", "Okay stop."])
def test_stop_talking_when_the_bot_is_silent_does_nothing(tmp_path, sentence):
    async def go():
        h = Harness(tmp_path, Scripted())
        await h.say(RAY, sentence)
        await h.settle()
        assert h.events("stop") == []
    run(go())


@pytest.mark.parametrize("sentence", ["Stop talking.", "Okay stop."])
def test_stop_talking_without_saying_agi_did_not_stop_the_bot(tmp_path, sentence):
    async def go():
        h = Harness(tmp_path, Scripted(answer_delay=0.5))
        await h.say(RAY, "Hey AGI, what was Q3 revenue according to the board deck?")
        await h.say("Dana Lee", sentence)            # anyone in the room, no "AGI"
        await h.settle()
        [stop] = h.events("stop")
        assert stop.payload.trigger == "phrase" and answers(h) == []
    run(go())


def test_stop_word_right_after_an_answer_still_counts(tmp_path):
    async def go():
        h = Harness(tmp_path, Scripted())
        await h.say(RAY, "Hey AGI, what was Q3 revenue?")
        await h.settle()
        await h.say("Dana Lee", "Stop, thanks.")
        assert len(h.events("stop")) == 1
    run(go())


@pytest.mark.parametrize("sentence", ["Stop the recording please.", "Fair enough, my mistake on the direction."])
def test_ordinary_sentence_with_a_stop_word_does_not_cut_the_answer(tmp_path, sentence):
    async def go():
        h = Harness(tmp_path, Scripted(answer_delay=0.3))
        await h.say(RAY, "Hey AGI, what was Q3 revenue?")
        await h.say("Dana Lee", sentence)
        await h.settle()
        assert h.events("stop") == [] and len(answers(h)) == 1
    run(go())


# ============================ 4. one person contradicting themselves ============================
SELF_CORRECTION = ["Revenue was rising at about three percent on Q2.",   # real caption line
                   "No wait, that's wrong, it fell.",
                   "Hmm, not sure that's correct."]


def test_ray_contradicting_himself_about_revenue_got_no_alert(tmp_path):
    async def go():
        h = Harness(tmp_path, CannedProvider("running under tests"))
        for i, line in enumerate(SELF_CORRECTION):
            await h.say(RAY, line, at=10 + i * 4)
        await h.settle()
        [alert] = h.record.alerts
        assert alert.kind == "contradiction" and alert.said_by == [RAY] and not alert.gated
        assert alert.claim == SELF_CORRECTION[0] and len(alert.segment_ids) == 2
        assert alert.evidence[0].document == "SAMPLE_board_deck_q3.md"   # the documents cover it: cited
        assert "41.2M" in alert.finding
        [chat] = [c for c in h.record.chat_posts if c.reason == "alert"]
        assert chat.text.startswith(f"Because you mentioned revenue ({RAY} said it both ways):")
        assert len(chat.text) < 500 and "CANNED" in chat.text
    run(go())


def test_self_correction_in_one_breath_gives_one_alert(tmp_path):
    async def go():
        h = Harness(tmp_path, CannedProvider("running under tests"))
        await h.say(RAY, "Q3 revenue was rising... no wait, that's wrong, it fell... hmm, not sure that's correct.")
        await h.settle()
        assert len(h.record.alerts) == 1 and not h.record.alerts[0].gated
    run(go())


def test_self_contradiction_the_documents_dont_cover_still_alerts_and_says_so(tmp_path):
    async def go():
        h = Harness(tmp_path, CannedProvider("running under tests"), with_docs=False)
        for i, line in enumerate(["Churn was up to five percent.", "No wait, that's wrong, churn went down.",
                                  "Hmm, not sure that's correct."]):
            await h.say(RAY, line, at=10 + i * 4)
        await h.settle()
        [alert] = h.record.alerts
        assert alert.kind == "contradiction" and alert.evidence == [] and not alert.gated
        assert alert.finding == f"{RAY} said churn both ways; the documents don't settle it."
    run(go())


def test_doubt_about_a_number_alerts_as_uncertainty(tmp_path):
    async def go():
        h = Harness(tmp_path, CannedProvider("running under tests"))
        await h.say("Dana Lee", "Total bookings came in at 52 million.", at=10)
        await h.say(RAY, "Hmm, I'm not sure that's right.", at=14)
        await h.settle()
        [alert] = h.record.alerts
        assert alert.kind == "uncertainty" and alert.said_by == [RAY]
        assert "52.0M" in alert.finding and alert.evidence
    run(go())


def _gemini(handler_replies, seen):
    def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        system = body["systemInstruction"]["parts"][0]["text"]
        reply = next(v for k, v in handler_replies.items() if k in system)
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": json.dumps(reply)}]}}]})
    return RealProvider(VendorClient(transport=httpx.MockTransport(handler)))


def test_real_check_and_judge_prompts_name_self_correction_and_show_speakers(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", FAKE_KEY)
    from backend.app.contract.events import TranscriptSegment
    lines = [TranscriptSegment(segment_id=f"seg_{i}", speaker_id="ray", speaker_name=RAY, text=t,
                               t_start=i, t_end=i + 1, source="replay") for i, t in enumerate(SELF_CORRECTION)]
    seen = []
    p = _gemini({"worth_a_look": {"worth_a_look": True, "score": 0.9, "topic": "revenue both ways"},
                 "record_verdict": {"is_issue": True, "kind": "contradiction", "topic": "revenue going both ways",
                                    "claim": "rising", "said_by": [RAY], "line_numbers": [0, 1],
                                    "finding": "Ray said it rose, then fell; the documents don't settle it.",
                                    "reasoning": "r", "confidence": 0.85, "passage_numbers": []}}, seen)
    asyncio.run(p.cheap_check("gemini-3.5-flash-lite", lines, [], []))
    v = asyncio.run(p.judge("gemini-3.5-flash-lite", lines, [], []))
    for body in seen:
        system = body["systemInstruction"]["parts"][0]["text"]
        prompt = body["contents"][0]["parts"][0]["text"]
        assert "THEMSELVES" in system or "themselves" in system
        assert "this is right" in system and "hedg" in system.lower()
        assert f"{RAY}: No wait, that's wrong, it fell." in prompt
    assert v.is_issue and v.kind == "contradiction" and v.passage_indexes == [] and v.segment_indexes == [0, 1]


# ============================ 5. longer, well-formed answers ============================
FIVE_SENTENCES = ("Q3 revenue was 41.2 million dollars, down 4 percent from Q2, according to the board deck. "
                  + "The main reason was light bookings in the second quarter, which feed revenue a quarter later. "
                  + "Bookings themselves came in at 52 million dollars, about 2 percent ahead of plan for the quarter. "
                  + "Gross margin improved to 61 percent on lower hosting costs after the cloud migration was finished. "
                  + "The finance team will send the board a revenue bridge that reconciles bookings and revenue for "
                  + "everyone, and it should arrive well before the next board meeting in the autumn, together with "
                  + "an updated forecast model covering the late stage pipeline figures and the renewal timing, "
                  + "the churn assumptions for the enterprise segment, and the hosting cost savings expected next year.")


def test_long_answer_was_cut_mid_sentence(tmp_path):
    async def go():
        assert len(FIVE_SENTENCES.split()) > 120
        h = Harness(tmp_path, Scripted(answer_text=FIVE_SENTENCES))
        await h.say(RAY, "Hey AGI, how did Q3 go?")
        await h.settle()
        text = answers(h)[0].text
        assert len(text.split()) <= 120 and text.endswith("finished.") and "…" not in text
        assert FIVE_SENTENCES.startswith(text)
    run(go())


def test_120_word_answer_is_not_cut_at_all(tmp_path):
    async def go():
        exact = " ".join(FIVE_SENTENCES.split()[:119]) + " end."
        assert len(exact.split()) == 120
        h = Harness(tmp_path, Scripted(answer_text=exact))
        await h.say(RAY, "Hey AGI, how did Q3 go?")
        await h.settle()
        assert answers(h)[0].text == exact
    run(go())


def test_question_the_documents_dont_answer_got_i_couldnt_find_that(tmp_path):
    """Ray: answer anyway from general knowledge, and say so first."""
    async def go():
        h = Harness(tmp_path, CannedProvider("running under tests"), with_docs=False)
        await h.say(RAY, "Hey AGI, what is a typical SaaS gross margin?")
        await h.settle()
        [answer] = answers(h)
        label = "CANNED ANSWER: "        # every canned answer is labelled (CLAUDE.md rule 6)
        assert answer.text.startswith(label + "That's not in your documents, but generally")
        [chat] = [c for c in h.record.chat_posts if c.reason == "answer"]
        assert chat.text.startswith("Because you asked:")
    run(go())


@pytest.mark.parametrize("reply, starts", [
    ({"spoken": "Typical SaaS gross margins are 70 to 80 percent.", "chat_line": "70-80% typical",
      "passage_numbers": [], "from_documents": False}, "That's not in your documents, but generally, typical SaaS"),
    ({"spoken": "Typical SaaS gross margins are 70 to 80 percent.", "chat_line": "70-80% typical",
      "passage_numbers": []}, "That's not in your documents, but generally, typical SaaS"),
    ({"spoken": "That's not in your documents, but generally SaaS margins run 70 to 80 percent.",
      "chat_line": "x", "passage_numbers": [], "from_documents": False}, "That's not in your documents, but generally SaaS"),
    ({"spoken": "The board deck says gross margin was 61 percent.", "chat_line": "61%",
      "passage_numbers": [0], "from_documents": True}, "The board deck says"),
])
def test_general_knowledge_answer_always_says_so_first(monkeypatch, reply, starts):
    monkeypatch.setenv("GEMINI_API_KEY", FAKE_KEY)
    from backend.app.knowledge import Passage
    seen = []
    p = _gemini({"record_answer": reply}, seen)
    a = asyncio.run(p.answer("gemini-3.5-flash-lite", "What is gross margin?", RAY,
                             [Passage("SAMPLE_board_deck_q3.md", "Gross margin improved to 61%.", "Margin")], 120))
    assert a.spoken.startswith(starts)
    system = seen[0]["systemInstruction"]["parts"][0]["text"]
    assert "2 to 5 complete spoken sentences" in system and "at most 120 words" in system
    assert "most important fact FIRST" in system and "That's not in your documents, but generally" in system
