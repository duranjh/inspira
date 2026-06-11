# Services

Standalone Inspira backend (FastAPI). Internal package name `planning_studio_service`.

## Local development (Mode 3: Postgres + empty)

Prod runs on Fly + Neon (Postgres). Local dev uses the same engine via Homebrew Postgres 16, with a fresh empty DB per worktree/session. Catches Postgres-specific migration drift that SQLite would silently mask.

### One-time setup

```bash
# Install Postgres 16
brew install postgresql@16
brew services start postgresql@16

# Create the role + base DB
/opt/homebrew/opt/postgresql@16/bin/psql -d postgres -c \
  "CREATE ROLE inspira WITH LOGIN PASSWORD 'inspira' CREATEDB;"
/opt/homebrew/opt/postgresql@16/bin/psql -d postgres -c \
  "CREATE DATABASE inspira_wave34_main OWNER inspira;"
```

### Per-worktree setup

```bash
cd services
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

# Create services/.env.local (gitignored). Copy OPENAI_API_KEY from root .env.
cat > .env.local <<EOF
DATABASE_URL=postgresql://inspira:inspira@127.0.0.1:5432/inspira_wave34_main
INSPIRA_DATABASE_DIRECT_URL=postgresql://inspira:inspira@127.0.0.1:5432/inspira_wave34_main
SESSION_SECRET=local-dev-secret-not-for-prod
INSPIRA_SESSION_SECRET=local-dev-secret-not-for-prod
INSPIRA_BYOK_SECRET=local-dev-byok-secret
INSPIRA_ALLOWED_ORIGINS=http://127.0.0.1:5181
OPENAI_API_KEY=<copy from root .env>
ENVIRONMENT=local
EOF
```

### Daily use

```bash
./services/scripts/dev.sh              # default DB inspira_wave34_main
./services/scripts/dev.sh alpha        # use inspira_wave34_alpha (per-session)
```

`dev.sh` is idempotent: ensures Postgres is running, creates the DB if missing, runs alembic, starts FastAPI on `127.0.0.1:4174`.

The frontend Vite dev server reads `VITE_INSPIRA_API_URL`; default is `http://127.0.0.1:4174`. Start it in a second shell:

```bash
cd app
VITE_INSPIRA_API_URL=http://127.0.0.1:4174 npm run dev -- --port 5181 --host 127.0.0.1
```

### Resetting between test runs

```bash
./services/scripts/reset-local-db.sh           # drops + recreates inspira_wave34_main
./services/scripts/reset-local-db.sh alpha     # targets inspira_wave34_alpha
```

### Parallel-session DB names (wave-3-4)

| Session | DB name | FastAPI port | Vite port |
|---|---|---|---|
| orchestrator | `inspira_wave34_main` | 4174 | 5181 |
| α | `inspira_wave34_alpha` | 4175 | 5182 |
| β | (pytest only, SQLite in-memory) | — | — |
| γ | `inspira_wave34_gamma` | 4177 | 5184 |
| δ | `inspira_wave34_delta` | 4178 | 5185 |
| ε | `inspira_wave34_epsilon` | 4179 | 5186 |

### Tests

```bash
source services/.venv/bin/activate
pytest services/tests/ -x
```

(Tests use SQLite in-memory by default; no Postgres needed for the test suite.)

## Production

Deployed to Fly.io via `services/scripts/deploy.sh`. DB is Neon Postgres. See `fly.toml` + `Dockerfile` for prod config.
