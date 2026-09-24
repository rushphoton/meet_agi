"""Wake and stop phrase detection (DESIGN.md §4.5). Failure paths first: talking ABOUT the bot must not wake it."""
import pytest

from backend.app.contract.records import Settings
from backend.app.pipeline.phrases import detect_stop, detect_wake, looks_like_wake_attempt

S = Settings()
WAKE = S.wake.variants


@pytest.mark.parametrize("sentence", [
    "If you say hey AGI it answers.",
    "The hey AGI thing is cool.",
    "Quick side note, the bot we added answers when you say hey AGI, so be careful.",
    "Say hey AGI to it.",
    "I said hey AGI earlier and nothing happened.",
    "We called it hey agi internally.",
    "Hey everyone, AGI is the topic today.",
    "They hey agi.",
    "",
])
def test_talking_about_the_wake_word_does_not_fire(sentence):
    assert detect_wake(sentence, WAKE, S.wake.max_word_position) is None


def test_wake_phrase_after_four_filler_words_is_too_late():
    assert detect_wake("So um uh okay hey AGI what now", WAKE, 3) is None


@pytest.mark.parametrize("sentence, variant, question", [
    ("Hey AGI, what was Q3 revenue according to the board deck?", "hey agi",
     "what was Q3 revenue according to the board deck?"),
    ("hey a g i what was churn", "hey a g i", "what was churn"),
    ("Hey aji, what's the pipeline?", "hey aji", "what's the pipeline?"),
    ("Hey AGI.", "hey agi", None),
    ("hey agi.", "hey agi", None),
    ("Okay, hey AGI, what was gross margin?", "hey agi", "what was gross margin?"),
    ("So um hey agee tell me about churn", "hey agee", "tell me about churn"),
    ("Hi AGI - what drove margin?", "hi agi", "what drove margin?"),
])
def test_transcription_variants_of_hey_agi_fire(sentence, variant, question):
    match = detect_wake(sentence, WAKE, S.wake.max_word_position)
    assert match is not None and match.variant == variant and match.question == question


def test_wake_variants_come_from_settings():
    assert detect_wake("Hey edgy, what was revenue?", WAKE) is None
    assert detect_wake("Hey edgy, what was revenue?", WAKE + ["hey edgy"]).question == "what was revenue?"


@pytest.mark.parametrize("sentence", ["Stop the recording please.", "AGI stopped working yesterday.",
                                      "We should stop talking about AGI.", "Let's talk about AGI."])
def test_sentences_that_merely_contain_stop_or_agi_are_not_stop_commands(sentence):
    assert detect_stop(sentence, S.stop_variants) is None


@pytest.mark.parametrize("sentence", ["AGI, stop talking.", "Okay AGI stop.", "Stop talking, AGI!",
                                      "a g i stop talking", "Aji, stop talking please."])
def test_stop_phrase_variants_are_recognised_anywhere_in_the_sentence(sentence):
    assert detect_stop(sentence, S.stop_variants) is not None


# ---------------- review B item 7: find the wake spellings rehearsal actually produces ----------------
@pytest.mark.parametrize("sentence", ["Hey Aggie, what was Q3 revenue?", "Okay hey AJ what's churn",
                                      "Hi Ajay.", "So hey AI, tell me the margin"])
def test_mistranscribed_wake_phrase_is_recognised_as_a_likely_miss(sentence):
    assert detect_wake(sentence, WAKE) is None
    assert looks_like_wake_attempt(sentence)


@pytest.mark.parametrize("sentence", ["They said hey to everyone.", "Whatever, hi.", "The hey AGI thing is cool."])
def test_sentences_that_do_not_open_with_hey_are_not_likely_misses(sentence):
    assert not looks_like_wake_attempt(sentence)
