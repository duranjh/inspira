# Alembic migrations

Schema migrations for the planning-studio-service (Inspira) backend.

## What alembic owns

The database schema is currently duplicated:

- `planning_studio_service/store.py` creates every table on service boot
  (`_initialize`, `_initialize_v2_schema`, `_initialize_users_schema`,
  `_initialize_v2_projects_schema`, `_ensure_user_id_columns`). This stays
  as-is for now so existing deployments keep working.
- `services/alembic/versions/` — new authoritative record. The baseline
  migration (`20260421_0001_baseline.py`) captures the current schema with
  every `CREATE TABLE` wrapped in `IF NOT EXISTS`, so running it against a
  live store.py-bootstrapped DB is a safe no-op.

Going forward: every schema change is a new alembic revision. Do not edit
`_initialize*` in store.py for new tables or columns.

## Database URL

The URL is resolved at runtime by `alembic/env.py`:

1. `-x url=...` on the alembic command line (ad-hoc override).
2. `DATABASE_URL` environment variable (production / CI).
3. Fallback: `sqlite:///<storage_root>/planning-studio.sqlite` composed
   from `ServiceConfig.db_path`.

`alembic.ini` intentionally leaves `sqlalchemy.url` blank so the URL
logic lives in one place.

## Running

From the `services/` directory:

```
alembic upgrade head
```

From the repository root (useful in Docker / CI):

```
alembic -c services/alembic.ini upgrade head
```

Pass `PYTHONPATH=services` if you invoke from the repo root and
`planning_studio_service` is not already installed in the active env.

## Creating a new migration

```
alembic revision -m "add foo column to bar"
```

Then edit the generated file in `services/alembic/versions/`. Because this
project uses raw SQL (no SQLAlchemy ORM models), write migrations with
`op.execute(sa.text("..."))` or `op.create_table(...)`. Autogenerate is
disabled — `target_metadata = None` in `env.py`.

## Pointing at Postgres

```
export DATABASE_URL=postgresql+psycopg://user:pass@host:5432/inspira
alembic -c services/alembic.ini upgrade head
```

Postgres support is the reason `psycopg[binary]` is pinned in
`pyproject.toml`. SQLite stays the dev default.

## Stamping an existing database

If a deployment already ran store.py's `_initialize*` and you want
alembic to track it without re-applying the baseline:

```
alembic -c services/alembic.ini stamp head
```

The baseline migration is idempotent (`IF NOT EXISTS` everywhere), so a
plain `upgrade head` is also safe — `stamp` is only faster.
