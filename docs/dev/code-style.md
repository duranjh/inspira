# Code Style

Short style guide for contributors. Two languages, two conventions.

## TypeScript / React

- **Indentation:** 2 spaces. No tabs.
- **Line length:** 100 chars soft cap.
- **Quotes:** double quotes for strings and JSX attributes.
- **Semicolons:** required.
- **Trailing commas:** always, in multi-line literals.
- **Naming:**
  - `camelCase` for variables, functions, props.
  - `PascalCase` for React components and type aliases / interfaces.
  - `UPPER_SNAKE_CASE` for module-level constants.
  - `useSomething` for hooks.
- **Imports:**
  - No unused imports.
  - Keep external imports above internal imports with a blank line
    separator.
  - No default-export convenience for components with a meaningful name
    — prefer named exports (`export function Foo() {...}`).
- **Components:**
  - **Functional only.** Never use `class` for React components
    (`ErrorBoundary` is the exception — it has to be a class to own
    `componentDidCatch`).
  - **One component per file** for reusable components. Multiple small
    inline helper components inside the same file are fine when they
    exist only in service of the main one (see `ProjectSwitcher` inside
    `InspiraApp.tsx`).
  - File name matches the exported component's `PascalCase` name:
    `ProjectCanvas.tsx` exports `ProjectCanvas`.
- **State:**
  - Prefer local state (`useState`) + prop passing.
  - Use discriminated unions for phase / mode state (see
    `InspiraApp.tsx:35`).
  - No Redux. No Zustand. No SWR / TanStack Query. If you need cross-
    component state that doesn't fit prop passing, use a React context
    colocated with the feature (`ToastProvider` pattern).
- **Types:**
  - Every exported function / component signature is typed.
  - Prefer `type` aliases for object shapes (used consistently in
    `api.ts`); reserve `interface` for types you expect consumers to
    extend (e.g. `ShortcutBinding`).
  - Avoid `any`. Use `unknown` when the shape is genuinely unknown at
    the boundary.
- **Functions:**
  - Prefer pure functions. Side effects (fetch, DOM write, event
    dispatch) live in `useEffect` or event handlers, not in render.
- **Error handling:**
  - `fetch` wrappers in `api.ts` throw `Error` with the HTTP status + body.
  - Callers translate to user-visible state via ToastProvider or by
    setting a phase variant.
- **Comments:**
  - Lead each non-trivial file with a block comment describing why it
    exists. Follow the pattern in `InspiraApp.tsx:1`.
  - Use `// ---- Section header ----` dividers for long files.
  - Write sentence-cased prose comments. Not JSDoc unless the export is
    intended for external consumers.

### Example — the canonical new-component shape

```typescript
// What this component does, and why it's a separate file.

import { useCallback, useState } from "react";

import { api, type Decision } from "./api";
import { toast } from "../../components/ToastProvider";

export type MyComponentProps = {
  topicId: string;
  onDecisionsChanged: () => void;
};

export function MyComponent({ topicId, onDecisionsChanged }: MyComponentProps) {
  const [busy, setBusy] = useState(false);

  const handleConfirm = useCallback(
    async (statement: string) => {
      setBusy(true);
      try {
        await api.createDecision(topicId, { statement });
        onDecisionsChanged();
      } catch (err) {
        toast.error(err instanceof Error ? err.message : "Unknown error");
      } finally {
        setBusy(false);
      }
    },
    [topicId, onDecisionsChanged],
  );

  return <button disabled={busy} onClick={() => handleConfirm("...")}>Confirm</button>;
}
```

## Python

- **Indentation:** 4 spaces. No tabs.
- **Line length:** 100 chars soft cap (some of our older comment blocks
  run to 88 for argparse-style readability; both are fine).
- **Imports:**
  - `from __future__ import annotations` at the top of every module
    that uses postponed evaluation (most of them). See
    `services/planning_studio_service/api.py:22`.
  - stdlib → third-party → internal, separated by a blank line.
  - No unused imports.
- **Naming:**
  - `snake_case` for functions, variables, module names.
  - `PascalCase` for classes, pydantic models, TypedDict.
  - `UPPER_SNAKE_CASE` for constants.
  - Private helpers prefixed with `_` (see `_record_llm_usage`,
    `_require_owned_project`).
- **Type hints:**
  - Required on every public function and method.
  - Use PEP 604 union syntax (`str | None`) rather than
    `Optional[str]`.
  - `Any` is OK at the HTTP boundary where payloads are loose dicts;
    avoid it everywhere else.
- **Docstrings:**
  - Module docstring at the top, explaining the file's role.
  - Every public class/function has at least a one-liner.
  - Multi-paragraph docstrings use sentence prose, not rST.
  - Examples for non-obvious API shapes (see
    `services/planning_studio_service/agents/openai_adapter.py:161`).
- **Comments:**
  - Plain-English prose, sentence-cased.
  - Prefer explanatory comments ("why") over narrating comments
    ("what"). The code says what.
- **Data classes:**
  - Use `@dataclass(slots=True)` for small internal config objects
    (see `OpenAIConfig`, `ServiceConfig`).
  - Use `BaseModel` from pydantic for HTTP request/response shapes.
- **Error handling:**
  - Catch narrowly. `except Exception:` is fine only with `# noqa:
    BLE001` and a clear intent (usually "translate to user-facing
    500" or "instrumentation must not break flows"). Follow the
    pattern in `_record_llm_usage`:
    ```python
    except Exception as exc:  # noqa: BLE001
        logger.warning("...", exc)
    ```
  - Re-raise with `from exc` when wrapping.
- **Functions:**
  - Prefer pure functions. Keep IO (SQL, HTTP, filesystem) at the
    edges of the call graph.
  - Pass collaborators in as arguments (store, adapter, client) rather
    than reaching into module globals. Makes testing straightforward.
- **Logging:**
  - Module-level logger: `logger = logging.getLogger("planning_studio.<module>")`.
  - INFO for state transitions, WARNING for recoverable issues, ERROR
    for real failures.
  - Never log secrets or PII.

### Example — the canonical new-function shape

```python
"""Brief module docstring.

Longer explanation if the file has a non-obvious role.
"""
from __future__ import annotations

import logging
from typing import Any

from .store import PlanningStudioStore

logger = logging.getLogger("planning_studio.example")


def do_a_thing(
    *,
    store: PlanningStudioStore,
    user_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Describe what this does and why.

    Returns a dict shaped as ``{...}``. Raises ``ValueError`` when the
    payload is missing required fields.
    """
    if not payload.get("required_field"):
        raise ValueError("required_field is required")
    try:
        row = store.some_method(user_id=user_id, ...)
    except Exception as exc:  # noqa: BLE001 — propagate
        logger.exception("do_a_thing failed for user=%s", user_id)
        raise RuntimeError("do_a_thing failed") from exc
    return {"result": row}
```

## Cross-cutting rules

- **No emoji in source or docs.** Product copy may use em dashes freely;
  code comments should stay plain.
- **US English.** "color" not "colour", "behavior" not "behaviour".
- **No commented-out code.** Delete it. git history has the audit trail.
- **Keep files focused.** If a file crosses ~1000 lines it's a signal
  to split. Current large files (`InspiraApp.tsx`, `ProjectCanvas.tsx`,
  `TopicDetail.tsx`, `store.py`, `api.py`) are on the list.
- **No new dependencies without justification.** Add to
  `services/pyproject.toml` or `app/package.json` only when a clear
  need exists. Check the file's header comments — several list
  "intentionally no new dependencies."
- **Never skip git hooks.** `--no-verify` bypasses commit linting.
  Fix the linted issue instead.
- **Never amend.** Prefer a new commit over `git commit --amend` for
  published history. Amending is fine for still-unstaged WIP on your
  local branch before pushing.

## Formatters and linters

Not enforced automatically today. Projects gradually add:

- **Python:** no black / ruff wired; we match style by inspection. If
  you land a big refactor, run `ruff check services/` manually to
  catch low-hanging issues (it'll flag unused imports, undefined
  names, and a few bad patterns).
- **TypeScript:** `tsc --noEmit` is the hard gate. No ESLint / Prettier
  today. Match the surrounding style in the file you edit.

## File layout reminder

```
services/
  planning_studio_service/
    agents/
      base.py              # Protocol for provider adapters
      openai_adapter.py    # OpenAI
      claude_adapter.py    # Anthropic (fallback, scaffolded)
      prompts.py           # system + mode prompt constants
      schemas.py           # JSON Schema dicts for tool calls
      suggestions.py       # AI project suggestions (separate call)
    api.py                 # FastAPI app factory + all v2 + meta routes
    auth.py                # signup/login/me + current_user dependency
    store.py               # SQLite bootstrap + CRUD
    config.py              # ServiceConfig, load_config
    app.py                 # legacy BaseHTTPServer entry (opt-in)
    __main__.py            # uvicorn entrypoint
    _env_bootstrap.py      # .env loader
  alembic/versions/        # one migration today
  tests/                   # unittest suites

app/
  src/
    App.tsx                # minimal shell → InspiraApp
    main.tsx               # root mount with ErrorBoundary + ToastProvider
    features/
      inspira/             # canvas, detail, kickoff, api, layout, file_extract
      projects/            # projects list (secondary UI path)
      account/             # account settings (awaiting backend)
      onboarding/          # first-run walkthrough
      errors/              # 404, offline, 500, session-expired
      palette/             # command palette + search overlay
      shared/              # cross-feature helpers
    components/            # app-wide UI (ErrorBoundary, Toast, Dialogs, ...)
    hooks/                 # useKeyboardShortcuts, useOnlineStatus
    pwa/                   # service worker source (inactive in dev)
    App.css                # all the global styles
```
