from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class ServiceConfig:
    storage_root: Path
    host: str = "127.0.0.1"
    port: int = 4174
    # Number of days a soft-deleted project remains recoverable on the
    # "Recently deleted" page before lazy-purge hard-deletes it. Override
    # via the ``INSPIRA_DELETED_PROJECT_GRACE_DAYS`` environment variable.
    deleted_project_grace_days: int = 30

    @property
    def sessions_root(self) -> Path:
        return self.storage_root / "sessions"

    @property
    def artifacts_root(self) -> Path:
        return self.storage_root / "artifacts"

    @property
    def db_path(self) -> Path:
        return self.storage_root / "planning-studio.sqlite"

    @property
    def database_url(self) -> str:
        """SQLAlchemy/Alembic-style URL for the service database.

        If the ``DATABASE_URL`` environment variable is set it wins — the
        same string is handed to SQLAlchemy's ``create_engine``. That lets
        production point at Postgres (``postgresql+psycopg://...``) while
        dev stays on the bundled SQLite file.

        Fallback: a SQLite URL composed from :attr:`db_path`. The path is
        made absolute and posix-style so the three leading slashes in
        ``sqlite:///<abs_path>`` work on both Windows and Unix.
        """
        env_url = os.environ.get("DATABASE_URL")
        if env_url:
            return env_url
        absolute = self.db_path.resolve().as_posix()
        return f"sqlite:///{absolute}"


def load_config() -> ServiceConfig:
    root = Path(os.environ.get("PLANNING_STUDIO_STORAGE_ROOT", Path(__file__).resolve().parents[2] / "local" / "data"))
    host = os.environ.get("PLANNING_STUDIO_HOST", "127.0.0.1")
    port = int(os.environ.get("PLANNING_STUDIO_PORT", "4174"))
    grace_raw = os.environ.get("INSPIRA_DELETED_PROJECT_GRACE_DAYS", "30")
    try:
        grace_days = max(1, int(grace_raw))
    except (TypeError, ValueError):
        grace_days = 30
    return ServiceConfig(
        storage_root=root.resolve(),
        host=host,
        port=port,
        deleted_project_grace_days=grace_days,
    )
