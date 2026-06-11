"""Create (or reset) a test admin account on the local Inspira database.

Why this exists
---------------

The anonymous-user kickoff gate (see InspiraApp's auth_gate phase) stops
you from poking around the app without signing in. For day-to-day dev
and smoke-testing we want a pre-provisioned account with a known
password so you can just sign in and go.

What it does
------------

1. Creates a user ``admin@inspira.local`` (configurable) with a known
   password. If the user already exists, resets the password and keeps
   the same user_id so prior projects stick around.
2. Upserts a ``team`` subscription for that user so the credit meter
   seeds at 2000 credits instead of 50.
3. Seeds the initial credit grant (idempotent — safe to re-run).

Usage
-----

    cd services
    python scripts/create_admin_user.py

Override the email / password / display name:

    python scripts/create_admin_user.py --email you@test.local --password 'somepw12' --name 'You'

The script prints the final credentials to stdout so you can copy them
into the sign-in form. DO NOT run this against a production database —
it'll happily stomp a real admin account's password.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Windows PowerShell / cmd defaults to cp1252; force UTF-8 so the arrow
# in the final banner doesn't crash the script.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Ensure the services package is importable when run via `python scripts/...`.
HERE = Path(__file__).resolve().parent
SERVICES_ROOT = HERE.parent
if str(SERVICES_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICES_ROOT))

from planning_studio_service._env_bootstrap import ensure_loaded  # noqa: E402
from planning_studio_service.auth import _hash_password  # noqa: E402
from planning_studio_service.config import load_config  # noqa: E402
from planning_studio_service.credits import ensure_initial_grant  # noqa: E402
from planning_studio_service.store import PlanningStudioStore  # noqa: E402


# Pydantic v2's EmailStr rejects `.local`, `.test`, `.example` and other
# IANA special-use TLDs, so login would 422 even with a correct password.
# `.app` is a real Google-operated TLD that passes strict validation.
DEFAULT_EMAIL = "admin@inspira.app"
DEFAULT_PASSWORD = "inspira-admin"  # 13 chars, clears the 8-char min
DEFAULT_NAME = "Admin"
DEFAULT_PLAN = "team"  # 2000-credit seed


def main(argv: list[str] | None = None) -> int:
    ensure_loaded()

    parser = argparse.ArgumentParser(
        description="Create or reset a test admin account on the local Inspira database.",
    )
    parser.add_argument("--email", default=DEFAULT_EMAIL, help="Admin email (default: %(default)s)")
    parser.add_argument(
        "--password",
        default=DEFAULT_PASSWORD,
        help="Plaintext password. Stored as argon2 hash. (default: %(default)s)",
    )
    parser.add_argument("--name", default=DEFAULT_NAME, help="Display name (default: %(default)s)")
    parser.add_argument(
        "--plan",
        default=DEFAULT_PLAN,
        choices=["free", "pro", "team"],
        help="Credit tier to seed (default: %(default)s, grants 2000 credits)",
    )
    args = parser.parse_args(argv)

    config = load_config()
    print(f"Using database: {config.database_url}")

    store = PlanningStudioStore(config=config)

    email = args.email.lower().strip()
    password_hash = _hash_password(args.password)

    # Create or reset. We check by email first so re-running the script
    # is idempotent; if the user already exists we flip their password
    # via the store's update helper.
    existing = store.get_user_by_email(email)
    if existing is None:
        user = store.create_user(
            email=email,
            password_hash=password_hash,
            display_name=args.name,
        )
        user_id = user["user_id"]
        print(f"[+] Created user {email} with id {user_id}")
    else:
        user_id = existing["user_id"]
        store.update_user_password(user_id, password_hash)
        # Display-name update isn't exposed on the store as a single-column
        # helper; skipping it on re-runs is fine — the user set it once on
        # first creation and can change it in Account Settings after that.
        print(f"[~] User {email} already exists; reset password.")

    # Upsert subscription so the credits module seeds at the requested tier.
    store.upsert_subscription(user_id=user_id, plan=args.plan, status="active")
    print(f"[+] Subscription set to '{args.plan}'")

    balance = ensure_initial_grant(store, user_id=user_id)
    print(f"[+] Credits balance: {balance}")

    print()
    print("=" * 60)
    print("Sign in with:")
    print(f"  Email:    {email}")
    print(f"  Password: {args.password}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
