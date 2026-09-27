"""
WHY THIS EXISTS
Findings from gate review v0.1 (reviews/gate-v0.1.md, 27 Sep 2026), each reproduced as a test named after
what the room would see. Engine and meeting findings live here too, so the gate's fixes are proven in one
place.
"""
import json

from backend.app.contract.records import Settings
from backend.app.settings import SettingsStore

OLD_STOP = ["agi stop talking", "a g i stop talking", "aji stop talking", "agi stop", "stop talking agi"]


# ---- item 1: a saved settings file hid new defaults ------------------------------------------------

def test_saved_settings_hid_the_new_stop_phrases(tmp_path):
    old = Settings().model_dump(mode="json")
    old["stop_variants"] = OLD_STOP
    old["answer_max_words"] = 60
    old["wake"]["question_wait_seconds"] = 8
    old["speakers"] = [{"match_name": "Ray", "display_name": "Ray Wan", "role": "CFO"}]
    (tmp_path / "settings.json").write_text(json.dumps(old), encoding="utf-8")

    s = SettingsStore(tmp_path).get()
    assert "stop talking" in s.stop_variants          # old default upgraded
    assert s.answer_max_words == 120
    assert s.wake.question_wait_seconds == 15
    assert s.speakers[0].display_name == "Ray Wan"    # the user's own choices survive


def test_saved_settings_keep_a_value_the_user_chose(tmp_path):
    chosen = Settings().model_dump(mode="json")
    chosen["answer_max_words"] = 80
    chosen["gate"]["min_confidence"] = 0.9
    (tmp_path / "settings.json").write_text(json.dumps(chosen), encoding="utf-8")
    s = SettingsStore(tmp_path).get()
    assert s.answer_max_words == 80
    assert s.gate.min_confidence == 0.9


def test_a_later_default_change_reaches_settings_saved_from_the_dashboard(tmp_path, monkeypatch):
    store = SettingsStore(tmp_path)
    store.put(Settings(answer_max_words=80))          # saved from the dashboard: stamps today's defaults
    # A later release changes the default fillers; the user never touched them.
    import backend.app.settings as settings_mod
    new_fillers = ["One sec."]

    class Later(Settings):
        fillers: list[str] = new_fillers

    monkeypatch.setattr(settings_mod, "Settings", Later)
    s = settings_mod.SettingsStore(tmp_path).get()
    assert s.fillers == new_fillers
    assert s.answer_max_words == 80
