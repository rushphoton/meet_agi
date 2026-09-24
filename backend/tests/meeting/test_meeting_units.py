"""
Small, pure pieces of the meeting lane, failure paths first (CLAUDE.md rule 5):
the webhook token and signature checks, the chat length limit, the MP3 clip
length reader and the sentence assembler.
"""
import base64
import hashlib
import hmac

from backend.app.integrations.chat import fit_chat
from backend.app.integrations.signature import recall_signature_valid, token_matches
from backend.app.providers.voice.mp3 import mp3_duration
from backend.app.speech.assembler import SentenceAssembler, Word
from backend.tests.meeting.conftest import ROOT

ASSETS = ROOT / "backend/app/providers/voice/assets"
SECRET = "whsec_" + base64.b64encode(b"a test secret, not a real one").decode()


def _sign(body: bytes, msg_id="msg_1", stamp="1700000000", secret=SECRET) -> dict:
    key = base64.b64decode(secret[len("whsec_"):])
    sig = base64.b64encode(hmac.new(key, f"{msg_id}.{stamp}.".encode() + body, hashlib.sha256).digest()).decode()
    return {"webhook-id": msg_id, "webhook-timestamp": stamp, "webhook-signature": f"v1,{sig}"}


def _words(text: str, start: float = 0.0, step: float = 0.3) -> list[Word]:
    out = []
    for w in text.split():
        out.append(Word(w, start, start + step))
        start += step
    return out


# ---------------- token ----------------
def test_empty_expected_token_never_matches_even_an_empty_one():
    assert token_matches("", "") is False
    assert token_matches("anything", "") is False


def test_token_mismatch_and_prefix_are_rejected():
    assert token_matches("abc", "abcd") is False
    assert token_matches("abcd", "abcd") is True


# ---------------- signature ----------------
def test_tampered_body_fails_signature():
    headers = _sign(b'{"a":1}')
    assert recall_signature_valid(b'{"a":2}', headers, SECRET, now=1700000000) is False


def test_wrong_secret_fails_signature():
    headers = _sign(b"{}")
    other = "whsec_" + base64.b64encode(b"another secret").decode()
    assert recall_signature_valid(b"{}", headers, other, now=1700000000) is False


def test_stale_timestamp_fails_signature():
    headers = _sign(b"{}")
    assert recall_signature_valid(b"{}", headers, SECRET, now=1700000000 + 3600) is False


def test_missing_headers_or_bad_secret_fail_signature():
    assert recall_signature_valid(b"{}", {}, SECRET) is False
    assert recall_signature_valid(b"{}", _sign(b"{}"), "not-a-whsec", now=1700000000) is False


def test_valid_signature_passes_with_webhook_or_svix_header_names():
    headers = _sign(b'{"ok":true}')
    assert recall_signature_valid(b'{"ok":true}', headers, SECRET, now=1700000000)
    svix = {k.replace("webhook-", "svix-"): v for k, v in headers.items()}
    assert recall_signature_valid(b'{"ok":true}', svix, SECRET, now=1700000000)


# ---------------- chat length ----------------
def test_chat_over_500_characters_is_cut_at_a_word_with_ellipsis():
    text = ("word " * 200).strip()
    out = fit_chat(text)
    assert len(out) <= 500 and out.endswith("…")
    assert out[:-1].split()[-1] == "word"


def test_chat_counts_emoji_as_two_units_like_the_browser():
    text = "😀" * 300  # 300 characters, but 600 UTF-16 units
    out = fit_chat(text)
    assert len(out.encode("utf-16-le")) // 2 <= 500


def test_chat_at_exactly_500_is_unchanged():
    text = "x" * 500
    assert fit_chat(text) == text


# ---------------- clip length ----------------
def test_garbage_bytes_fall_back_to_a_bitrate_estimate():
    assert abs(mp3_duration(b"\x00" * 16000) - 1.0) < 1e-6


def test_clip_lengths_match_ffprobe_within_a_tenth_of_a_second():
    # ffprobe said 6.583 s and 0.575 s when the clips were made
    assert abs(mp3_duration((ASSETS / "canned_sample_clip.mp3").read_bytes()) - 6.58) < 0.1
    assert abs(mp3_duration((ASSETS / "silence_half_second.mp3").read_bytes()) - 0.57) < 0.1


# ---------------- sentence assembly ----------------
def test_unfinished_fragment_is_held_until_the_sentence_ends():
    a = SentenceAssembler()
    assert a.add("1", "Dana", _words("Q3 revenue was", 0)) == []
    assert a.has_pending
    out = a.add("1", "Dana", _words("forty one million.", 1.0))
    assert [s.text for s in out] == ["Q3 revenue was forty one million."]
    assert out[0].speaker_name == "Dana" and out[0].t_start == 0.0


def test_pause_of_1_2_seconds_ends_a_sentence_without_punctuation():
    a = SentenceAssembler()
    a.add("1", "Dana", _words("so the number is", 0))
    out = a.add("1", "Dana", _words("anyway moving on", 5.0))
    assert [s.text for s in out] == ["so the number is"]
    assert a.has_pending


def test_different_speaker_ends_the_previous_sentence():
    a = SentenceAssembler()
    a.add("1", "Dana", _words("I think that", 0))
    out = a.add("2", "Marcus", _words("No it was higher.", 1.0))
    assert [(s.speaker_name, s.text) for s in out] == [("Dana", "I think that"), ("Marcus", "No it was higher.")]


def test_monologue_is_cut_at_40_words_after_the_last_full_stop():
    a = SentenceAssembler()
    text = "First short sentence here. " + " ".join(["blah"] * 60)
    out = a.add("1", "Dana", _words(text, 0, step=0.1))
    assert out[0].text == "First short sentence here."
    assert all(len(s.text.split()) <= 40 for s in out)
    assert sum(len(s.text.split()) for s in out) + len(a.flush()[0].text.split()) == 64


def test_one_unbroken_utterance_with_two_sentences_stays_one_line():
    a = SentenceAssembler()
    out = a.add("1", "Tom", _words("Thanks Tom. I'll walk through the numbers.", 0))
    assert [s.text for s in out] == ["Thanks Tom. I'll walk through the numbers."]


def test_blank_words_are_ignored_and_flush_on_empty_is_harmless():
    a = SentenceAssembler()
    assert a.add("1", "Dana", [Word(" ", 0, 0.1)]) == []
    assert a.flush() == []
