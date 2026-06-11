#!/usr/bin/env python3
"""Reset a user's Inspira account — wipe data + flip plan_tier.

Wraps scripts/reset-account.sql. Use this when you don't have psql
installed: it reuses the project's Python venv (which already has
``psycopg`` for the backend).

Usage:
    services/.venv/bin/python scripts/reset_account.py EMAIL [DATABASE_URL]

If DATABASE_URL is omitted, the script reads it from the
``DATABASE_URL_PROD`` env var or prompts (input is hidden).

Run TWICE for the full demo prep:
  1. First run wipes everything and deletes the user's workspaces,
     so /api/auth/me returns default_workspace_id=NULL → the
     Onboarding Wizard fires on next sign-in.
  2. After the user completes onboarding (creates a fresh
     workspace), run again to stamp plan_tier='frontier' on the
     newly-created workspace.
"""
from __future__ import annotations

import getpass
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SQL_PATH = REPO_ROOT / "scripts" / "reset-account.sql"


def _split_sql_statements(sql: str) -> list[str]:
    """Split a SQL script into individual statements.

    Honors:
    - ``$$`` dollar-quoted blocks (DO/PL bodies) — semicolons inside
      don't end the statement.
    - ``--`` line comments — content from ``--`` to end-of-line is
      copied verbatim (so the SQL parser still sees them as comments)
      but ``;`` inside the comment doesn't split.
    - ``/* */`` block comments — same idea.
    - Single/double-quoted string literals — semicolons inside strings
      don't split.
    """
    out: list[str] = []
    cur: list[str] = []
    in_dollar = False
    in_line_comment = False
    in_block_comment = False
    in_single_quote = False
    in_double_quote = False
    i = 0
    while i < len(sql):
        ch = sql[i]
        nxt = sql[i:i + 2]
        if in_line_comment:
            cur.append(ch)
            if ch == "\n":
                in_line_comment = False
            i += 1
            continue
        if in_block_comment:
            cur.append(ch)
            if nxt == "*/":
                cur.append(sql[i + 1])
                in_block_comment = False
                i += 2
                continue
            i += 1
            continue
        if in_single_quote:
            cur.append(ch)
            if ch == "'":
                in_single_quote = False
            i += 1
            continue
        if in_double_quote:
            cur.append(ch)
            if ch == '"':
                in_double_quote = False
            i += 1
            continue
        if nxt == "$$":
            in_dollar = not in_dollar
            cur.append("$$")
            i += 2
            continue
        if not in_dollar:
            if nxt == "--":
                in_line_comment = True
                cur.append("--")
                i += 2
                continue
            if nxt == "/*":
                in_block_comment = True
                cur.append("/*")
                i += 2
                continue
            if ch == "'":
                in_single_quote = True
                cur.append(ch)
                i += 1
                continue
            if ch == '"':
                in_double_quote = True
                cur.append(ch)
                i += 1
                continue
        if ch == ";" and not in_dollar:
            stmt = "".join(cur).strip()
            if stmt:
                out.append(stmt)
            cur = []
        else:
            cur.append(ch)
        i += 1
    tail = "".join(cur).strip()
    if tail:
        out.append(tail)
    return out


def _resolve_database_url(argv_url: str | None) -> str:
    if argv_url:
        return argv_url
    env_url = os.environ.get("DATABASE_URL_PROD") or os.environ.get(
        "DATABASE_URL"
    )
    if env_url:
        return env_url
    print(
        "DATABASE_URL not provided as arg or env var.\n"
        "Get the *unpooled* connection string from your Neon dashboard\n"
        "(or your password manager) and paste it below — input is hidden.\n"
        "Format: postgresql://USER:PASS@HOST.neon.tech/DBNAME?sslmode=require"
    )
    url = getpass.getpass("DATABASE_URL: ").strip()
    if not url:
        print("No URL given — aborting.", file=sys.stderr)
        sys.exit(2)
    return url


def main() -> int:
    if len(sys.argv) < 2 or len(sys.argv) > 3:
        print(
            "Usage: reset_account.py EMAIL [DATABASE_URL]",
            file=sys.stderr,
        )
        return 2
    email = sys.argv[1].strip()
    if "@" not in email or email.startswith("[") or "(mailto:" in email:
        print(
            f"That doesn't look like a plain email: {email!r}. "
            "Paste it as plain text, no markdown link.",
            file=sys.stderr,
        )
        return 2
    database_url = _resolve_database_url(
        sys.argv[2] if len(sys.argv) == 3 else None
    )

    try:
        import psycopg  # type: ignore[import-not-found]
    except ImportError:
        print(
            "psycopg not importable. Run with the project venv:\n"
            "  services/.venv/bin/python scripts/reset_account.py …",
            file=sys.stderr,
        )
        return 1

    sql_template = SQL_PATH.read_text()
    # The .sql file is written for psql's ``\set`` + ``:email`` substitution.
    # psycopg doesn't run psql meta-commands, so we strip them and inline the
    # email as a literal — single-quoted, escaping inner quotes for safety.
    safe_email = email.replace("'", "''")
    sql = sql_template
    # Drop psql-only lines: \set, \echo, \-prefixed.
    sql = "\n".join(
        line for line in sql.splitlines()
        if not line.lstrip().startswith("\\")
    )
    # Replace the ``:email`` placeholders with the SQL string literal.
    # psql supports two forms: bare ``:email`` (raw substitution) and
    # ``:'email'`` (auto-SQL-quoted). Replace the auto-quoted form
    # FIRST so the bare-form replace doesn't strip the surrounding
    # quotes.
    quoted_literal = f"'{safe_email}'"
    sql = sql.replace(":'email'", quoted_literal)
    sql = sql.replace(":email", quoted_literal)

    print(f"Resetting account for {email} …")
    statements = _split_sql_statements(sql)
    with psycopg.connect(database_url, autocommit=False) as conn:
        skipped: list[tuple[str, str]] = []
        for idx, stmt in enumerate(statements):
            stmt_clean = stmt.strip()
            if not stmt_clean:
                continue
            preview = stmt_clean.split("\n", 1)[0][:80]
            # Skip psql meta-commands (\set, \echo, ...) — they're
            # psql-specific, not valid on a raw psycopg connection.
            if stmt_clean.startswith("\\"):
                continue
            # BEGIN / COMMIT live in the .sql for psql's benefit but
            # psycopg manages the outer transaction itself.
            if stmt_clean.upper().rstrip(";") in (
                "BEGIN", "COMMIT", "ROLLBACK", "START TRANSACTION",
            ):
                continue
            sp = f"s{idx}"
            schema_skip_msg: str | None = None
            with conn.cursor() as cur:
                cur.execute(f"SAVEPOINT {sp}")
                try:
                    cur.execute(stmt_clean)
                    # Surface any final-row output.
                    try:
                        rows = cur.fetchall()
                        if rows:
                            cols = [d.name for d in cur.description] if cur.description else []
                            print(f"\n--- Output of: {preview}")
                            print(" | ".join(cols))
                            for r in rows:
                                print(" | ".join(str(c) for c in r))
                    except psycopg.ProgrammingError:
                        pass
                    cur.execute(f"RELEASE SAVEPOINT {sp}")
                except (
                    psycopg.errors.UndefinedTable,
                    psycopg.errors.UndefinedColumn,
                    psycopg.errors.UndefinedObject,
                ) as exc:
                    # Schema mismatch — table/column doesn't exist on
                    # this DB. The cursor is now poisoned; release on
                    # a fresh cursor below.
                    schema_skip_msg = str(exc).split("\n", 1)[0]
            if schema_skip_msg is not None:
                # Recover the savepoint with a clean cursor.
                with conn.cursor() as recover:
                    recover.execute(f"ROLLBACK TO SAVEPOINT {sp}")
                    recover.execute(f"RELEASE SAVEPOINT {sp}")
                skipped.append((preview, schema_skip_msg))
        if skipped:
            print(f"\nSkipped {len(skipped)} statement(s) with schema mismatches:")
            for preview, msg in skipped:
                print(f"  · {preview}\n     → {msg}")
        conn.commit()
    print("✓ Reset committed.")
    print(
        "\nNext steps:\n"
        "  1. Sign out + sign back in on https://tryinspira.com\n"
        "  2. The Onboarding Wizard should fire (no default workspace).\n"
        "  3. Complete onboarding — creates a fresh workspace.\n"
        "  4. Run this script again to stamp plan_tier='frontier'."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
