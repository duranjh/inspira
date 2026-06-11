"""Alembic environment for planning-studio-service.

The schema is defined in raw SQL inside ``planning_studio_service.store`` —
this project does not use SQLAlchemy ORM models. Alembic is here only to
manage migrations (upgrade / downgrade / stamp), using ``op.execute`` and
``op.create_table`` primitives against the URL produced by
:attr:`planning_studio_service.config.ServiceConfig.database_url`.

The URL resolution order:

1. ``DATABASE_URL`` environment variable, if set. This takes precedence so
   production or CI can point at Postgres without touching alembic.ini.
2. Otherwise, a ``sqlite:///<absolute_path>`` URL composed from
   ``config.db_path`` (the bundled dev SQLite file).

``target_metadata`` is ``None`` because there are no SQLAlchemy models to
autogenerate against; every migration is hand-written SQL.
"""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool


# Ensure the services/ directory is on sys.path so `planning_studio_service`
# resolves no matter whether alembic is invoked from the repo root with
# `-c services/alembic.ini` or from inside services/ directly.
_SERVICES_DIR = Path(__file__).resolve().parents[1]
if str(_SERVICES_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVICES_DIR))

from planning_studio_service.config import load_config  # noqa: E402


config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _normalize_postgres_driver(url: str) -> str:
    """Force SQLAlchemy to use psycopg (v3), not psycopg2.

    The runtime store uses psycopg v3 directly (it's the only postgres
    driver we ship), so SQLAlchemy needs to agree — otherwise alembic
    tries to ``import psycopg2`` and errors with ``ModuleNotFoundError:
    No module named 'psycopg2'``.

    Rewrites accept every common Neon / Fly / Heroku DATABASE_URL shape:

      postgres://...              → postgresql+psycopg://...
      postgresql://...             → postgresql+psycopg://...
      postgresql+psycopg2://...    → postgresql+psycopg://...
      postgresql+psycopg://...     → unchanged
      sqlite://... / anything else → unchanged

    The ``postgres://`` → ``postgresql://`` swap in particular is needed
    because Heroku/Render-style URLs still use the old scheme that
    SQLAlchemy 2.x stopped accepting.
    """
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]
    if url.startswith("postgresql+psycopg2://"):
        url = "postgresql+psycopg://" + url[len("postgresql+psycopg2://") :]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://") :]
    return url


def _resolve_database_url() -> str:
    """Pick the URL alembic should run against.

    Priority:
    1. ``-x url=...`` passed on the alembic command line (escape hatch
       for ad-hoc runs against a one-off DB).
    2. ``DATABASE_URL`` env var (handled inside ``load_config`` /
       ``ServiceConfig.database_url`` — checked there to keep the rule
       in one place).
    3. ``config.database_url`` fallback (SQLite file under
       ``storage_root``).

    The returned URL is run through ``_normalize_postgres_driver`` so
    SQLAlchemy picks psycopg v3 regardless of whether the caller gave
    us a ``postgres://`` or ``postgresql+psycopg2://`` shape.
    """
    x_args = context.get_x_argument(as_dictionary=True)
    override = x_args.get("url")
    if override:
        return _normalize_postgres_driver(override)
    # ``load_config`` reads PLANNING_STUDIO_STORAGE_ROOT; ``database_url``
    # reads DATABASE_URL. Both env vars still apply here.
    service_config = load_config()
    return _normalize_postgres_driver(service_config.database_url)


target_metadata = None


def run_migrations_offline() -> None:
    """Run migrations without an active DB connection.

    Useful for generating SQL scripts with ``alembic upgrade --sql``.
    """
    url = _resolve_database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live DB connection."""
    url = _resolve_database_url()
    # Inject into the alembic config so engine_from_config sees it —
    # alembic.ini intentionally leaves sqlalchemy.url blank.
    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = url

    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        is_sqlite = connection.dialect.name == "sqlite"
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            render_as_batch=is_sqlite,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
