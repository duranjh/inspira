# LLM Pipeline

End-to-end tour of how a user request ends up producing a planner turn,
from prompt assembly to post-call repair to token accounting.

## Provider topology

- **Primary:** OpenAI Chat Completions
  (`services/planning_studio_service/agents/openai_adapter.py`). Default
  model `gpt-5-mini` (`openai_adapter.py:50`), configurable per-instance
  via `OpenAIConfig`.
- **Fallback:** Anthropic Messages API
  (`services/planning_studio_service/agents/claude_adapter.py`). Default
  model `claude-sonnet-4-6` (`claude_adapter.py:64`), configurable via
  `ANTHROPIC_MODEL`.
- **Current wiring:** the primary adapter is instantiated lazily by
  `_require_adapter` in `api.py:255`. The fallback adapter is **planned**
  — `claude_adapter.py` exists, implements the same `PlanningInterviewer`
  interface, and reuses the same sanitize helpers, but the OpenAI adapter
  does not yet call into it when the circuit breaker opens or retries are
  exhausted. Today a primary failure returns
  `500 planner_call_failed` to the client.

## Prompt structure

Every LLM call composes exactly **one** prompt of the shape:

```
system = BASE_SYSTEM_PROMPT + "\n\n" + <MODE>_MODE_PROMPT
user   = <mode-specific user message built from request context>
```

Only one mode prompt is appended per call — never concatenate modes.

### BASE_SYSTEM_PROMPT

Source: `services/planning_studio_service/agents/prompts.py:16`.

Defines the planner persona: "focused, quietly warm thinking partner,"
one question at a time with a "why this matters" annotation, up to three
suggested responses, decision extraction with user confirmation,
pressure-test at weak spots, consistency-check against every other
topic's decisions, no emoji, no cheerleading, structured tool-call
output only.

### Mode prompts

Five modes defined in `prompts.py`:

| constant | line | purpose | status |
|---|---|---|---|
| `KICKOFF_MODE_PROMPT` | `prompts.py:57` | Map a vague idea into 5-10 topic cards with dotted relationships. | **active** — wired to `POST /api/v2/projects/{id}/kickoff`. |
| `TOPIC_INTERVIEW_MODE_PROMPT` | `prompts.py:110` | One planner turn inside a topic's Q&A thread. | **active** — wired to `POST /api/v2/topics/{id}/turn`. |
| `COMPOSER_ROUTE_MODE_PROMPT` | `prompts.py:162` | Route a free-text composer input to the right surface. | **planned** — schema and prompt exist, no HTTP route yet. |
| `SUMMARY_SYNTHESIS_MODE_PROMPT` | `prompts.py:196` | Regenerate the Plan Summary as adaptive sections. | **planned**. |
| `PROPAGATION_PREVIEW_MODE_PROMPT` | `prompts.py:231` | Preview downstream effects of a decision edit. Never mutates. | **planned**. |

Each mode has a matching JSON Schema (`agents/schemas.py`) enforced via
OpenAI's `strict: true` function-calling mode. See the
[tool-call enforcement](#tool-call-enforcement) section.

### User message assembly

- Kickoff: `_format_kickoff_user_message` (`openai_adapter.py:591`) —
  renders the user's idea between `---` fences and lists attached
  sources with their excerpts.
- Topic turn: `_format_topic_turn_user_message`
  (`openai_adapter.py:418`) — renders the current topic (title + icon),
  prior decisions, full Q&A thread, open questions, risks, and every
  **other** topic with its decisions for cross-topic consistency checking.

## Tool-call enforcement

The adapter forces the model to respond with a single specific
function-call. No free-text responses; no choice of tool.

Request-side (`openai_adapter.py:139`):

```python
"tools": [tool_spec],
"tool_choice": {"type": "function", "function": {"name": "kickoff_response"}},
```

Where `tool_spec` comes from `_build_openai_tool_spec` (`openai_adapter.py:346`)
which wraps the JSON Schema in OpenAI's envelope with `strict: true`:

```python
{
  "type": "function",
  "function": {
    "name": "kickoff_response",
    "description": spec["description"],
    "parameters": spec["schema"],
    "strict": true,
  },
}
```

Response-side (`openai_adapter.py:360` `_extract_tool_call_args`):

- Pulls `response.choices[0].message.tool_calls[0]`.
- Validates the function name matches what we forced.
- `json.loads` on the arguments string.
- Raises `_EmptyToolCallResponse` if there was no tool call — a
  retriable condition (see below).

### Schema strictness

Every schema in `agents/schemas.py`:

- Uses `"additionalProperties": false` at every object level.
- Lists every property in `"required"` even for optional fields — the
  null case uses `"type": ["string", "null"]` rather than
  `"nullable": true`.
- Uses explicit `"enum"` for closed choices (icons, actions, domains).

Strict mode enforces these at decode time, so an otherwise-successful
API call is guaranteed to return a conformant shape. The adapter's
post-call `_sanitize_*` functions (below) catch cross-reference bugs
that per-property schema constraints can't express.

## Reliability layers

In call order, outermost to innermost:

### 1. Circuit breaker

`openai_adapter.py:271-290`. Uses `pybreaker`:

- `fail_max = 5` — after 5 consecutive transient failures, the breaker
  trips.
- `reset_timeout = 60` seconds — during which further calls
  short-circuit with `RuntimeError("OpenAI temporarily unavailable...")`.
- `name = "openai-chat-completions"`.

Goal: fail fast instead of piling on a broken upstream. When the breaker
raises `CircuitBreakerError`, the adapter repackages it as a plain
`RuntimeError` with a user-friendly message so the HTTP layer's
`_planner_error_response` (`api.py:384`) can scrub it and return a
correlation id.

### 2. Transient-error retry

`openai_adapter.py:293-331`. Inside the breaker call:

- `_TRANSIENT_RETRIES = 3` retries.
- Exponential backoff: `_TRANSIENT_BACKOFF_BASE = 1.5s`, doubled each
  attempt.
- `_is_transient_error` matches across SDK versions: class names
  `RateLimitError`, `APIConnectionError`, `APITimeoutError`,
  `InternalServerError`, plus `status_code == 429` or `5xx`, plus plain
  `TimeoutError` / `ConnectionError`.
- Non-transient errors propagate immediately.

### 3. Empty-tool-call retry

`openai_adapter.py:234-265` `_call_with_toolcall_retry`. GPT-5-family
occasionally returns no `tool_call` despite `tool_choice` forcing one —
usually because reasoning ate the completion budget. The adapter retries
once by default (`OpenAIConfig.max_empty_toolcall_retries = 1`). No
backoff — this is model non-compliance, not a rate-limit issue.

The inner `_extract_tool_call_args` surfaces diagnostic detail on the
exception (`finish_reason`, `usage.completion_tokens_details`,
`content`) so a persistent empty-tool-call can be debugged by reading
the server log.

### 4. Timeout

Hard-coded 30-second per-call timeout (`OpenAIConfig.timeout_s = 30.0`,
`openai_adapter.py:51`). Passed through as `timeout=` to the SDK.

## Sanitize + repair layer

After a successful tool call, the adapter runs one of two
mode-specific sanitizers to catch cross-reference bugs strict mode
can't express.

### `_sanitize_kickoff_response` (`openai_adapter.py:635`)

Behavior:

- **Structural failures raise `RuntimeError`** (the call is broken,
  don't show a broken canvas):
  - Model set `clarifying_question_if_too_vague` AND returned topics or
    relationships — internally inconsistent.
  - Topic count outside the `[5, 10]` soft limits (config-tunable).
- **Minor integrity bugs are silently repaired** (log under
  `parsed["_sanitize"]`):
  - Relationships referencing unknown topic titles are dropped.
  - Invalid `suggested_first_topic` falls back to the first topic.
  - Orphan topics (not referenced in any relationship) auto-connect to
    the anchor topic with a `"relates to"` label. The anchor itself
    falls back to the second topic if it would self-loop.

### `_sanitize_topic_turn` (`openai_adapter.py:540`)

- Enforces `action → field` consistency:
  - `action == "suggest_close"` must have null question /
    why_this_matters / suggested_responses. Sanitizer clears them if set.
  - `action in {"ask", "pressure_test", "followup"}` must have a
    question. Missing one raises `RuntimeError`.
  - Unknown action values raise `RuntimeError`.
- Drops `consistency_flags` referencing a topic title that is not
  actually in `other_topics` (model hallucination). Logged under
  `parsed["_sanitize"]["dropped_consistency_flags"]`.

Both sanitizers attach a `_sanitize` key to the parsed dict so callers
can log / surface repairs. The frontend ignores this key (typed as
optional in `app/src/features/inspira/api.ts:50`).

## Claude fallback path

`services/planning_studio_service/agents/claude_adapter.py` implements
the same `PlanningInterviewer` interface. It:

- Reuses the exact same `BASE_SYSTEM_PROMPT` + mode prompt
  (`claude_adapter.py:150, 176`).
- Reuses `_format_kickoff_user_message` and
  `_format_topic_turn_user_message` from `openai_adapter.py`.
- Reuses `_sanitize_kickoff_response` and `_sanitize_topic_turn` so
  provider-agnostic post-processing stays in one place.
- Wraps the schema in Anthropic's flatter envelope (no `function`
  wrapper, no `strict` flag — Claude enforces schemas by default on
  forced `tool_choice`), see `_build_claude_tool_spec`
  (`claude_adapter.py:225`).
- Pulls the forced tool-use block from `response.content` where
  `block.type == "tool_use"`. Raises `RuntimeError` on missing block
  with stop_reason / usage / block-type diagnostics
  (`_extract_tool_use_args`, `claude_adapter.py:240`).

**Not yet integrated** with the OpenAI fallback path. Activation plan:
when `_openai_breaker` is open or `_TRANSIENT_RETRIES` exhaust, catch
the terminal `RuntimeError` and retry the same call through
`ClaudePlanningInterviewer`. Until then, dual-provider outages surface
to the client as the single `planner_call_failed` 500.

Construction requires `ANTHROPIC_API_KEY`; a missing key raises at
instantiation so a broken fallback fails loudly instead of producing a
confusing 401 later (`claude_adapter.py:112`).

## Token budget enforcement

### Per-user daily budget

Gate: `_require_token_budget` in `api.py:408`. Called BEFORE the LLM
call on any route that makes one.

- Default budget: **200,000 combined prompt+completion tokens per UTC
  day per user** (`api.py:49`).
- Override: `INSPIRA_USER_DAILY_TOKEN_BUDGET` env var. Non-positive value
  disables the gate entirely (dev/test escape hatch).
- Reset: implicit at UTC midnight — the `user_usage` table keys rows on
  `(user_id, day_utc)`, so tomorrow's row starts fresh.
- Denial response: HTTP 429 with
  `{"error": "daily_token_budget_exhausted", "budget": N, "spent": M,
  "retry_after_seconds": S}` and a `Retry-After` header set to
  `_seconds_until_utc_midnight()`.

### Usage recording

Post-call: `_record_llm_usage` in `api.py:443`. Called AFTER the LLM
returns.

- Prefers real `response.usage.prompt_tokens` / `completion_tokens` when
  the caller passes the OpenAI usage object.
- Falls back to a char-count estimate: `len(text) // 4`
  (`api.py:64` `_ESTIMATE_CHARS_PER_TOKEN`). Conservative over-estimate
  beats under-counting because the budget is the user-visible cap.
- Suggestions path (`api.py:915`) wraps the usage payload in a
  `SimpleNamespace` so `_record_llm_usage` reads it the same way.
- Instrumentation errors are swallowed with a log — never block a user
  flow on usage-recording failure.

### Per-IP rate limit

Independent of the token budget. Default 120 requests / minute per
remote IP (`api.py:311`). Configured via `INSPIRA_RATE_LIMIT` as a
slowapi rate string (e.g. `"60/minute"`, `"10/second"`).

## Prompt caching

OpenAI automatically caches prompts ≥1,024 tokens. `BASE_SYSTEM_PROMPT`
+ a mode prompt is ~2k tokens, so every call on the same project hits
the cache after the first. No explicit cache-breakpoint API is
required; tuning is handled by OpenAI's server-side behavior.

## Suggestions call (separate path)

The AI project-suggestions feature
(`services/planning_studio_service/agents/suggestions.py`) is a
one-shot call that lives outside the `PlanningInterviewer` contract:

- Fresh `OpenAI` client per call (no retry/sanitize plumbing,
  `suggestions.py:224`).
- Own system prompt (`suggestions.py:113 _SYSTEM_PROMPT`).
- Own tool spec (`suggestions.py:46 _SUGGESTIONS_TOOL_SPEC`) forcing a
  `project_suggestions` call with 3-5 items.
- Privacy contract: the prompt only ever sees project titles, topic
  titles, and confirmed decision statements. Never Q&A bodies,
  attachments, or rationale text.
- Caller path: `api.py:910` `v2_suggest_projects` — gates on real user
  (not system user), `>= 2` active projects, and a 4-hour in-database
  cache (`store.suggestions_cache`). Token budget applies on cache miss.

## Provider-agnostic tests

- `services/tests/test_openai_adapter.py` — unit tests for sanitize,
  retry, tool-call extraction, circuit-breaker behavior, plus two live
  integration tests gated on `OPENAI_API_KEY` (skipped in CI).
- `services/tests/_helpers.py:96` `fake_kickoff_response` +
  `fake_turn_response` — canonical valid-shaped payloads used to stub
  the adapter in route tests so CI never hits OpenAI.
