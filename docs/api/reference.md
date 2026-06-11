# API Reference

Complete REST API for the Inspira backend. Routes, payload shapes, and
error cases are sourced from
`services/planning_studio_service/api.py` and
`services/planning_studio_service/auth.py`.

## Base URL

- Dev: `http://127.0.0.1:4174`
- Prod: your deployed backend URL (e.g. `https://api.tryinspira.com`).

Frontend default: `import.meta.env.VITE_INSPIRA_API_URL` or
`http://127.0.0.1:4174` (`app/src/features/inspira/api.ts:9`).

## Conventions

- **Content type:** every endpoint accepts and returns
  `application/json`.
- **Auth:** request must carry the `inspira_session` cookie. Without
  it, the backend resolves the "system" user and most v2 endpoints
  return `404` because the system user owns no user-scoped projects.
  The frontend sets `credentials: "include"` on every fetch
  (`app/src/features/inspira/api.ts:117`).
- **Ownership errors:** return `404` (never `403`) with an
  `{"error": "..._not_found"}` detail. This prevents ID enumeration.
- **Validation errors:** return `422` from pydantic with a default
  detail shape.
- **Rate limiting:** per-IP default `120/minute`. Exceeding returns
  `429 {"error": "rate_limited", "detail": "..."}`.
- **Daily token budget:** 200k tokens per UTC day per user. Exceeding
  returns `429 {"error": "daily_token_budget_exhausted", "budget": N,
  "spent": M, "retry_after_seconds": S}` with a `Retry-After` header.
- **Timestamps:** ISO 8601 UTC, seconds precision.

## Endpoint Index

### Meta
- `GET /api/health` — liveness.

### Auth
- `POST /api/auth/signup` — create account, set cookie.
- `POST /api/auth/login` — verify, set cookie.
- `POST /api/auth/logout` — clear cookie.
- `GET /api/auth/me` — resolve current user.
- `GET /api/auth/google/start` — **planned (501)**.
- `GET /api/auth/google/callback` — **planned (501)**.

### v2 projects
- `GET /api/v2/projects` — list user's projects.
- `POST /api/v2/projects` — create.
- `POST /api/v2/projects/{project_id}/update` — rename.
- `POST /api/v2/projects/{project_id}/delete` — soft-delete.
- `POST /api/v2/projects/suggest` — AI project suggestions.
- `POST /api/v2/projects/{project_id}/kickoff` — run planner kickoff.

### v2 topics
- `GET /api/v2/projects/{project_id}/topics` — list.
- `POST /api/v2/projects/{project_id}/topics` — create.
- `POST /api/v2/topics/{topic_id}/update` — update.
- `POST /api/v2/topics/{topic_id}/delete` — soft-delete.

### v2 decisions
- `GET /api/v2/topics/{topic_id}/decisions` — list for a topic.
- `POST /api/v2/topics/{topic_id}/decisions` — create.
- `GET /api/v2/projects/{project_id}/decisions` — list for a project.
- `POST /api/v2/decisions/{decision_id}/delete` — retract.

### v2 relationships
- `GET /api/v2/projects/{project_id}/relationships` — list.
- `POST /api/v2/projects/{project_id}/relationships` — create.
- `POST /api/v2/relationships/{relationship_id}/delete` — soft-delete.

### v2 Q&A turns
- `POST /api/v2/topics/{topic_id}/turn` — run the planner for a turn.
- `GET /api/v2/topics/{topic_id}/turns` — list the topic's thread.

### v1 legacy (deprecated)
- `GET /api/projects`
- `GET /api/sessions`
- `POST /api/sessions`
- `GET /api/artifacts`

---

## `GET /api/health`

Unauthenticated liveness endpoint. Returns a **trimmed** payload — no
filesystem paths, per the security audit (H4). Use this for Docker and
load-balancer healthchecks.

**Response 200:**

```json
{
  "service": "planning-studio",
  "status": "ok",
  "generated_at": "2026-04-21T12:34:56+00:00"
}
```

---

## Auth

### `POST /api/auth/signup`

Create a new user, sign them in, set the session cookie.

**Request:**

```json
{
  "email": "alice@example.com",
  "password": "at-least-eight-chars",
  "display_name": "Alice"
}
```

- `email` — required, RFC 5322 via pydantic `EmailStr`.
- `password` — required, 8-256 chars.
- `display_name` — optional, defaults to the left side of the email.

**Response 201:** `AuthedUser`

```json
{
  "user_id": "user-abc123...",
  "email": "alice@example.com",
  "display_name": "Alice",
  "is_system": false
}
```

Sets `Set-Cookie: inspira_session=...; HttpOnly; SameSite=Lax; Max-Age=2592000; Path=/`.
`Secure` attribute is toggled by `INSPIRA_COOKIE_SECURE`.

**Errors:**
- `409 {"error": "email_in_use"}` — duplicate email.
- `422` — missing or malformed email / password.

### `POST /api/auth/login`

Verify credentials, sign in, set the session cookie.

**Request:**

```json
{ "email": "alice@example.com", "password": "..." }
```

**Response 200:** `AuthedUser` (same shape as signup).

**Errors:**
- `401 {"error": "invalid_credentials"}` — unknown email, wrong
  password, or the account has `password_hash = NULL` (OAuth-only or
  system user). The same message is returned for all cases so callers
  cannot distinguish them.

### `POST /api/auth/logout`

Clear the session cookie.

**Response 200:** `{"logged_out": true}`

### `GET /api/auth/me`

Resolve the current session. Never 401 — falls back to the system user
(`is_system: true`) when no cookie is present or the cookie is
invalid/expired.

**Response 200:** `AuthedUser`

When unauthenticated:

```json
{
  "user_id": "user-system",
  "email": "system@inspira.local",
  "display_name": "System",
  "is_system": true
}
```

### `GET /api/auth/google/start` — planned

**Currently returns 501.** When `GOOGLE_OAUTH_CLIENT_ID` is unset:

```json
{
  "error": "google_oauth_not_configured",
  "hint": "Set GOOGLE_OAUTH_CLIENT_ID, GOOGLE_OAUTH_CLIENT_SECRET, GOOGLE_OAUTH_REDIRECT_URI."
}
```

When configured, returns 501 with
`{"error": "google_oauth_unimplemented"}` — the full implementation is
pending.

### `GET /api/auth/google/callback` — planned

Same as above: 501, `{"error": "google_oauth_unimplemented"}`.

---

## v2 — Projects

### `GET /api/v2/projects`

List all active projects owned by the current user, sorted by
`updated_at DESC`.

**Response 200:**

```json
{
  "projects": [
    {
      "project_id": "project-abc123...",
      "user_id": "user-abc...",
      "title": "Small outdoor wine festival",
      "metadata": {},
      "created_at": "2026-04-21T12:00:00+00:00",
      "updated_at": "2026-04-21T12:05:00+00:00"
    }
  ]
}
```

### `POST /api/v2/projects`

Create a new v2 project.

**Request:** `{ "title": "Optional project name" }` — empty / missing
title defaults to `"Untitled project"`.

**Response 201:**

```json
{
  "project": {
    "project_id": "project-...",
    "user_id": "user-...",
    "title": "...",
    "metadata": {},
    "created_at": "...",
    "updated_at": "..."
  }
}
```

Side effect: invalidates the user's cached AI project suggestions.

### `POST /api/v2/projects/{project_id}/update`

Rename a project (the only supported update today).

**Request:** `{ "title": "New title" }`

**Response 200:** `{ "project": { ... } }`

**Errors:**
- `400 "no valid fields to update"` — request body was empty.
- `404 {"error": "project_not_found"}` — missing or not owned by user.

### `POST /api/v2/projects/{project_id}/delete`

Soft-delete a project. Cascades to soft-delete its topics and
relationships.

**Response 200:** `{ "deleted": true, "project_id": "..." }`

**Errors:**
- `404 {"error": "project_not_found"}` — missing or not owned.

### `POST /api/v2/projects/suggest`

Get AI project suggestions for the "what to plan next" screen.

Returns an empty list when the caller is the system user (no real
user), or when they have fewer than 2 active projects. Otherwise reads
from the `suggestions_cache` (4-hour TTL). On a cache miss, runs the
`project_suggestions` LLM call and persists the result.

Privacy contract: only project titles, topic titles, and confirmed
decision statements are sent in the prompt. Never Q&A bodies or
attachment excerpts.

**Response 200:**

```json
{
  "suggestions": [
    {
      "title": "Short kickoff label",
      "why_this": "1-2 sentences grounded in the user's portfolio pattern.",
      "example_idea": "Concrete 1-2 sentence seed the user can paste."
    }
  ]
}
```

**Errors:**
- `429` — daily token budget exhausted on a cache miss.
- `500 {"error": "planner_call_failed", "request_id": "..."}` — LLM
  call failed.

### `POST /api/v2/projects/{project_id}/kickoff`

Run the kickoff planner call: map the user's idea into 5-10 topic cards
+ relationships.

**Request:**

```json
{
  "user_idea": "I'm planning a small outdoor wine festival for 200 people.",
  "attached_sources": [
    { "display_name": "...", "kind": "file:text", "excerpt": "..." }
  ]
}
```

- `user_idea` — required, non-empty, max 8000 chars.
- `attached_sources` — optional, max 10 entries, each excerpt max 20000
  chars, each display_name max 500 chars.

**Response 201:**

```json
{
  "kickoff": {
    "domain": "event",
    "domain_confidence": "high",
    "opening_card": { "body": "Five topics. Start with Venue." },
    "topics": [
      { "title": "Venue", "icon": "map-pin", "why_this_topic": "..." }
    ],
    "relationships": [
      { "from_topic_title": "Venue", "to_topic_title": "Safety", "label": "requires" }
    ],
    "suggested_first_topic": "Venue",
    "clarifying_question_if_too_vague": null,
    "_sanitize": { "dropped_relationships": [], "suggested_first_fallback": null, "auto_connected_orphans": [] }
  },
  "topics": [
    {
      "topic_id": "topic-...",
      "project_id": "project-...",
      "title": "Venue",
      "icon": "map-pin",
      "position_x": 0.0,
      "position_y": 0.0,
      "status": "empty",
      "order_index": 0,
      "origin": "planner_initial",
      "metadata": { "why_this_topic": "..." },
      "created_at": "...",
      "updated_at": "...",
      "deleted_at": null
    }
  ],
  "relationships": [
    {
      "relationship_id": "rel-...",
      "project_id": "project-...",
      "source_topic_id": "topic-...",
      "target_topic_id": "topic-...",
      "label": "requires",
      "origin": "planner_inferred",
      "strength": "confirmed",
      "created_at": "...",
      "deleted_at": null
    }
  ]
}
```

Side effects:
- Creates the project if missing (`ensure_project`).
- Persists topics and relationships.
- Records token usage against the user's daily budget.

**Errors:**
- `400 "user_idea is required"`.
- `404 {"error": "project_not_found"}` — project owned by another user.
- `422` — payload validation.
- `429` — daily token budget exhausted. Includes `Retry-After`.
- `500 {"error": "planner_call_failed", "request_id": "..."}` — planner
  call failed or sanitizer raised. Correlation id for the server log.

---

## v2 — Topics

### `GET /api/v2/projects/{project_id}/topics`

List active topics in a project. Ordered by `order_index, created_at`.

**Response 200:** `{ "topics": [Topic, ...] }` (shape as above).

**Errors:**
- `404 {"error": "project_not_found"}`.

### `POST /api/v2/projects/{project_id}/topics`

Create a topic manually.

**Request:**

```json
{
  "title": "New topic",
  "icon": "flag",
  "position_x": 0.0,
  "position_y": 0.0
}
```

- `title` — required, 1-200 chars.
- `icon` — optional, defaults to `"flag"`. Must be in the curated icon
  set.
- `position_x`, `position_y` — optional, default 0.0.

**Response 201:** `{ "topic": Topic }`

**Errors:**
- `400 "title is required"`.
- `404 {"error": "project_not_found"}`.

### `POST /api/v2/topics/{topic_id}/update`

Update a topic. Partial update — send only the fields you want to
change.

**Request:**

```json
{
  "title": "...",
  "icon": "...",
  "position_x": 200.0,
  "position_y": 100.0,
  "status": "in_progress"
}
```

All fields optional. `title` max 200, `icon` max 40, `status` max 40.

**Response 200:** `{ "topic": Topic }`

**Errors:**
- `400 "no valid fields to update"` — empty patch.
- `404 {"error": "topic_not_found"}`.

### `POST /api/v2/topics/{topic_id}/delete`

Soft-delete a topic. Cascades to soft-delete any relationship touching
it. Q&A turns and decisions are preserved.

**Response 200:** `{ "deleted": true, "topic_id": "..." }`

**Errors:**
- `404 {"error": "topic_not_found"}`.

---

## v2 — Decisions

### `GET /api/v2/topics/{topic_id}/decisions`

List non-retracted decisions on a topic.

**Response 200:** `{ "decisions": [Decision, ...] }`.

Decision shape:

```json
{
  "decision_id": "dec-...",
  "topic_id": "topic-...",
  "project_id": "project-...",
  "statement": "...",
  "rationale": "... or null",
  "status": "proposed | confirmed | retracted",
  "source_turn_id": "turn-... or null",
  "proposed_by": "planner | user",
  "confirmed_by_user_id": "user-... or null",
  "created_at": "...",
  "updated_at": "...",
  "retracted_at": "... or null"
}
```

**Errors:**
- `404 {"error": "topic_not_found"}`.

### `POST /api/v2/topics/{topic_id}/decisions`

Create a decision on a topic.

**Request:**

```json
{
  "statement": "We will use X.",
  "rationale": "Because Y.",
  "source_turn_id": "turn-... or null",
  "proposed_by": "planner | user",
  "status": "proposed | confirmed | retracted"
}
```

Defaults: `proposed_by="planner"`, `status="confirmed"`. Statement
max 2000 chars; rationale max 4000.

**Response 201:** `{ "decision": Decision }`.

**Errors:**
- `400 "statement is required"`.
- `404 {"error": "topic_not_found"}`.

### `GET /api/v2/projects/{project_id}/decisions`

List all non-retracted decisions across all topics in a project.

**Response 200:** `{ "decisions": [Decision, ...] }`.

**Errors:**
- `404 {"error": "project_not_found"}`.

### `POST /api/v2/decisions/{decision_id}/delete`

Retract a decision (status → `retracted`; row is preserved). Hidden
from all list endpoints.

**Response 200:** `{ "deleted": true, "decision_id": "..." }`

**Errors:**
- `404 {"error": "decision_not_found"}` — missing or not owned.
- `404 {"error": "decision_not_found_or_already_retracted"}` — race
  with a prior delete.

---

## v2 — Relationships

### `GET /api/v2/projects/{project_id}/relationships`

List active relationships in a project.

**Response 200:** `{ "relationships": [Relationship, ...] }`

Relationship shape:

```json
{
  "relationship_id": "rel-...",
  "project_id": "project-...",
  "source_topic_id": "topic-...",
  "target_topic_id": "topic-...",
  "label": "requires",
  "origin": "planner_inferred | user_drawn",
  "strength": "confirmed | implied | null",
  "created_at": "...",
  "deleted_at": null
}
```

**Errors:**
- `404 {"error": "project_not_found"}`.

### `POST /api/v2/projects/{project_id}/relationships`

Create a relationship.

**Request:**

```json
{
  "source_topic_id": "topic-...",
  "target_topic_id": "topic-...",
  "label": "blocks"
}
```

- Both topic IDs required.
- `label` optional, 1-120 chars. (Planner-set labels follow the "short
  verb phrase" rule; user-drawn is free text.)
- Self-loops rejected.

**Response 201:** `{ "relationship": Relationship }`

**Errors:**
- `400 "source_topic_id and target_topic_id are required"`.
- `400 "a relationship cannot connect a topic to itself"`.
- `404 {"error": "project_not_found"}` / `{"error": "topic_not_found"}`.

### `POST /api/v2/relationships/{relationship_id}/delete`

Soft-delete a relationship.

**Response 200:** `{ "deleted": true, "relationship_id": "..." }`

**Errors:**
- `404 {"error": "relationship_not_found"}`.

---

## v2 — Q&A turns

### `POST /api/v2/topics/{topic_id}/turn`

Run one planner turn inside a topic's Q&A thread.

**Request:**

```json
{
  "user_answer": "Around 200 guests, outdoor in July.",
  "attached_sources": [{ "display_name": "...", "kind": "...", "excerpt": "..." }]
}
```

- `user_answer` — optional, max 8000 chars. When present, persisted as
  a `role=user` turn before the planner is called.
- `attached_sources` — optional, max 10 entries.

Behavior:

1. Persist the user's answer (if any) as a new `qna_turns` row.
2. Gather full context: current topic + decisions + all turns + other
   topics + their decisions.
3. Call the planner (`topic_turn` mode).
4. When the planner's action is `suggest_close`, skip persisting a
   planner turn. Otherwise persist the planner's question as a new
   `qna_turns` row.
5. Return both the raw planner response and the persisted turn row.

**Response 201:**

```json
{
  "turn_result": {
    "action": "ask | pressure_test | followup | suggest_close",
    "question": "... or null (null when suggest_close)",
    "why_this_matters": "... or null",
    "suggested_responses": [{ "label": "...", "intent": "..." }],
    "proposed_decisions": [{ "statement": "...", "rationale": "... or null", "extracted_from_turn_id": "turn-..." }],
    "consistency_flags": [{ "other_topic_title": "...", "other_decision_id": "...", "description": "..." }],
    "new_topic_proposal": null,
    "close_recommendation_reason": "... or null",
    "_sanitize": { "dropped_consistency_flags": [] }
  },
  "planner_turn": {
    "turn_id": "turn-...",
    "topic_id": "topic-...",
    "project_id": "project-...",
    "role": "planner",
    "order_index": 7,
    "body": "...",
    "why_this_matters": "... or null",
    "action": "ask",
    "suggested_responses": [...],
    "status": "open",
    "parent_turn_id": null,
    "attachments": [],
    "created_at": "..."
  }
}
```

`planner_turn` is `null` when `action == "suggest_close"`.

**Errors:**
- `404 {"error": "topic_not_found"}`.
- `422` — payload validation.
- `429` — daily token budget exhausted.
- `500 {"error": "planner_call_failed", "request_id": "..."}`.

### `GET /api/v2/topics/{topic_id}/turns`

List all Q&A turns on a topic, ordered by `order_index`.

**Response 200:** `{ "turns": [QnaTurn, ...] }`.

**Errors:**
- `404 {"error": "topic_not_found"}`.

---

## v1 — deprecated

These endpoints power the original Planning Studio triplet (projects /
sessions / artifacts). Kept for backward compatibility with
`services/tests/test_service.py` and any legacy client. Do not build
new functionality against them.

### `GET /api/projects`

**Response 200:** `{ "projects": [LegacyProject, ...] }`

### `GET /api/sessions?project_id=...`

**Response 200:** `{ "sessions": [LegacySession, ...] }`

### `POST /api/sessions`

Create a legacy session + transcript file at
`<storage_root>/sessions/<session_id>.md`.

**Request:**

```json
{
  "project_id": "...",
  "title": "...",
  "objective": "...",
  "mode": "interview"
}
```

**Response 201:** `{ "session": LegacySession }`.

### `GET /api/artifacts?project_id=...`

**Response 200:** `{ "artifacts": [LegacyArtifact, ...] }`

---

## OpenAPI

A checked-in OpenAPI 3.1 spec for the same surface lives at
`docs/api/openapi.yaml`. FastAPI also serves a live copy at
`/openapi.json` (and Swagger UI at `/docs`, ReDoc at `/redoc`) when the
app is running.
