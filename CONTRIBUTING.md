# Contributing to Inspira

Thanks for your interest in Inspira. This repository is `planning-studio` internally — the public product brand is **Inspira**. The working code, services, and folder names keep the `planning-studio` identifier for historical reasons; anything user-facing should say "Inspira."

Today, Inspira is built by a single founder. These guidelines are written for a solo project now, but set up so future contributors can join without the rules changing underneath them.

---

## Code of conduct

Be respectful, thoughtful, and kind. Disagreements happen — keep them about the work, not the person. The formal code of conduct lives at [`.github/CODE_OF_CONDUCT.md`](.github/CODE_OF_CONDUCT.md).

Report unacceptable behavior privately to the maintainer (**@duranjh** on GitHub).

---

## Ways to contribute

- **Report a bug.** Open an issue with the steps to reproduce, what you expected, and what actually happened. Include browser and operating system if the bug is UI-related, and an `X-Request-ID` header value if you see one.
- **Propose a feature.** Open an issue titled "Proposal: <short name>" with the problem, the audience, and one or two sketches of a solution. Keep the scope small — one page is plenty.
- **Improve the docs.** Fixes to typos, broken links, outdated examples, and rough edges are always welcome.
- **Submit a patch.** See the development setup below.
- **Report a security issue.** Do **not** open a public issue. Report privately via GitHub Security Advisories (Security tab → "Report a vulnerability"). See also the security-research section of the Acceptable Use Policy.

---

## Development setup

### Prerequisites

- Node.js 20+ (bundled with npm)
- Python 3.11+
- A local or remote Postgres 15+
- An OpenAI API key and an Anthropic API key for the AI features

### One-time setup

```bash
# From the repo root
npm --prefix app ci
pip install -e services/
cp services/.env.example services/.env   # fill in the values
```

`services/.env` holds local secrets and is gitignored. Set at least:

```
DATABASE_URL=postgresql://...
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
INSPIRA_SESSION_SECRET=<any 32+ char random string>
```

### Running the backend

```bash
python -m planning_studio_service
```

The API listens on `http://localhost:8000` by default. Run database migrations with Alembic if required (`alembic upgrade head`).

### Running the frontend

```bash
npm --prefix app run dev
```

The app is available at `http://localhost:5173` and talks to the backend on `http://localhost:8000`.

### Running the tests

```bash
# Python (services)
pytest services/tests

# Frontend
npm --prefix app test
```

Add tests for every non-trivial change. A PR that changes behavior without a test is a reason to request changes.

---

## Branching strategy

Trunk-based with short-lived feature branches:

- `main` is always deployable. Never push work-in-progress there.
- Feature work lives on a branch named `type/short-description` — for example, `feat/canvas-undo`, `fix/topic-drag-race`, `chore/bump-fastapi`.
- Keep branches short-lived. Rebase frequently and merge within days, not weeks.
- Delete branches after they merge.

---

## Commit messages

Follow the existing convention, which matches the [Conventional Commits](https://www.conventionalcommits.org) spirit without strict tooling:

```
type(scope): short imperative summary

Longer description if needed, wrapped at ~72 chars. Explain the why.
Reference issues as "Fixes #123" or "Related to #456" on their own line.
```

Allowed types:

- `feat` — a new user-visible capability
- `fix` — a bug fix
- `refactor` — structural change with no behavior change
- `chore` — dependency bumps, build-system tweaks, non-code tasks
- `docs` — documentation-only changes
- `test` — test-only changes
- `perf` — performance work
- `ci` — CI configuration changes
- `style` — formatting, missing semicolons, no logic change

Scope is optional but recommended. Look at `git log` for examples — current scopes include `canvas`, `inspira`, `app`, `http`, `adapter`.

Examples from the history:

```
feat(canvas): spacing, drag-persist, edge CRUD, dagre auto-layout
fix(adapter): reasoning-budget, reasoning_effort, and graceful repair
```

---

## Pull requests

1. Open a draft PR as soon as you have something running. Mark it "Ready for review" when it is actually reviewable.
2. Fill in the PR description: what changed, why, how to test.
3. Link the issue the PR closes.
4. Keep PRs focused. If you find yourself mixing a refactor with a feature, split them into two PRs.
5. Keep the diff reviewable. Anything over 500 lines of real code needs a very good reason.
6. Update docs in the same PR as the code change. Out-of-date docs are a bug.

### Testing expectations

Before requesting review:

- The code compiles and linters pass (`npm --prefix app run lint`, `ruff check services/`, etc.).
- All tests pass locally.
- You ran the smoke-test flow in `docs/ops/runbook.md` for anything that touches the core flows (sign-in, kickoff, topic Q&A, canvas edit).
- You added or updated tests for the change.

### Review process

Today, with a single maintainer, review is self-review plus a cooling-off period: open the PR, leave it for a few hours, come back, read the diff as if someone else wrote it, and merge only if it still makes sense.

When a second maintainer joins, review becomes required: one approval, no self-merges on production paths.

---

## Style

- **Python:** PEP 8 via `ruff`. Type hints where they help. Docstrings on public functions in `planning_studio_service`.
- **TypeScript:** prettier + eslint defaults. Prefer named exports. No `any` without a reason.
- **Markdown:** 80-char soft wrap where practical. US English.
- **Language:** in user-facing copy, documentation, and marketing, use "Inspira" (capitalized). Internal code can keep `planning_studio_service`, `planning-studio`, and similar identifiers unchanged.
- **No emojis in code, commits, or docs** unless they carry a safety signal (see the draft legal docs for the only intended exception).

---

## License and contributor agreements

Inspira is licensed under the [MIT License](LICENSE). By submitting a
contribution, you agree that it is licensed under MIT as well. There is no
CLA to sign.

---

## Questions

Open an issue, or start a thread in GitHub Discussions.

---

*Related docs: `docs/ops/runbook.md` (day-to-day), `docs/ops/incident-response.md` (incidents), `docs/legal/acceptable-use.md` (what users can and can't do).*
