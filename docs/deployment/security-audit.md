# Security audit - pre-deployment

Date: 2026-04-21
Scope: full repository at commit `eba825c30793fa5778b843387ef0076bd44a6634`
Auditor: read-only pass; no code changes.

## Summary

| Severity | Count |
|---|---|
| Critical | 3 |
| High | 5 |
| Medium | 6 |
| Low / observational | 5 |
| Clean | 8 (see list) |

Biggest risk: the `user_id` parameter is accepted but silently discarded on virtually every row-level lookup in `store.py`, so once more than one user exists any authenticated caller can read, mutate, or delete any other user's topics, decisions, Q&A turns, and relationships by guessing or enumerating their IDs. Critical IDOR, must fix before public deploy.

Separate note: a real-looking OpenAI API key was found in the working tree at `.env` line 5 (value redacted from this report - user to investigate). `.env` is gitignored and was never committed to git history, so the exposure is limited to whoever has had access to the workstation or any image builds that might have copied it. See C1 below.

---

## Critical (must fix before public deploy)

### C1. Real OpenAI API key present in working tree `.env`
- File: `.env` (line 5)
- Concern: A plausible-looking `sk-proj-...` OpenAI key is stored in the local `.env` file. The key value is NOT reproduced here per audit policy - user must inspect the file directly and rotate if in use.
- Mitigating facts:
  - `.gitignore` line 21 correctly excludes `.env`, and `git log --all -p -S"sk-proj-"` returned no history matches, so the key was never committed.
  - `.dockerignore` lines 12-13 correctly exclude `.env` from build contexts, so it will not end up baked into the backend image.
- Fix: Rotate the key at https://platform.openai.com/api-keys now (even if never shared), since it has been sitting on disk in plaintext. Going forward, inject via the platform's secrets manager (Fly secrets / Railway env / AWS SSM). Do not ship a file-based secret with the image.

### C2. Authorization is broken - `user_id` ignored on most store reads/writes (IDOR)
- File: `services/planning_studio_service/store.py`, lines 655, 670, 689, 714, 775, 814, 835, 856, 885, 916, 951, 970, 1041, 1075, 1137, 1157
- Concern: Nearly every method that accepts `user_id` throws it away via `_ = user_id` and issues an unscoped query keyed only on the row's own ID (`WHERE topic_id = ?`, `WHERE decision_id = ?`, etc.). That includes `get_topic`, `list_topics`, `list_qna_turns`, `list_decisions`, `list_relationships`, `delete_topic`, `delete_decision`, `delete_relationship`, `update_topic`, `create_topic`, `create_decision`, `create_relationship`, `append_qna_turn`. The only methods that actually honor `user_id` are the user/project-level helpers (`_get_v2_project`, `update_v2_project`, `delete_v2_project`, `list_v2_projects`, `ensure_project`).
- Exploit: as soon as user B signs up, they can POST `/api/v2/topics/{user_A_topic_id}/update` or `.../delete`, GET `/api/v2/topics/{any_topic_id}/turns`, or `/api/v2/topics/{any_topic_id}/decisions` - the handlers pass `user_id=user["user_id"]` into the store, but the store ignores it. IDs are 10-hex UUID prefixes, brute-forceable over time and leak through relationships / project listings.
- Fix: every row-level lookup must JOIN or filter on the owning project's `user_id`, e.g.
  ```sql
  SELECT ... FROM topics t
  JOIN v2_projects p ON t.project_id = p.project_id
  WHERE t.topic_id = ? AND p.user_id = ?
  ```
  and every delete/update must `AND project_id IN (SELECT project_id FROM v2_projects WHERE user_id = ?)`. Cross-cutting change; the safest fix is a single `_assert_owns` helper invoked from every handler before the mutation.

### C3. Session cookie signing secret has a checked-in dev fallback
- File: `services/planning_studio_service/auth.py`, lines 42-53
- Concern: When `INSPIRA_SESSION_SECRET` is unset the code falls back to the hardcoded string `"inspira-dev-only-change-me"`. In production this would make every session cookie trivially forgeable - an attacker who knows the fallback (now public via this audit and via source) can mint a valid `inspira_session` cookie for any known or guessable `user_id` and read/mutate that user's workspace. The code emits a WARNING log and keeps running; there is no refuse-to-start guard.
- Fix: in production, raise RuntimeError on startup when `INSPIRA_SESSION_SECRET` is empty OR still equals the dev fallback. Pseudocode:
  ```python
  if not secret or secret == "inspira-dev-only-change-me":
      if os.environ.get("ENVIRONMENT", "development") == "production":
          raise RuntimeError("INSPIRA_SESSION_SECRET must be set in production")
  ```
  Generate a 32+ byte random value (`python -c 'import secrets; print(secrets.token_urlsafe(48))'`) and load via Fly/Railway secrets.

---

## High (should fix before public deploy)

### H1. CORS defaults to `allow_origins=["*"]` and is not bound by environment gate
- File: `services/planning_studio_service/api.py`, lines 171-187
- Concern: If `INSPIRA_ALLOWED_ORIGINS` env var is unset, the API accepts any origin. Combined with the fact that the frontend sends `credentials: "include"` (`app/src/features/inspira/api.ts:123`), a production deploy that forgets to set this var is wide open to any third-party site sending the user's session cookie. The code does correctly set `allow_credentials=False` when origins is `["*"]`, which means browsers will reject cookie-bearing preflight in that config - so the failure mode is the frontend breaks rather than cookies leaking. Still, this is too much footgun for a public deploy.
- Fix: refuse to start when `ENVIRONMENT=production` and `INSPIRA_ALLOWED_ORIGINS` is empty. For `tryinspira.com`, set `INSPIRA_ALLOWED_ORIGINS="https://tryinspira.com,https://www.tryinspira.com"`.

### H2. Cookie `Secure` flag defaults to false
- File: `services/planning_studio_service/auth.py`, line 136
- Concern: `secure` is read from `INSPIRA_COOKIE_SECURE` env var, defaulting to `"false"`. If the production deploy forgets to set this, the session cookie will be sent over plain HTTP if one ever leaks a mixed-content request. For a site behind HTTPS (tryinspira.com), this should default to true.
- Fix: flip the production default, OR refuse to start with `secure=False` when `ENVIRONMENT=production`. Also consider `samesite="strict"` for stronger CSRF protection (current: `"lax"`; see M1).

### H3. Raw exception strings leak to the client on planner errors
- File: `services/planning_studio_service/api.py`, lines 291, 540 (FastAPI path) and `app.py` lines 163, 516 (legacy path)
- Concern: When the OpenAI call raises, the handler returns `{"error": "planner_call_failed", "detail": str(exc)}` with `str(exc)` copied verbatim. Error strings from the OpenAI SDK can include the organization ID, request ID, and sometimes the model name plus a chunk of the request body (e.g. "your key starting with sk-abc... does not have access to...") - useful for the user, but they're also useful reconnaissance for an attacker probing which upstream provider is wired.
- Fix: log the full exception server-side (Sentry is already wired), return a generic 500 body like `{"error": "planner_call_failed", "request_id": "<uuid>"}` to the client.

### H4. Legacy `app.py` dumps full traceback to the client
- File: `services/planning_studio_service/app.py`, lines 585-593
- Concern: The stdlib `BaseHTTPServer` legacy handler catches unhandled exceptions and returns `{"error": "internal_server_error", "traceback": tb}` with the full `traceback.format_exc(limit=3)` in the body. If `python -m planning_studio_service --legacy` is ever run in production (e.g. as a fallback if uvicorn has a problem), this leaks file paths, line numbers, and local variable names from the stack.
- Fix: either rip out the legacy path before deploy, or scrub the `traceback` field from the response in a follow-up.

### H5. No `max_length` / size cap on free-text user input forwarded to the LLM
- Files:
  - `services/planning_studio_service/api.py`, `KickoffBody.user_idea` (line 60), `TopicTurnBody.user_answer` (line 80), `TopicCreateBody.title` (line 65), `TopicUpdateBody.title` (line 72)
  - `AttachedSource.excerpt` (line 56) has no cap either
- Concern: a caller can POST a 10 MB `user_idea` or a list of 100 `attached_sources` each with a 1 MB `excerpt`. The backend forwards this straight to `OpenAIPlanningInterviewer.kickoff` / `topic_turn` without truncation. OpenAI bills per input token, so this turns into a direct billing DoS against us. Also inflates the SQLite DB (q_na turn bodies have no DB-side length cap).
- Fix: add `Field(max_length=8000)` on `user_idea` / `user_answer` / `title`, `Field(max_length=20000)` on `excerpt`, and a top-level cap on number of `attached_sources` (e.g. 10). Add a pre-send byte/token estimate in the adapter as a defense in depth.

---

## Medium (fix during hardening pass)

### M1. No CSRF protection on authenticated mutations
- Files: `services/planning_studio_service/api.py` (all `/api/v2/.../delete`, `.../update`, `.../kickoff`, `.../turn` routes), `auth.py` (cookie uses `samesite="lax"`)
- Concern: Once origins are locked down (H1) and `allow_credentials=True` kicks in, any mutation route accepts a JSON POST from the frontend origin. Because `SameSite=Lax` still allows top-level POST form submissions, an attacker site could still trigger a state-changing request from a phishing victim if the endpoints accepted `application/x-www-form-urlencoded` (they currently accept JSON only, which most browsers won't allow cross-origin without a CORS preflight, but a CORS misconfig would re-open this).
- Fix: set `samesite="strict"` for the session cookie, OR add an explicit CSRF token header / double-submit cookie on state-changing routes.

### M2. `/api/health` leaks absolute filesystem paths
- File: `services/planning_studio_service/api.py` line 225-227 calling `store.health()` in `store.py` lines 642-650
- Concern: `GET /api/health` returns `storage_root`, `db_path`, `sessions_root`, `artifacts_root` as fully-qualified absolute paths. Tells an attacker which OS / user home dir / `/data` mount the service runs from, which tightens targeted exploits. The endpoint is unauthenticated by design (liveness check).
- Fix: trim the response to `{"service": "planning-studio", "status": "ok", "generated_at": "..."}`. Keep the path detail behind an admin-only route if it's ever useful.

### M3. Email enumeration via differentiated signup / login responses
- File: `services/planning_studio_service/auth.py`, lines 160 (`email_in_use` on signup conflict) and 183-185 (`invalid_credentials` on login failure)
- Concern: Signup returns 409 `email_in_use` when the address is registered; login returns 401 for wrong password AND for unknown email. Together, an attacker can enumerate whether any email has an account by attempting signup.
- Fix: in production, signup should return a generic 200 response even when the address is in use ("check your email to complete registration") and send a real confirmation email via a side channel. For v1 MVP without email verification, at minimum delay the response with `time.sleep(random.uniform(0.1, 0.3))` so timing doesn't leak.

### M4. Argon2 parameters are library defaults - no explicit tuning
- File: `services/planning_studio_service/auth.py`, lines 56-75
- Concern: `PasswordHasher()` uses argon2-cffi's defaults which are reasonable (t=3, m=64MB, p=4) but have not been benchmarked on the target deploy hardware. On a small Fly/Railway instance with 256MB RAM this could OOM under concurrent logins, OR under-tune vs. a 4GB instance.
- Fix: explicitly configure `PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)` after benchmarking target deploy tier. Document the rotation policy.

### M5. No per-user token budget on LLM-hitting routes (only per-IP rate limit)
- File: `services/planning_studio_service/api.py`, lines 190-211 (slowapi per-IP), comment at 282-283 references a non-existent per-user budget
- Concern: slowapi's `get_remote_address` rate-limits at 120/min per IP. That does not prevent: (a) a single authenticated user burning our OpenAI budget by looping the topic_turn endpoint all day, (b) an attacker rotating through free proxies to evade per-IP limits. The comment at line 282-283 ("Per-user token-budget gate happens inside the adapter call") is aspirational - no such gate exists in `openai_adapter.py`.
- Fix: track daily token usage per `user_id` in a `user_usage` table, decrement the budget in a transaction around each `adapter.kickoff` / `adapter.topic_turn` call, and return 429 when exceeded. Seed with a modest free-tier default (e.g. 100k tokens/day).

### M6. `ensure_project` silently upgrades a system-user-owned project to the first caller
- File: `services/planning_studio_service/store.py`, lines 493-527
- Concern: The code at line 503 says "Already owned by another user - refuse to silently re-assign" but the condition is `existing["user_id"] != user_id and existing["user_id"] != "user-system"`. So if user A hits a kickoff for a project currently owned by `user-system` (legacy seed), user A claims it. In the transition phase that's intentional - but once public, the bootstrap system-user row is still present, and any caller who can guess the seed `project_id` string can adopt it.
- Fix: either remove the system-user escape hatch once multi-user goes live, or restrict it to a known hardcoded list of seed IDs.

---

## Low / observational

### L1. Dev-only `dotenv` bootstrap is already guarded
- File: `services/planning_studio_service/_env_bootstrap.py`, lines 1-47
- Notes: walks up from CWD to find nearest `.env`, uses `override=False` so explicit shell env vars win. Does NOT log any loaded values. Clean.

### L2. `credentials: "include"` sent on every frontend fetch
- File: `app/src/features/inspira/api.ts` line 123
- Notes: correct for the session-cookie auth model. Paired with the CORS fix in H1, this is fine. If origins get mis-configured this becomes the attack surface for cookie theft.

### L3. `GOOGLE_OAUTH_*` scaffold is present but unimplemented
- File: `services/planning_studio_service/auth.py`, lines 277-296
- Notes: returns 501 when unconfigured. Safe default. Remember to revisit when actually wiring OAuth - don't copy-paste a callback that skips the state/nonce check.

### L4. Sentry is gated by `SENTRY_DSN` env var and sends `send_default_pii=False`
- File: `services/planning_studio_service/api.py`, lines 103-127
- Notes: good. Make sure the DSN env var is only set in environments where you want error capture.

### L5. DEPRECATED v1 legacy path still seeds public data at startup
- File: `services/planning_studio_service/store.py`, lines 332-386 (`_seed_defaults`)
- Notes: every fresh database gets a hardcoded `project-second-brain-commercialization` project, `session-bootstrap`, and `artifact-prd-outline`. Harmless but visible to the system user on `/api/projects`. Consider removing for the public launch so the first-run experience starts empty.

---

## Clean

Items reviewed that look correct:

- SQL injection: every query in `store.py` uses `?` placeholders. The two f-string `UPDATE` queries (`store.py:582` for v2_projects and `store.py:849` for topics) build a `set_clause` from an allow-list of column names only - no user input reaches the SQL string. Safe.
- Password hashing: argon2 via `argon2-cffi`, no plaintext, no MD5/SHA1 shortcuts. `_hash_password` on signup, `_verify_password` on login (`auth.py`:56-75).
- Cookie `httpOnly` flag: set to `True` on the session cookie (`auth.py`:141).
- Cookie `path` scope: `"/"`, fine.
- Session tampering: `itsdangerous.URLSafeTimedSerializer` catches both `BadSignature` and `SignatureExpired` and falls through to the system user (`auth.py`:256-262).
- `.env` gitignore: `.gitignore` line 21 covers it; git history confirms no leak (`git log --all -p -S"sk-proj-"` empty).
- `.dockerignore` excludes `.env` (line 12-13), so secrets do not enter the image build context.
- Frontend bundle: only `VITE_INSPIRA_API_URL` is exposed via `import.meta.env`. No backend secret is pulled into the client bundle. `nginx.conf` sets `X-Content-Type-Options`, `X-Frame-Options: DENY`, `Referrer-Policy`.
- Pydantic request bodies on all v2 endpoints: yes. Typed pydantic models enforce basic shape/types (`api.py`:46-96).

---

## Recommendations for deploy

### Required env vars (refuse to start if missing in prod)

| Var | Value | Why |
|---|---|---|
| `ENVIRONMENT` | `production` | gates the checks below |
| `INSPIRA_SESSION_SECRET` | 48+ bytes of `secrets.token_urlsafe()` output | fixes C3 |
| `INSPIRA_COOKIE_SECURE` | `true` | fixes H2 |
| `INSPIRA_ALLOWED_ORIGINS` | `https://tryinspira.com,https://www.tryinspira.com` | fixes H1 |
| `OPENAI_API_KEY` | freshly rotated key (after C1) | don't reuse the existing one |
| `SENTRY_DSN` | project DSN | centralize error capture (H3 depends on this for real diagnosis) |
| `DATABASE_URL` | `postgresql+psycopg://...` | if migrating off SQLite; otherwise leave unset |
| `INSPIRA_RATE_LIMIT` | e.g. `60/minute` | tighter than the 120/min default |

### Startup assertions to add

Add a `_require_production_safe_env` check that runs at app startup and raises on:
- `INSPIRA_SESSION_SECRET` empty or equal to `"inspira-dev-only-change-me"`
- `INSPIRA_ALLOWED_ORIGINS` empty when `ENVIRONMENT=production`
- `INSPIRA_COOKIE_SECURE != "true"` when `ENVIRONMENT=production`
- `OPENAI_API_KEY` empty when running under uvicorn (not tests)

### Secrets management

- Fly.io: `fly secrets set INSPIRA_SESSION_SECRET=... OPENAI_API_KEY=... SENTRY_DSN=...` - these become env vars at runtime, never written to disk in the image.
- Railway: same story via the project dashboard "Variables" tab.
- AWS: SSM Parameter Store with `SecureString` and IAM role on the task.
- Never bake secrets into the Docker image. `.dockerignore` already blocks `.env`, so the current image is safe from that leak. Do NOT add an `ENV OPENAI_API_KEY=...` line to the Dockerfile.

### CORS origins for tryinspira.com

```
INSPIRA_ALLOWED_ORIGINS=https://tryinspira.com,https://www.tryinspira.com
```
If you host the API on a subdomain like `api.tryinspira.com`, make sure the frontend's `VITE_INSPIRA_API_URL` points at `https://api.tryinspira.com` in the production build, and the cookie's `Domain` stays unset (defaults to the API host, which is what you want with credentialed CORS from the web origin).

### Post-deploy verification checklist

1. `curl https://api.tryinspira.com/api/health` returns trimmed output, not file paths (M2).
2. `curl -H "Origin: https://evil.example" https://api.tryinspira.com/api/auth/me` returns no `Access-Control-Allow-Origin` header (H1).
3. `curl -i https://api.tryinspira.com/api/auth/login ...` sets cookie with `Secure; HttpOnly; SameSite=Lax` (H2).
4. Attempting to GET `/api/v2/topics/{some_other_user_topic_id}/turns` returns 404, not 200 (C2 - must be fixed).
5. Sending a 10MB `user_idea` returns 422 validation error, not a 500 (H5).
