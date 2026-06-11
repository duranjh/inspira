# Auth Flow

How users sign in, how session cookies carry identity, and how every
row-level operation ends up scoped to the signed-in user.

## Provider status

Active:
- **Password auth** with argon2 hashing
  (`services/planning_studio_service/auth.py:82` `_hash_password`,
  `auth.py:86` `_verify_password`).

Scaffolded, not wired:
- **Google OAuth** — routes exist and return `501 google_oauth_unimplemented`
  when `GOOGLE_OAUTH_CLIENT_ID` is unset
  (`services/planning_studio_service/auth.py:331` `google_start_route`,
  `auth.py:348` `google_callback_route`). Listed here as **planned**.

## Session model

Sessions are carried by a signed cookie, not a server-side session table.

- Cookie name: `inspira_session` (`auth.py:36`).
- Signing: `itsdangerous.URLSafeTimedSerializer` with salt
  `"inspira-session"` (`auth.py:42` `_session_serializer`).
- Signing key: `INSPIRA_SESSION_SECRET` environment variable. When unset,
  a hardcoded dev fallback `"inspira-dev-only-change-me"` is used and a
  WARNING is logged. Production start-up refuses to boot with the
  fallback (`api.py:212-216`).
- Cookie payload: `{"user_id": "<user-id>", "iat": <issue-epoch>}`
  (`auth.py:151` `_set_session_cookie`).
- Max age: 30 days (`auth.py:39` `SESSION_MAX_AGE_SECONDS`).
- Cookie attributes:
  - `HttpOnly: true` — no JavaScript access
  - `SameSite: Lax` — top-level navigations still carry it
  - `Secure: $INSPIRA_COOKIE_SECURE` (`"true"` in prod, `"false"` in dev;
    production start-up refuses to boot when `Secure=false` via
    `api.py:199` `_assert_production_safe`).
  - `Path: /`.

The cookie renews on each authenticated request indirectly — a user who
logs in with a 30-day max_age and then stays active keeps their cookie
live because the browser never lets it expire. There is no sliding
renewal on the server side.

## Cookie lifecycle

### Sign-up

Route: `POST /api/auth/signup` (`auth.py:174` `signup_route`).

1. Validate email with `EmailStr` (pydantic) and password with
   `min_length=8, max_length=256` (`auth.py:104` `SignupBody`).
2. `store.get_user_by_email` — reject duplicates with 409
   `{"error": "email_in_use"}`.
3. `_hash_password` with argon2 (`auth.py:82`).
4. `store.create_user` (`store.py:408`) inserts a new row with a
   `user-<12hex>` id.
5. `_set_session_cookie(response, user_id)` (`auth.py:151`) sets the
   freshly-signed cookie on the 201 response.
6. Response body shape `AuthedUser` (defined near `auth.py:115`).

### Login

Route: `POST /api/auth/login` (`auth.py:212` `login_route`).

1. Lookup by email; reject with 401 `{"error": "invalid_credentials"}`
   when the user is missing or has `password_hash = NULL` (OAuth-only or
   system user).
2. `argon2.PasswordHasher().verify` — fail paths collapse to the same
   401 so the attacker cannot distinguish "wrong email" from "wrong
   password" (`auth.py:86`).
3. `_set_session_cookie` on success. Same 200 body shape as signup.

### Current user

Route: `GET /api/auth/me` (`auth.py:255` `me_route`). Reads the
`inspira_session` cookie and returns the resolved user, falling back to
the system user (see below).

### Logout

Route: `POST /api/auth/logout` (`auth.py:249` `logout_route`).
`_clear_session_cookie` removes the cookie by re-setting it with
`Max-Age=0` (`auth.py:169`).

## Cookie -> user resolution

The `current_user` dependency (`auth.py:288` `current_user_dependency`)
is wired into every protected route via `Depends(_current_user)`
(`api.py:331`). It follows a strict fall-through policy, captured in
`_resolve_user` (`auth.py:300`):

```
if not session_cookie:
    return _ensure_system_user()         # no cookie -> system user
try:
    payload = serializer.loads(cookie, max_age=SESSION_MAX_AGE_SECONDS)
except SignatureExpired | BadSignature | Exception:
    return _ensure_system_user()         # tampered or expired -> system user
user_id = payload["user_id"]
if not user_id:
    return _ensure_system_user()
user = store.get_user_by_id(user_id)
if user is None:
    return _ensure_system_user()         # user was deleted -> system user
return user
```

The fallback is the `user-system` sentinel (`auth.py:99`). It exists so
the legacy single-tenant UI still works end-to-end when no one has
signed in yet. Everything the system user creates stays scoped to that
sentinel id; once a real user signs in, their rows scope to their own
`user_id`.

**Security note:** the system-user fallback means missing/invalid
cookies never produce `401`. A route that requires a **real** user must
check `user["user_id"] != SYSTEM_USER_ID` explicitly. The current
codebase does not enforce this anywhere — the system user has full
access to the single seeded v1 project plus anything else it creates.

## Tenancy enforcement: `user_id` scoping

Every row belonging to a real user is scoped via the parent project's
`user_id` column. The gate is layered:

### Layer 1: HTTP ownership dependencies (api.py)

Defined inside `create_app` so they close over `_store`
(`api.py:358-382`). Each helper returns the resolved row or raises a
`404` (never a `403` — do not leak object-existence hints).

```python
def _require_owned_project(project_id: str, user: dict) -> None:
    if not _store.verify_project_ownership(
        project_id=project_id, user_id=user["user_id"],
    ):
        raise HTTPException(status_code=404, detail={"error": "project_not_found"})

def _require_owned_topic(topic_id: str, user: dict) -> dict:
    topic = _store.get_topic_with_ownership(topic_id, user_id=user["user_id"])
    if topic is None:
        raise HTTPException(status_code=404, detail={"error": "topic_not_found"})
    return topic

# ...and _require_owned_decision, _require_owned_relationship
```

Every v2 route that reads or mutates a single entity calls one of these
helpers first. See the route handlers at:

- `v2_list_topics` (`api.py:610`): `_require_owned_project`.
- `v2_update_topic` / `v2_delete_topic` (`api.py:641, 652`):
  `_require_owned_topic`.
- `v2_create_decision` (`api.py:672`): `_require_owned_topic`.
- `v2_delete_decision` (`api.py:701`): `_require_owned_decision`.
- `v2_create_relationship` (`api.py:725`): `_require_owned_project` +
  `_require_owned_topic` for both endpoints.
- `v2_delete_relationship` (`api.py:750`): `_require_owned_relationship`.
- `v2_topic_turn` (`api.py:765`): `_require_owned_topic`.

Kickoff (`api.py:528`) is slightly different — it calls
`_store.ensure_project(project_id=..., user_id=...)` which rejects on
ownership mismatch by raising `PermissionError`, translated to `404`.

### Layer 2: Store ownership helpers (store.py)

These are the ground-truth checks. The HTTP layer delegates to them.

- `verify_project_ownership(project_id, user_id)` (`store.py:541`) —
  returns `True` iff `v2_projects.user_id == user_id` AND the project is
  not soft-deleted. Accepting `user_id=None` returns `True`
  unconditionally; the HTTP layer never passes `None`.
- `get_topic_with_ownership(topic_id, user_id)` (`store.py:557`) —
  fetches the topic and re-checks ownership of the parent project.
- `get_decision_with_ownership(decision_id, user_id)` (`store.py:570`).
- `get_relationship_with_ownership(relationship_id, user_id)`
  (`store.py:594`).

### Listings

Listings scope via a WHERE on the parent project:

- `list_v2_projects(user_id=...)` (`store.py:606`) filters
  `WHERE user_id = ? AND deleted_at IS NULL`.
- `list_topics`, `list_relationships`, `list_decisions`,
  `list_qna_turns` scope on `project_id` or `topic_id`. The HTTP layer
  calls `_require_owned_project` / `_require_owned_topic` first, so by
  the time the list call happens we already know the caller owns the
  parent.

### Known caveat — direct store calls bypass the gate

The underlying CRUD helpers (`create_topic`, `update_topic`,
`delete_topic`, `append_qna_turn`, `create_decision`, etc.) accept
`user_id` as a kwarg but intentionally ignore it via `_ = user_id` lines
(see `store.py:840`, `879`, etc.). This is by design — the HTTP layer
pre-checks ownership and the store just does the SQL. If you write new
code that calls store methods directly outside the HTTP layer, you MUST
call `verify_project_ownership` yourself first.

Tests that verify the gate live at
`services/tests/test_ownership.py` — two users, one shared app, every
cross-user probe returns `404`.

## Rate limits and token budgets

Two additional layers sit on top of the ownership gate on any route
that calls the LLM:

- **Per-IP rate limit** (`api.py:309`): slowapi Limiter keyed on
  `get_remote_address`. Default `120/minute`, overridable via
  `INSPIRA_RATE_LIMIT`.
- **Per-user daily token budget** (`api.py:408`
  `_require_token_budget`): reads today's `user_usage` row, denies with
  429 + `Retry-After` when `tokens_in + tokens_out >=
  INSPIRA_USER_DAILY_TOKEN_BUDGET` (default 200,000). The budget is
  bypassed for `user_id` values where the gate is disabled
  (non-positive budget value) and for the cached suggestions path.

## Production start-up guardrails

`_assert_production_safe` (`api.py:199`) refuses to boot in production
when any of the following is missing or still on the dev default:

- `INSPIRA_SESSION_SECRET` — unset or literally `"inspira-dev-only-change-me"`.
- `INSPIRA_ALLOWED_ORIGINS` — unset.
- `INSPIRA_COOKIE_SECURE` — not `"true"`.
- `OPENAI_API_KEY` — unset.

A loud failure at start-up is safer than a quiet compromise. Set
`ENVIRONMENT=production` to activate the guard. See `docs/deploy/env-vars.md`.

## Testing

- `services/tests/test_auth_routes.py` — signup, login, logout, /me,
  cookie persistence, and tamper-resistance.
- `services/tests/test_ownership.py` — full IDOR suite for two users
  against one app.

Both suites construct a fresh app per test via `make_test_app` at
`services/tests/_helpers.py:48`, so the session secret, CORS config, and
seeded system user are isolated between tests.
