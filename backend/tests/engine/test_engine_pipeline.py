"""
The engine end to end on a real bus and store, with a scripted AI provider.
Failure paths first: outages, slow models, the gate, mute, stop, silence.
"""
from backend.app.contract.events import Mute, Wake
from backend.app.contract.records import GateSettings, Settings, WakeSettings
from backend.tests.engine.harness import Harness, LLMError, Scripted, flag_when, run, verdict

DISPUTE = "Q3 revenue was rising, up about three percent on Q2."
QUESTION = "Hey AGI, what was Q3 revenue according to the board deck?"


def alerts(h, gated=None):
    return [a for a in h.record.alerts if gated is None or a.gated == gated]


def chats(h, reason=None):
    return [c for c in h.record.chat_posts if reason is None or c.reason == reason]


# ======================= vendor trouble =======================
def test_cheap_check_outage_skips_alerts_but_the_meeting_carries_on(tmp_path):
    async def go():
        h = Harness(tmp_path, Scripted(cheap=LLMError("HTTP 503"), verdict=verdict()))
        await h.say("Marcus Chen", DISPUTE)
        await h.say("Tom Walsh", QUESTION)
        await h.settle()
        assert alerts(h) == [] and h.provider.calls["judge"] == 0
        assert len(h.record.answers) == 1 and len(chats(h, "answer")) == 1
    run(go())


def test_judge_failure_gives_no_alert_and_no_chat(tmp_path):
    async def go():
        h = Harness(tmp_path, Scripted(cheap=flag_when("revenue"), verdict=LLMError("timed out after 8 s")))
        await h.say("Marcus Chen", DISPUTE)
        await h.settle()
        assert alerts(h) == [] and chats(h) == []
    run(go())


def test_answer_model_failure_says_so_out_loud_instead_of_going_silent(tmp_path):
    async def go():
        h = Harness(tmp_path, Scripted(answer_error=LLMError("HTTP 529 overloaded")))
        await h.say("Tom Walsh", QUESTION)
        await h.settle()
        [answer] = h.record.answers
        assert answer.text.startswith("Sorry, I couldn't look that up")
        assert chats(h, "answer")[0].text.startswith("Because you asked:")
    run(go())


def test_summary_model_failure_still_ends_the_meeting_with_a_summary_that_says_so(tmp_path):
    async def go():
        h = Harness(tmp_path, Scripted(cheap=flag_when("revenue"), verdict=verdict(),
                                       summary_error=LLMError("timed out")))
        await h.say("Marcus Chen", DISPUTE)
        await h.end()
        s = h.record.summary
        assert "SUMMARY UNAVAILABLE" in s.takeaways[0] and s.alert_count == 1
        assert s.follow_up_count == 1 and h.record.follow_ups[0].source_alert_id == h.record.alerts[0].alert_id
    run(go())


def test_summary_waits_for_a_slow_last_dispute_check(tmp_path):
    async def go():
        h = Harness(tmp_path, Scripted(cheap=flag_when("revenue"), verdict=verdict(), judge_delay=0.3))
        await h.say("Marcus Chen", DISPUTE)
        await h.end()   # ended while the judge is still thinking
        assert h.record.summary.alert_count == 1 and len(alerts(h)) == 1
        types = [e.type for e in h.events()]
        assert types.index("alert") < types.index("meeting.summary")
    run(go())


def test_meeting_ended_twice_gives_one_summary(tmp_path):
    async def go():
        h = Harness(tmp_path, Scripted())
        await h.say("Tom Walsh", "Let's wrap up.")
        await h.end()
        await h.end()
        assert len(h.events("meeting.summary")) == 1 and h.provider.calls["summary"] == 1
    run(go())


# ======================= the gate =======================
def test_cheap_check_below_threshold_never_pays_for_the_judge(tmp_path):
    async def go():
        h = Harness(tmp_path, Scripted(cheap=flag_when("revenue", score=0.3), verdict=verdict()))
        await h.say("Marcus Chen", DISPUTE)
        await h.settle()
        assert h.provider.calls == {"cheap": 1, "judge": 0, "answer": 0, "summary": 0} and alerts(h) == []
    run(go())


def test_unsure_verdict_is_recorded_for_the_dashboard_but_never_posted_to_chat(tmp_path):
    async def go():
        h = Harness(tmp_path, Scripted(cheap=flag_when("revenue"), verdict=verdict(confidence=0.5)))
        await h.say("Marcus Chen", DISPUTE)
        await h.settle()
        [a] = alerts(h)
        assert a.gated and "confidence" in a.gate_reason and not a.delivered_to_chat and chats(h) == []
    run(go())


def test_second_dispute_inside_the_cooldown_is_gated_and_after_it_posts(tmp_path):
    async def go():
        h = Harness(tmp_path, Scripted(cheap=flag_when("revenue"), verdict=verdict()))
        await h.say("Marcus Chen", DISPUTE, at=10)
        await h.say("Priya Nair", "No, revenue went down.", at=40)      # 30 s later: cooldown
        await h.say("Dana Lee", "Revenue was flat, I think.", at=200)   # well after
        await h.settle()
        assert [a.gated for a in alerts(h)] == [False, True, False]
        assert "cooldown" in alerts(h)[1].gate_reason
        assert len(chats(h, "alert")) == 2
    run(go())


def test_alert_cap_stops_chat_posts(tmp_path):
    async def go():
        settings = Settings(gate=GateSettings(cooldown_seconds=0, max_alerts_per_meeting=2))
        h = Harness(tmp_path, Scripted(cheap=flag_when("revenue"), verdict=verdict()), settings)
        for i in range(4):
            await h.say("Marcus Chen", f"Revenue claim number {i}.", at=10 + i * 10)
        await h.settle()
        assert [a.gated for a in alerts(h)] == [False, False, True, True]
        assert "cap" in alerts(h)[3].gate_reason and len(chats(h, "alert")) == 2
    run(go())


def test_huge_model_finding_still_fits_in_one_chat_message(tmp_path):
    async def go():
        long = "the board deck says " + "revenue fell sharply and " * 60 + "that is all."
        h = Harness(tmp_path, Scripted(cheap=flag_when("revenue"),
                                       verdict=verdict(topic="Q3 revenue was rising " * 5, finding=long)))
        await h.say("Marcus Chen", DISPUTE)
        await h.settle()
        [c] = chats(h, "alert")
        assert len(c.text) < 500 and c.text.startswith("Because you mentioned Q3 revenue was rising")
        assert c.text.endswith("Details in the dashboard.") and "…" in c.text
        assert len(alerts(h)[0].topic) <= 60 and alerts(h)[0].finding == long   # full text kept for dashboard
    run(go())


def test_muted_meeting_alert_goes_to_the_dashboard_only(tmp_path):
    async def go():
        h = Harness(tmp_path, Scripted(cheap=flag_when("revenue"), verdict=verdict()))
        await h.bus.publish(h.meeting_id, "mute", Mute(muted=True))
        await h.say("Marcus Chen", DISPUTE)
        await h.say("Tom Walsh", QUESTION)
        await h.settle()
        [a] = alerts(h)
        assert not a.gated and not a.delivered_to_chat
        assert h.record.answers[0].status == "muted"
        assert chats(h) and all(c.status == "suppressed_muted" for c in chats(h))
    run(go())


def test_disagreement_is_flagged_whoever_is_speaking(tmp_path):
    async def go():
        h = Harness(tmp_path, Scripted(cheap=flag_when("not sure"), verdict=verdict(topic="Q3 revenue direction")))
        await h.say("Priya Nair", "Hmm, I'm not sure that's right, I remember it being lower.")
        await h.settle()
        [a] = alerts(h)
        assert a.said_by == ["Priya Nair"] and a.reasoning and a.evidence
        assert chats(h, "alert")[0].text.startswith("Because you mentioned Q3 revenue direction:")
    run(go())


# ======================= speech mode =======================
def test_stop_phrase_when_the_bot_is_silent_does_nothing(tmp_path):
    async def go():
        h = Harness(tmp_path, Scripted())
        await h.say("Tom Walsh", "AGI, stop talking.")
        await h.settle()
        assert h.events("stop") == []
    run(go())


def test_stop_phrase_while_an_answer_is_being_prepared_drops_it(tmp_path):
    async def go():
        h = Harness(tmp_path, Scripted(answer_delay=0.5))
        await h.say("Tom Walsh", QUESTION)
        await h.say("Dana Lee", "AGI, stop talking.")
        await h.settle()
        [stop] = h.events("stop")
        assert stop.payload.trigger == "phrase" and h.record.answers == [] and chats(h) == []
    run(go())


def test_stop_phrase_right_after_an_answer_publishes_stop(tmp_path):
    async def go():
        h = Harness(tmp_path, Scripted())
        await h.say("Tom Walsh", QUESTION)
        await h.settle()
        await h.say("Tom Walsh", "Okay AGI stop.")
        assert len(h.events("stop")) == 1
    run(go())


def test_stop_button_drops_an_answer_being_prepared(tmp_path):
    async def go():
        from backend.app.contract.events import Stop
        h = Harness(tmp_path, Scripted(answer_delay=0.5))
        await h.say("Tom Walsh", QUESTION)
        await h.bus.publish(h.meeting_id, "stop", Stop(trigger="button"))
        await h.settle()
        assert h.record.answers == []
    run(go())


def test_bare_hey_agi_then_silence_says_it_did_not_catch_a_question(tmp_path):
    async def go():
        settings = Settings(wake=WakeSettings(question_wait_seconds=0))
        h = Harness(tmp_path, Scripted(), settings)
        await h.say("Tom Walsh", "Hey AGI.")
        await h.settle()
        [wake] = h.events("wake")
        assert wake.payload.question is None
        [answer] = h.record.answers
        assert answer.text == "Sorry, I didn't catch a question." and chats(h) == []
        assert h.provider.calls["answer"] == 0
    run(go())


def test_bare_hey_agi_takes_the_same_persons_next_sentence_as_the_question(tmp_path):
    async def go():
        h = Harness(tmp_path, Scripted(cheap=flag_when("revenue"), verdict=verdict()))
        await h.say("Tom Walsh", "Hey AGI.", at=100)
        await h.say("Dana Lee", "Revenue is a big topic.", at=102)   # someone else: not the question
        await h.say("Tom Walsh", "What was Q3 revenue?", at=104)
        await h.settle()
        [answer] = h.record.answers
        assert answer.question == "What was Q3 revenue?" and answer.asked_by == "Tom Walsh"
        assert chats(h, "answer")[0].text.startswith("Because you asked:")
        assert len(alerts(h)) == 1 and alerts(h)[0].said_by == ["Dana Lee"]   # the question was not checked
    run(go())


def test_question_that_comes_too_late_is_not_taken_as_the_question(tmp_path):
    async def go():
        h = Harness(tmp_path, Scripted())
        await h.say("Tom Walsh", "Hey AGI.", at=100)
        await h.say("Tom Walsh", "Anyway, moving on to churn.", at=130)   # 30 s later (> 8 s)
        await h.settle()
        [answer] = h.record.answers
        assert answer.text == "Sorry, I didn't catch a question." and h.provider.calls["answer"] == 0
    run(go())


def test_wake_button_with_a_typed_question_answers_it(tmp_path):
    async def go():
        h = Harness(tmp_path, Scripted())
        await h.bus.publish(h.meeting_id, "wake", Wake(trigger="button", question="What was Q3 revenue?"))
        await h.settle()
        [answer] = h.record.answers
        assert answer.question == "What was Q3 revenue?" and answer.asked_by is None
        assert answer.evidence and answer.evidence[0].document == "SAMPLE_board_deck_q3.md"
        assert len(h.events("wake")) == 1   # the engine does not echo the button's wake
    run(go())


def test_wake_button_without_a_question_takes_the_next_sentence_from_anyone(tmp_path):
    async def go():
        h = Harness(tmp_path, Scripted())
        await h.bus.publish(h.meeting_id, "wake", Wake(trigger="button"))
        await h.say("Priya Nair", "What was churn in Q3?")
        await h.settle()
        [answer] = h.record.answers
        assert answer.question == "What was churn in Q3?" and answer.asked_by == "Priya Nair"
    run(go())


def test_spoken_answer_is_held_to_the_word_limit(tmp_path):
    async def go():
        h = Harness(tmp_path, Scripted(answer_text="word " * 200))
        await h.say("Tom Walsh", QUESTION)
        await h.settle()
        assert len(h.record.answers[0].text.split()) <= 60
    run(go())
