"""
WHY THIS EXISTS
Loads configuration in one place: machine settings from .env (keys, flags,
paths) and the user-editable Settings (speakers, models, gates) saved as
data/settings.json by the dashboard's settings screen.

FAILURE IT PREVENTS
Each lane reading .env its own way, printing keys by accident, or two copies
of the settings drifting apart. Owned by the integrate step only.

DEPENDENCIES (CLAUDE.md rule 4): none - a tiny .env reader instead of
python-dotenv, because we need only KEY=VALUE lines.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from .contract.records import Settings

ROOT = Path(__file__).resolve().parents[2]


def load_dotenv(path: Path = ROOT / ".env") -> None:
    """Read KEY=VALUE lines into os.environ without overriding values already set."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Config:
    recall_webhook_token: str
    recall_workspace_secret: str
    recall_region: str
    public_base_url: str
    dev_mode: bool
    offline: bool
    data_dir: Path
    knowledge_dir: Path


def load_config() -> Config:
    load_dotenv()
    return Config(
        recall_webhook_token=os.environ.get("RECALL_WEBHOOK_TOKEN", ""),
        recall_workspace_secret=os.environ.get("RECALL_WORKSPACE_SECRET", ""),
        recall_region=os.environ.get("RECALL_REGION", "us-west-2"),
        public_base_url=os.environ.get("PUBLIC_BASE_URL", ""),
        dev_mode=_flag("DEV_MODE"),
        offline=_flag("OFFLINE"),
        data_dir=Path(os.environ.get("MEETAGI_DATA_DIR", ROOT / "data")),
        knowledge_dir=Path(os.environ.get("MEETAGI_KNOWLEDGE_DIR", ROOT / "knowledge")),
    )


# Defaults shipped before data/settings.defaults.json existed (gate v0.1, 27 Sep 2026). A saved value
# equal to one of these was never chosen by the user - it is an old default - so it is upgraded to
# today's default on load. Without this, a settings file saved before Round 2 silently kept the old
# stop phrases (all needing "AGI"), the 60-word cap and the 8 s question wait.
_OLD_WAKE = ["hey agi", "hey a g i", "hey aji", "hey age i", "hey a gi", "hey ag i", "hi agi", "hey agee"]
_CLAUDE = "claude-haiku-4-5-20251001"
PAST_DEFAULTS: dict[tuple[str, ...], list] = {
    ("models", "judge"): [_CLAUDE],
    ("models", "answer"): [_CLAUDE],
    ("models", "summary"): [_CLAUDE],
    ("wake", "variants"): [
        _OLD_WAKE,
        _OLD_WAKE + ["hey gi", "hey g i"],
        _OLD_WAKE + ["hey gi", "hey g i", "hey giant", "hey gi joe", "hey ajay", "hey aj", "hey edgy"],
    ],
    ("wake", "question_wait_seconds"): [8],
    ("stop_variants",): [["agi stop talking", "a g i stop talking", "aji stop talking", "agi stop",
                          "stop talking agi"]],
    ("answer_max_words",): [60],
    ("consent_text",): ['Meet AGI is listening to help with facts from our documents. It may post short '
                        'notes here. Say "Hey AGI" to ask it something, or "AGI, stop talking" to stop it.'],
}


def _merge(saved, shipped, current, path: tuple[str, ...] = ()):
    """Keep what the user changed; give everything they left at a default today's default.

    `shipped` is the defaults in force when the file was last saved (None if unknown)."""
    if isinstance(saved, dict) and isinstance(current, dict):
        out = dict(current)
        for key, value in saved.items():
            if key in current:
                sub = shipped.get(key) if isinstance(shipped, dict) else None
                out[key] = _merge(value, sub, current[key], path + (key,))
        return out
    untouched = (shipped is not None and saved == shipped) or saved in PAST_DEFAULTS.get(path, [])
    return current if untouched else saved


class SettingsStore:
    """The user-editable Settings, persisted to data/settings.json.

    Beside it, data/settings.defaults.json records the code's defaults at the time of the last save, so
    a later change to a default reaches every setting the user never touched (gate v0.1, item 1)."""

    def __init__(self, data_dir: Path) -> None:
        self.path = Path(data_dir) / "settings.json"
        self.defaults_path = Path(data_dir) / "settings.defaults.json"
        self._settings = Settings()
        if self.path.exists():
            saved = json.loads(self.path.read_text(encoding="utf-8"))
            shipped = (json.loads(self.defaults_path.read_text(encoding="utf-8"))
                       if self.defaults_path.exists() else None)
            current = Settings().model_dump(mode="json")
            self._settings = Settings.model_validate(_merge(saved, shipped, current))

    def get(self) -> Settings:
        return self._settings

    def put(self, settings: Settings) -> Settings:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(settings.model_dump(mode="json"), indent=1), encoding="utf-8")
        self.defaults_path.write_text(json.dumps(Settings().model_dump(mode="json"), indent=1),
                                      encoding="utf-8")
        self._settings = settings
        return settings
