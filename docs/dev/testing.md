# Testing

Guide to the existing test suite, how to run it, how to add tests, and
what conventions each suite follows.

## Where tests live

```
services/tests/
  _helpers.py                 # shared fixtures: make_test_app, signup_and_login, fake_*_response
  test_api_fastapi.py         # FastAPI route tests via TestClient
  test_auth_routes.py         # signup, login, /me, cookie behavior
  test_ownership.py           # cross-user IDOR prevention (audit C2)
  test_openai_adapter.py      # adapter unit tests + live integration (gated)
  test_service.py             # legacy store tests (v1 CRUD)
  test_v2_http.py             # legacy v2 BaseHTTPServer tests (kept alongside FastAPI)
```

No frontend test suite today. The CI gate for the frontend is
`tsc --noEmit` + `npm run build` (`.github/workflows/ci.yml`).

## Running tests

### Backend — full suite

```powershell
# PowerShell
Set-Location .\services
python -m unittest discover -s tests -v
```

```bash
# bash
cd services && python -m unittest discover -s tests -v
```

CI uses the same command. The adapter's live integration tests are
skipped unless `OPENAI_API_KEY` is set (see below).

### Backend — single test file

```powershell
python -m unittest tests.test_api_fastapi -v
```

### Backend — single test class or method

```powershell
python -m unittest tests.test_api_fastapi.V2ProjectsTests -v
python -m unittest tests.test_api_fastapi.V2ProjectsTests.test_fresh_user_has_no_projects -v
```

### Frontend

```powershell
Set-Location .\app
npx tsc --noEmit
npm run build
```

The build runs Vite's transform pipeline end-to-end; any type or import
error from any module surfaces here.

## Testing conventions

### Python

- **Framework**: stdlib `unittest` (no pytest, despite the `pytest`
  dev dep being pinned — it was added in anticipation and is not
  currently used). `setUp` / `tearDown` patterns are the norm; no
  fixtures via pytest decorators.
- **Test file names**: `test_*.py` — `unittest.discover` globs them.
- **Test class names**: group related tests in a class named after the
  system under test (`AuthMeTests`, `V2ProjectsTests`,
  `CrossUserOwnershipTests`). One class per behavior area, not per
  source file.
- **Test method names**: `test_<verb_phrase_describing_outcome>`. Prefer
  "what it should do" over "what is checked":
  - Good: `test_user_b_cannot_update_user_a_topic`
  - Less good: `test_update_returns_404`
- **One behavior per test**. Ownership suite is the model —
  each test probes one endpoint + one mutation shape.

### Fixtures (`services/tests/_helpers.py`)

Three functions centralize the boilerplate every route test needs:

- `make_test_app()` — returns `(client, store, adapter, temp_dir)`.
  Each test gets its own SQLite file in a tempdir, its own instance of
  `create_app`, and a `MagicMock` adapter wired via
  `create_app(store=..., adapter=...)`. The app is completely isolated
  from every other test — session secret, CORS config, seeded system
  user are all fresh.
- `signup_and_login(client, email, password)` — mutates the passed
  `TestClient` in place so subsequent calls on it carry the signed
  `inspira_session` cookie. Returns the signup response body. httpx
  (the underlying TestClient transport) persists cookies on the client
  automatically.
- `fake_kickoff_response()` / `fake_turn_response(action=...)` —
  canonical valid-shaped planner payloads. Route tests assign them to
  `adapter.kickoff.return_value` and `adapter.topic_turn.return_value`
  so routes exercise the full persist-and-return path without hitting
  OpenAI.

**Setup pattern:**

```python
class MyFeatureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        signup_and_login(self.client, email="u@example.com", password="password123")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()
```

**Gotcha:** keep `self.temp_dir` alive across the test. If it gets
garbage-collected mid-test, the tempdir disappears and SQLite errors
with `unable to open database file`. The handle is kept alive by
being an attribute on `self`.

### Cross-origin / cookie behavior

Prefer testing via the FastAPI `TestClient`. It's a thin wrapper over
httpx and honors `Set-Cookie` correctly across requests on the same
client. Two users = two clients:

```python
self.a, self.store, self.adapter, self.temp_dir = make_test_app()
self.b = TestClient(self.a.app)  # same app, different cookie jar
signup_and_login(self.a, email="alice@example.com", password="alice-pw-1")
signup_and_login(self.b, email="bob@example.com", password="bob-pw-123")
```

This is exactly the shape in `test_ownership.py`.

### Mocking the LLM

Never let tests hit OpenAI. The pattern:

```python
self.adapter.kickoff.return_value = fake_kickoff_response()
response = self.client.post(
    f"/api/v2/projects/{project_id}/kickoff",
    json={"user_idea": "A small outdoor wine festival."},
)
```

For negative tests — "make sure the LLM was NOT called when auth
fails" — assert on the mock:

```python
self.adapter.kickoff.assert_not_called()
```

### Windows tempfile cleanup

`tempfile.TemporaryDirectory(ignore_cleanup_errors=True)` is used in
`_helpers.py:57` because SQLite on Windows sometimes holds the file
open past GC, and cleanup throws a `PermissionError`. The
`ignore_cleanup_errors` flag makes that a warning instead of a test
failure. The same pattern is in `test_service.py` and `test_v2_http.py`.

## Integration tests that hit OpenAI

`services/tests/test_openai_adapter.py` has two tests gated on
`OPENAI_API_KEY` in the environment:

```python
@unittest.skipIf(
    not os.environ.get("OPENAI_API_KEY"),
    "set OPENAI_API_KEY to run live integration tests",
)
class LiveKickoffTests(unittest.TestCase): ...
```

These actually call the API. They spend real tokens (a few hundred per
test). Only run them when you've changed the adapter or the prompts:

```bash
export OPENAI_API_KEY="sk-..."
export INSPIRA_TEST_MODEL="gpt-5-mini"  # or "gpt-5" for cheaper check
cd services
python -m unittest tests.test_openai_adapter -v
```

Or on Windows (PowerShell):

```powershell
$env:OPENAI_API_KEY = "sk-..."
$env:INSPIRA_TEST_MODEL = "gpt-5-mini"  # or "gpt-5" for cheaper check
Set-Location .\services
python -m unittest tests.test_openai_adapter -v
```

CI does NOT set `OPENAI_API_KEY`, so live tests are skipped automatically.

## Security regression tests

`services/tests/test_ownership.py` is the living test for the April
2026 security audit's C2 finding (IDOR). Treat it as a hard gate:

- Every new route that touches a topic / decision / relationship must
  be added here with two-user probes.
- The expected status for "not your thing" is **always `404`**,
  NEVER `403`. A `403` leaks object existence; a `404` does not.

When you add a cross-user probe, include BOTH the read/mutate attempt
and a positive-control read from the real owner to confirm the
operation didn't leak through:

```python
def test_user_b_cannot_delete_user_a_topic(self) -> None:
    response = self.b.post(f"/api/v2/topics/{self.topic_id}/delete")
    self.assertEqual(response.status_code, 404)
    # Positive control — user A can still see the topic
    topics = self.a.get(
        f"/api/v2/projects/{self.project_id}/topics",
    ).json()["topics"]
    self.assertIn(self.topic_id, {t["topic_id"] for t in topics})
```

## Adding a new test

1. Find the right file by system:
   - HTTP route behavior → `test_api_fastapi.py`
   - auth / cookie / user state → `test_auth_routes.py`
   - cross-user access → `test_ownership.py`
   - adapter unit behavior → `test_openai_adapter.py`
2. Add a `unittest.TestCase` subclass if none fits your area, or a new
   test method inside an existing class.
3. Use the `_helpers.py` fixtures. Do not re-roll
   `TemporaryDirectory` + `MagicMock` from scratch.
4. Run the single test to iterate:
   ```powershell
   python -m unittest tests.test_api_fastapi.MyNewTests -v
   ```
5. Run the full suite before you commit:
   ```powershell
   python -m unittest discover -s tests -v
   ```

## Why unittest (not pytest)

Historical reason: `unittest` was already in use when pytest got added
to the dev deps for future fixtures. Every existing test file inherits
`unittest.TestCase`. Do not mix the two in one file — pick one per file.
The stdlib path remains the supported default because:

- CI's `python -m unittest discover` works without any config.
- The fixtures in `_helpers.py` are plain functions, not pytest fixtures.
- Adding pytest-only flavor to a random file breaks the discover path
  unless you add `pytest-asyncio`-style markers the other files don't
  carry.

If you want to move to pytest for a specific new feature, open a PR
that converts ALL suites and drops `_helpers.py`'s functions in favor
of pytest fixtures. A partial migration is worse than either endpoint.

## Live-adapter live-reload

Use `UVICORN_RELOAD=true` when iterating on `api.py` or `auth.py`. The
store (`store.py`) reloads cleanly too — the SQLite connection is
opened per-method, not held on the dataclass. Schema changes to
`_initialize*` or an alembic migration require stopping the service
and either deleting `local/data/planning-studio.sqlite` or running the
new migration explicitly.
