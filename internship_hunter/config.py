from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def load_dotenv(path: Path) -> None:
    """Charge un fichier .env simple (KEY=VALUE) sans écraser l'environnement."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), value)


@dataclass
class Config:
    locations: list[str]
    radius_km: int = 25
    search_terms: list[str] = field(default_factory=lambda: ["stage"])
    title_must_match: list[str] = field(default_factory=list)
    exclude_keywords: list[str] = field(default_factory=list)
    highlight_keywords: list[str] = field(default_factory=list)
    only_highlighted: bool = False
    sources: dict[str, bool] = field(default_factory=dict)
    max_age_days: int = 14
    max_notifications_per_run: int = 30
    interval_minutes: int = 30
    database: str = "data/internship_hunter.db"
    cv_path: str = "cv.pdf"
    profile_path: str = "profil.md"
    claude_model: str = "claude-opus-5-5"

    telegram_token: str = ""
    telegram_chat_id: str = ""
    adzuna_app_id: str = ""
    adzuna_app_key: str = ""
    anthropic_api_key: str = ""

    @property
    def database_path(self) -> Path:
        p = Path(self.database)
        return p if p.is_absolute() else ROOT / p


def load_config(path: str | None = None) -> Config:
    load_dotenv(ROOT / ".env")
    if path:
        cfg_path = Path(path)
    else:
        cfg_path = ROOT / "config.yaml"
        if not cfg_path.is_file():
            cfg_path = ROOT / "config.example.yaml"
    data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}

    known = set(Config.__dataclass_fields__)
    unknown = set(data) - known
    if unknown:
        raise ValueError(f"Clés inconnues dans {cfg_path.name}: {', '.join(sorted(unknown))}")

    cfg = Config(**{k: v for k, v in data.items() if v is not None})
    cfg.telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    cfg.telegram_chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    cfg.adzuna_app_id = os.environ.get("ADZUNA_APP_ID", "").strip()
    cfg.adzuna_app_key = os.environ.get("ADZUNA_APP_KEY", "").strip()
    cfg.anthropic_api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    return cfg
