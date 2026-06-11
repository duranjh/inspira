"""Print a direct password-reset link for a given email.

Why this exists
---------------

Inspira's transactional email isn't wired yet, so the forgot-password
form can generate a reset token in the database but the link never
actually reaches the user. This script is the break-glass stopgap: an
admin runs it, it mints a fresh token directly via the store, and
prints the ready-to-use reset URL to stdout. The admin relays that URL
to the user out-of-band (Slack DM, text, whatever).

When real email lands this script stays as a useful emergency bypass;
it doesn't need to be removed.

Usage
-----

    cd services
    python scripts/reset_password_link.py --email user@example.com

The script prints the full reset URL, e.g.:

    https://<your-frontend>/reset-password?token=<hex>

Copy that URL and send it to the user. It expires in 1 hour.

Running on Fly.io
-----------------

The scripts directory is shipped inside the backend image at /app/scripts/.
To mint a reset link against the production database, SSH into the running
machine and invoke the script directly:

    fly ssh console -a inspira-backend -C "python /app/scripts/reset_password_link.py --email user@example.com"

DATABASE_URL and INSPIRA_FRONTEND_URL come from the Fly app's secrets /
environment, so no extra flags are needed in normal use. The printed URL
points at the configured frontend (http://localhost:5173 by default).

Options
-------

--email   Email address of the account to generate a reset for.
--base-url  Override the frontend base URL (default: $INSPIRA_FRONTEND_URL
            or http://localhost:5173 if unset).
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Windows PowerShell / cmd defaults to cp1252; force UTF-8 so special chars
# in the URL don't crash the output.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Ensure the services package is importable when run via `python scripts/...`.
HERE = Path(__file__).resolve().parent
SERVICES_ROOT = HERE.parent
if str(SERVICES_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICES_ROOT))

from planning_studio_service._env_bootstrap import ensure_loaded  # noqa: E402
from planning_studio_service.config import load_config  # noqa: E402
from planning_studio_service.store import PlanningStudioStore  # noqa: E402

DEFAULT_FRONTEND_BASE = "http://localhost:5173"


def main(argv: list[str] | None = None) -> int:
    ensure_loaded()

    parser = argparse.ArgumentParser(
        description="Generate a direct password-reset link for a user (email delivery bypass).",
    )
    parser.add_argument(
        "--email",
        required=True,
        help="Email address of the account to reset.",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help=(
            "Frontend base URL for the reset link "
            "(default: $INSPIRA_FRONTEND_URL or http://localhost:5173)."
        ),
    )
    args = parser.parse_args(argv)

    config = load_config()
    store = PlanningStudioStore(config=config)

    email = args.email.lower().strip()
    user = store.get_user_by_email(email)
    if user is None:
        print(f"[!] No account found for {email!r}.", file=sys.stderr)
        print(
            "    Check the email address is correct and that the user has signed up.",
            file=sys.stderr,
        )
        return 1

    if not user.get("password_hash"):
        print(
            f"[!] The account for {email!r} uses OAuth / has no password. "
            "Password-reset doesn't apply.",
            file=sys.stderr,
        )
        return 1

    raw_token = store.create_password_reset_token(user["user_id"])

    base_url = (
        args.base_url
        or os.environ.get("INSPIRA_FRONTEND_URL", "").strip()
        or DEFAULT_FRONTEND_BASE
    ).rstrip("/")

    reset_link = f"{base_url}/reset-password?token={raw_token}"

    ttl_hours = store.PASSWORD_RESET_TOKEN_TTL_SECONDS // 3600
    ttl_label = "1 hour" if ttl_hours == 1 else f"{ttl_hours} hours"

    print()
    print("=" * 70)
    print(f"Password-reset link for: {email}")
    print(f"Expires in:              {ttl_label}")
    print()
    print(f"  {reset_link}")
    print()
    print("Send this URL to the user directly (Slack, text, etc.).")
    print("It can only be used once.")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
