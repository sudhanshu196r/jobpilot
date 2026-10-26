"""Process-level configuration, read once from the environment.

Everything a user is expected to change at runtime (sources, model, API key,
preferences) lives in the database-backed settings store instead; see
``app.services.settings_store``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader so the app runs without python-dotenv."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


ROOT_DIR = Path(__file__).resolve().parent.parent
_load_dotenv(ROOT_DIR / ".env")


@dataclass(frozen=True)
class Config:
    data_dir: Path = field(
        default_factory=lambda: Path(os.environ.get("JOBPILOT_DATA_DIR", ROOT_DIR / "data")).resolve()
    )
    host: str = field(default_factory=lambda: os.environ.get("JOBPILOT_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: int(os.environ.get("JOBPILOT_PORT", "8765")))
    anthropic_api_key: str = field(default_factory=lambda: os.environ.get("ANTHROPIC_API_KEY", ""))
    # Disable the background ingestion loop (useful for tests and one-off runs).
    disable_scheduler: bool = field(
        default_factory=lambda: os.environ.get("JOBPILOT_DISABLE_SCHEDULER", "") in {"1", "true", "yes"}
    )

    @property
    def db_path(self) -> Path:
        return self.data_dir / "jobpilot.db"

    @property
    def files_dir(self) -> Path:
        return self.data_dir / "files"


config = Config()
