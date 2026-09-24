"""The alert gate and the document search. Failure paths first."""
import time

from backend.app.contract.records import GateSettings
from backend.app.knowledge import KnowledgeBase
from backend.app.pipeline.gate import check_gate
from backend.tests.engine.harness import SAMPLE_DOC

G = GateSettings()  # 0.75 confidence, 90 s cooldown, 8 alerts


# ---------------- gate ----------------
def test_unsure_verdict_is_blocked_with_a_reason():
    r = check_gate(0.6, now_t=100, last_alert_t=None, alerts_so_far=0, gate=G)
    assert not r.passed and "confidence" in r.reason


def test_second_alert_inside_the_cooldown_is_blocked():
    r = check_gate(0.9, now_t=150, last_alert_t=100, alerts_so_far=1, gate=G)
    assert not r.passed and "cooldown" in r.reason


def test_ninth_alert_is_blocked_by_the_cap():
    r = check_gate(0.99, now_t=1000, last_alert_t=100, alerts_so_far=8, gate=G)
    assert not r.passed and "cap" in r.reason


def test_gate_numbers_are_settings():
    loose = GateSettings(min_confidence=0.5, cooldown_seconds=10, max_alerts_per_meeting=1)
    assert check_gate(0.6, now_t=20, last_alert_t=5, alerts_so_far=0, gate=loose).passed
    assert not check_gate(0.6, now_t=20, last_alert_t=5, alerts_so_far=1, gate=loose).passed


def test_confident_first_alert_passes():
    assert check_gate(0.8, now_t=10, last_alert_t=None, alerts_so_far=0, gate=G).passed


# ---------------- knowledge ----------------
def test_empty_or_missing_knowledge_folder_finds_nothing_instead_of_crashing(tmp_path):
    assert KnowledgeBase(tmp_path / "does-not-exist").search("Q3 revenue") == []
    (tmp_path / "empty").mkdir()
    assert KnowledgeBase(tmp_path / "empty").search("Q3 revenue") == []


def test_one_unreadable_document_does_not_break_search_of_the_others(tmp_path):
    (tmp_path / "broken.pdf").write_bytes(b"not really a pdf")
    (tmp_path / "SAMPLE_board_deck_q3.md").write_text(SAMPLE_DOC.read_text(encoding="utf-8"), encoding="utf-8")
    hits = KnowledgeBase(tmp_path).search("Q3 revenue")
    assert hits and hits[0].document == "SAMPLE_board_deck_q3.md"


def test_blank_question_finds_nothing(tmp_path):
    (tmp_path / "a.md").write_text("Revenue fell.", encoding="utf-8")
    assert KnowledgeBase(tmp_path).search("the and of ?") == []


def test_readme_in_the_knowledge_folder_is_not_a_document(tmp_path):
    (tmp_path / "README.md").write_text("Revenue revenue revenue.", encoding="utf-8")
    assert KnowledgeBase(tmp_path).search("revenue") == []


def test_edited_document_is_picked_up_without_a_restart(tmp_path):
    doc = tmp_path / "notes.md"
    doc.write_text("Churn was 3 percent.", encoding="utf-8")
    kb = KnowledgeBase(tmp_path)
    assert kb.search("pricing") == []
    time.sleep(0.01)
    doc.write_text("Churn was 3 percent.\n\nThe new pricing tier costs 40 dollars.", encoding="utf-8")
    assert kb.search("pricing")[0].document == "notes.md"


def test_passages_carry_their_document_and_heading():
    kb = KnowledgeBase(SAMPLE_DOC.parent)
    best = kb.search("Q3 revenue rising up on Q2", 3)[0]
    assert best.document == "SAMPLE_board_deck_q3.md" and best.locator == "Revenue summary"
    assert "down 4%" in best.text


def test_long_documents_are_cut_into_overlapping_passages(tmp_path):
    words = [f"w{i}" for i in range(300)]
    (tmp_path / "long.txt").write_text(" ".join(words), encoding="utf-8")
    kb = KnowledgeBase(tmp_path)
    assert kb.chunk_count("long.txt") == 3            # 0-119, 100-219, 200-299
    assert kb.search("w110")[0].text.count("w110") == 1
    assert len(kb.search("w110", 5)) == 2              # the overlap holds it twice
