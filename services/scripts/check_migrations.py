"""Pre-deploy drift guard for alembic migrations.

Usage:

    cd services
    python scripts/check_migrations.py

    # or from the repo root:
    python services/scripts/check_migrations.py

What it does:

    1. Connects to the database pointed at by ``DATABASE_URL`` (same var the
       app reads at runtime).
    2. Runs the equivalent of ``alembic current`` to read
       ``alembic_version.version_num`` off the live DB.
    3. Loads every migration script from ``services/alembic/versions`` and
       walks the revision graph to find the head(s).
    4. Exits 0 if the DB is at head. Exits non-zero and prints a loud
       message otherwise.

Why this exists: Fly's ``flyctl deploy --remote-only`` does NOT run
migrations. Deploying a backend that references columns the live DB
doesn't have causes HTTP 500s at first request, and — because ``fly deploy``
swaps traffic to the new machine after it health-checks — the bad machine
takes prod down. This script is meant to run as a pre-deploy step: if it
fails, the deploy never starts.

Env:

    DATABASE_URL   Required. The unpooled Neon / Postgres URL. SQLite also
                   works (``sqlite:///...``) for local smoke testing.
    ALEMBIC_CONFIG Optional. Path to alembic.ini. Defaults to
                   ``../alembic.ini`` relative to this script.

Exit codes:

    0  DB is at head (or DB has no schema yet and there are also no
       migrations — extremely unusual, but not a drift condition)
    1  DATABASE_URL missing
    2  Could not inspect the database (connection error, auth, etc.)
    3  DB is behind head — migrations need to run
    4  DB is ahead of head — code checkout is older than what was applied
       against prod (someone merged out of order; someone reverted a
       migration without downgrading first; worst case someone pointed
       at the wrong DB)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
SERVICES_DIR = SCRIPT_DIR.parent
DEFAULT_ALEMBIC_INI = SERVICES_DIR / "alembic.ini"

# Make sure ``planning_studio_service`` and ``alembic`` env.py can import
# without having to invoke via ``python -m`` — this lets the script run
# either from ``services/`` or from the repo root.
if str(SERVICES_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICES_DIR))


def _eprint(msg: str) -> None:
    """Write to stderr so stdout stays clean for pipelines that parse it."""
    print(msg, file=sys.stderr, flush=True)


def _resolve_alembic_config_path() -> Path:
    override = os.environ.get("ALEMBIC_CONFIG")
    if override:
        return Path(override).resolve()
    return DEFAULT_ALEMBIC_INI


def main() -> int:
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        _eprint(
            "check_migrations: DATABASE_URL is not set. Refusing to check "
            "a null database - set DATABASE_URL to the Neon unpooled URL.",
        )
        return 1

    alembic_ini = _resolve_alembic_config_path()
    if not alembic_ini.exists():
        _eprint(
            f"check_migrations: alembic.ini not found at {alembic_ini}. "
            "Set ALEMBIC_CONFIG to override.",
        )
        return 2

    # Imports are local so the help text and arg-parsing above work even
    # when alembic / sqlalchemy aren't installed (e.g. someone tries to
    # run this from a Node-only CI step).
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory
        from alembic.runtime.migration import MigrationContext
        from sqlalchemy import create_engine
    except ImportError as e:
        _eprint(
            f"check_migrations: required packages missing ({e}). "
            "Install via `pip install -e services/` first.",
        )
        return 2

    # Read the set of migration heads known to the checked-out code. With
    # a single linear migration chain this is always one revision; the
    # API returns a tuple to accommodate branched histories.
    cfg = Config(str(alembic_ini))
    script = ScriptDirectory.from_config(cfg)
    code_heads = tuple(script.get_heads())

    if not code_heads:
        _eprint(
            "check_migrations: no migrations found under "
            "alembic/versions. This almost never happens - abort.",
        )
        return 2

    # Normalise Heroku/Render-style scheme so create_engine accepts it.
    normalised_url = database_url
    if normalised_url.startswith("postgres://"):
        normalised_url = "postgresql://" + normalised_url[len("postgres://"):]
    if normalised_url.startswith("postgresql+psycopg2://"):
        normalised_url = (
            "postgresql+psycopg://"
            + normalised_url[len("postgresql+psycopg2://"):]
        )
    if normalised_url.startswith("postgresql://"):
        normalised_url = "postgresql+psycopg://" + normalised_url[len("postgresql://"):]

    try:
        engine = create_engine(normalised_url)
        with engine.connect() as conn:
            ctx = MigrationContext.configure(conn)
            db_heads = tuple(ctx.get_current_heads())
    except Exception as e:  # noqa: BLE001 — any failure is fatal for a check
        _eprint(f"check_migrations: could not inspect database: {e}")
        return 2

    # Normalise for comparison — order doesn't matter, set membership does.
    code_set = set(code_heads)
    db_set = set(db_heads)

    if code_set == db_set:
        print(
            f"check_migrations: OK - DB is at head "
            f"({', '.join(sorted(code_set)) or '<empty>'}).",
        )
        return 0

    # Migrations exist in the code that haven't been applied → deploy blocked.
    missing_on_db = code_set - db_set
    if missing_on_db:
        _eprint(
            "check_migrations: BLOCKED - the DB is behind the code. "
            "Run `alembic upgrade head` against the production DATABASE_URL "
            "before deploying.\n"
            f"  code heads : {sorted(code_set)}\n"
            f"  DB heads   : {sorted(db_set)}\n"
            f"  missing on DB: {sorted(missing_on_db)}",
        )
        return 3

    # DB is ahead of code — someone applied a migration whose .py file isn't
    # in this checkout. Deploying would boot a machine that cannot load its
    # own DB schema. Refuse.
    extra_on_db = db_set - code_set
    _eprint(
        "check_migrations: BLOCKED - the DB is ahead of the code. "
        "This checkout would boot against a schema it does not know about.\n"
        f"  code heads : {sorted(code_set)}\n"
        f"  DB heads   : {sorted(db_set)}\n"
        f"  extra on DB: {sorted(extra_on_db)}",
    )
    return 4


if __name__ == "__main__":
    raise SystemExit(main())
