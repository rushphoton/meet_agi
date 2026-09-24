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


class SettingsStore:
    """The user-editable Settings, persisted to data/settings.json."""

    def __init__(self, data_dir: Path) -> None:
        self.path = Path(data_dir) / "settings.json"
        self._settings = Settings()
        if self.path.exists():
            self._settings = Settings.model_validate_json(self.path.read_text(encoding="utf-8"))

    def get(self) -> Settings:
        return self._settings

    def put(self, settings: Settings) -> Settings:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(settings.model_dump(mode="json"), indent=1), encoding="utf-8")
        self._settings = settings
        return settings
