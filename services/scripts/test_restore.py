"""Smoke test: verify a Postgres backup can be restored end-to-end.

Usage:

    cd services
    python scripts/test_restore.py [--dump path/to/inspira-YYYYMMDD-HHMMSS.dump.gz]

What it does (in order):

    1. Locates a backup artifact. If ``--dump`` is omitted, looks for the
       newest ``inspira-*.dump`` or ``inspira-*.dump.gz`` under
       ``services/backups/``.
    2. Creates a throwaway scratch SQLite database under a temp directory.
       SQLite is used (not Postgres) because this is a structural smoke
       test that runs anywhere — CI, laptops, no Docker — and the goal is
       to prove the alembic chain still applies cleanly. For a *fidelity*
       restore test that loads the actual pg_dump bytes, see the optional
       ``--postgres-url`` mode below.
    3. Runs ``alembic upgrade head`` against the scratch database so the
       migration retrofits exercise the same code path a fresh restore
       would hit when the dump is loaded into a clean target.
    4. Issues a handful of read-only sanity queries against well-known
       tables that every deploy must have. If any query errors, the test
       fails loudly with the offending statement.
    5. (Optional) If ``--postgres-url`` is provided, the script ALSO
       performs a real ``pg_restore`` of the dump into that database and
       re-runs the sample queries against it. Use this against a scratch
       Neon branch in CI for full-fidelity verification.

Exit codes:

    0  All smoke checks passed.
    1  No backup file found / argument problem.
    2  Alembic upgrade failed.
    3  One of the sample queries failed.
    4  Optional pg_restore step failed.

The script intentionally does NOT touch any prod or staging database. The
scratch SQLite file lives in a tempdir and is deleted on exit.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional


SCRIPT_DIR = Path(__file__).resolve().parent
SERVICES_DIR = SCRIPT_DIR.parent
DEFAULT_BACKUP_DIR = SERVICES_DIR / "backups"
DEFAULT_ALEMBIC_INI = SERVICES_DIR / "alembic.ini"

# Make sure planning_studio_service / alembic env.py imports resolve when run
# from either ``services/`` or the repo root.
if str(SERVICES_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICES_DIR))


# Sample queries: each must succeed against a freshly migrated schema.
# These intentionally reference tables created by different migration
# revisions so we exercise the whole chain, not just the baseline.
SAMPLE_QUERIES: tuple[tuple[str, str], ...] = (
    ("baseline.users",            "SELECT COUNT(*) FROM users"),
    ("baseline.v2_projects",      "SELECT COUNT(*) FROM v2_projects"),
    ("baseline.topics",           "SELECT COUNT(*) FROM topics"),
    ("0002.shelves",              "SELECT COUNT(*) FROM shelves"),
    ("0003.project_share_tokens", "SELECT COUNT(*) FROM project_share_tokens"),
    ("0003.shared_links",         "SELECT COUNT(*) FROM shared_links"),
    ("alembic_version",           "SELECT version_num FROM alembic_version"),
)


def _eprint(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _find_latest_backup(backup_dir: Path) -> Optional[Path]:
    """Return the most recently modified inspira-*.dump[.gz] in backup_dir."""
    if not backup_dir.is_dir():
        return None
    candidates = list(backup_dir.glob("inspira-*.dump")) + list(
        backup_dir.glob("inspira-*.dump.gz")
    )
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


def _run_alembic_upgrade(database_url: str, alembic_ini: Path) -> bool:
    """Run `alembic upgrade head` against DATABASE_URL. Returns True on success."""
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    cmd = ["alembic", "-c", str(alembic_ini), "upgrade", "head"]
    print(f"test_restore: running {' '.join(cmd)} against {database_url}")
    proc = subprocess.run(cmd, env=env, cwd=str(SERVICES_DIR))
    return proc.returncode == 0


def _run_sample_queries(database_url: str) -> bool:
    """Issue every SAMPLE_QUERY against database_url. Returns True if all pass."""
    try:
        from sqlalchemy import create_engine, text
    except ImportError as e:
        _eprint(f"test_restore: sqlalchemy missing ({e}); install services package")
        return False

    engine = create_engine(database_url)
    failed: list[tuple[str, str, str]] = []
    with engine.connect() as conn:
        for label, query in SAMPLE_QUERIES:
            try:
                result = conn.execute(text(query)).first()
                print(f"test_restore: ok   [{label}] {query} -> {result}")
            except Exception as e:  # noqa: BLE001 — any failure is a test failure
                print(f"test_restore: FAIL [{label}] {query} -> {e}")
                failed.append((label, query, str(e)))
    return not failed


def _smoke_test_sqlite(alembic_ini: Path) -> int:
    """Build a clean SQLite, migrate it, run sample queries. Returns exit code."""
    tmp_dir = Path(tempfile.mkdtemp(prefix="inspira-restore-test-"))
    try:
        sqlite_path = tmp_dir / "scratch.db"
        sqlite_url = f"sqlite:///{sqlite_path.as_posix()}"

        if not _run_alembic_upgrade(sqlite_url, alembic_ini):
            _eprint("test_restore: alembic upgrade failed against scratch SQLite")
            return 2

        if not _run_sample_queries(sqlite_url):
            _eprint("test_restore: one or more sample queries failed")
            return 3

        print("test_restore: SQLite smoke test PASSED")
        return 0
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _fidelity_test_postgres(
    backup_path: Path, postgres_url: str, alembic_ini: Path
) -> int:
    """Optional: pg_restore the dump into postgres_url and rerun the queries."""
    if shutil.which("pg_restore") is None:
        _eprint("test_restore: pg_restore not on PATH; skipping fidelity test")
        return 4

    tmp_dump: Optional[Path] = None
    try:
        # Decompress to a temp file if needed — pg_restore can't read .gz directly.
        if backup_path.suffix == ".gz":
            tmp_dir = Path(tempfile.mkdtemp(prefix="inspira-restore-dump-"))
            tmp_dump = tmp_dir / backup_path.stem  # strips .gz
            print(f"test_restore: decompressing {backup_path} -> {tmp_dump}")
            with open(backup_path, "rb") as src, open(tmp_dump, "wb") as dst:
                # Stream through gzip so we don't slurp huge dumps into RAM.
                import gzip
                with gzip.open(src, "rb") as gz:
                    shutil.copyfileobj(gz, dst)
            dump_to_load = tmp_dump
        else:
            dump_to_load = backup_path

        cmd = [
            "pg_restore",
            "--clean", "--if-exists", "--no-owner", "--no-acl",
            "--dbname", postgres_url,
            str(dump_to_load),
        ]
        print(f"test_restore: running pg_restore against {postgres_url}")
        proc = subprocess.run(cmd)
        if proc.returncode != 0:
            _eprint("test_restore: pg_restore failed")
            return 4

        if not _run_sample_queries(postgres_url):
            _eprint("test_restore: sample queries failed against restored Postgres")
            return 3

        print("test_restore: Postgres fidelity test PASSED")
        return 0
    finally:
        if tmp_dump is not None and tmp_dump.exists():
            shutil.rmtree(tmp_dump.parent, ignore_errors=True)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--dump",
        type=Path,
        default=None,
        help="Path to a .dump or .dump.gz from scripts/backup.sh. "
             "If omitted, picks the newest under services/backups/.",
    )
    parser.add_argument(
        "--alembic-ini",
        type=Path,
        default=DEFAULT_ALEMBIC_INI,
        help="Path to alembic.ini (default: services/alembic.ini).",
    )
    parser.add_argument(
        "--postgres-url",
        type=str,
        default=os.environ.get("TEST_RESTORE_POSTGRES_URL", "").strip() or None,
        help="Optional scratch Postgres URL. If provided, the dump is "
             "actually pg_restored into it (full-fidelity test). NEVER "
             "point this at prod or staging.",
    )
    parser.add_argument(
        "--skip-sqlite",
        action="store_true",
        help="Skip the SQLite structural smoke test (only useful when "
             "--postgres-url is set).",
    )
    args = parser.parse_args(argv)

    alembic_ini: Path = args.alembic_ini
    if not alembic_ini.is_file():
        _eprint(f"test_restore: alembic.ini not found at {alembic_ini}")
        return 1

    # Locate a backup artifact. Required for the fidelity test, optional
    # for the SQLite smoke test (the latter only needs the migration tree).
    backup_path: Optional[Path] = args.dump
    if backup_path is None:
        backup_path = _find_latest_backup(DEFAULT_BACKUP_DIR)

    if backup_path is not None:
        if not backup_path.is_file():
            _eprint(f"test_restore: backup file not readable: {backup_path}")
            return 1
        print(f"test_restore: using backup artifact {backup_path}")
    else:
        print(
            "test_restore: no backup artifact found under "
            f"{DEFAULT_BACKUP_DIR} - SQLite smoke test only "
            "(no fidelity restore step)."
        )

    overall = 0

    if not args.skip_sqlite:
        rc = _smoke_test_sqlite(alembic_ini)
        if rc != 0:
            return rc
    else:
        print("test_restore: --skip-sqlite set; skipping SQLite smoke test")

    if args.postgres_url:
        if backup_path is None:
            _eprint(
                "test_restore: --postgres-url given but no dump artifact "
                "located; pass --dump explicitly or place one in "
                f"{DEFAULT_BACKUP_DIR}."
            )
            return 1
        rc = _fidelity_test_postgres(backup_path, args.postgres_url, alembic_ini)
        if rc != 0:
            return rc
    else:
        print(
            "test_restore: TEST_RESTORE_POSTGRES_URL not set; skipping "
            "pg_restore fidelity test (run against a scratch Neon branch in CI)"
        )

    print("test_restore: ALL CHECKS PASSED")
    return overall


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
