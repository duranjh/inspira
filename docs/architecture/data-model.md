# Data Model

Complete schema reference for the Inspira backend store. SQLite is the
default; Postgres is supported when `DATABASE_URL` is set. Every table
and index below is created by either:

- `services/planning_studio_service/store.py` at service boot (legacy
  bootstrap path; `CREATE TABLE IF NOT EXISTS` everywhere), OR
- `services/alembic/versions/20260421_0001_baseline.py` via
  `alembic upgrade head` (authoritative path going forward).

Both paths are idempotent against each other. A separate design doc at
`docs/product/architecture/data-model.md` describes the aspirational
model with workspaces, privacy flags, and other fields that are not yet
in the live schema — this document captures the **current state** only.

## Table of contents

- [v2 domain tables](#v2-domain-tables) (live in the UI)
  - [`v2_projects`](#v2_projects)
  - [`topics`](#topics)
  - [`relationships`](#relationships)
  - [`qna_turns`](#qna_turns)
  - [`decisions`](#decisions)
  - [`open_questions`](#open_questions)
  - [`risks_assumptions`](#risks_assumptions)
  - [`consistency_flags`](#consistency_flags)
  - [`summary_versions`](#summary_versions)
  - [`approval_actions`](#approval_actions)
  - [`context_sources`](#context_sources)
  - [`source_references`](#source_references)
- [Identity + auth](#identity-and-auth)
  - [`users`](#users)
- [Operational tables](#operational-tables)
  - [`user_usage`](#user_usage)
  - [`suggestions_cache`](#suggestions_cache)
  - [`audit_log`](#audit_log)
  - [`schema_version`](#schema_version)
- [v1 deprecated tables](#v1-deprecated-tables) (kept for legacy tests)
  - [`projects`](#projects)
  - [`sessions`](#sessions)
  - [`artifacts`](#artifacts)
- [Data ownership](#data-ownership)
- [Soft-delete semantics](#soft-delete-semantics)
- [Schema provenance](#schema-provenance)

## v2 domain tables

### `v2_projects`

One row per Inspira project owned by a real user. Replaces the v1
`projects` table for all new code paths.

Source: `services/planning_studio_service/store.py:479`

| column | type | nullable | default | purpose |
|---|---|---|---|---|
| `project_id` | `TEXT` | no | - | Primary key. Format `project-<12hex>` (`store.py:626`). |
| `user_id` | `TEXT` | no | - | Owner. FK-like reference to `users.user_id`. Not declared as a FOREIGN KEY at the SQL level because `user-system` is a sentinel. |
| `title` | `TEXT` | no | - | Human-visible project name. Default `"Project <last6>"` when not supplied. |
| `metadata_json` | `TEXT` | no | `'{}'` | Extensible JSON blob. Do not put queryable fields here. |
| `created_at` | `TEXT` | no | - | ISO 8601 UTC. |
| `updated_at` | `TEXT` | no | - | ISO 8601 UTC, bumped on every mutation. |
| `deleted_at` | `TEXT` | yes | `NULL` | Soft-delete timestamp. `NULL` means active. |

Indexes:
- `idx_v2_projects_user` on `(user_id)` — powers
  `GET /api/v2/projects`.

### `topics`

A topic card on the canvas. Freeform; the schema is identical for all
topics regardless of domain.

Source: `services/planning_studio_service/store.py:104`

| column | type | nullable | default | purpose |
|---|---|---|---|---|
| `topic_id` | `TEXT` | no | - | PK. Format `topic-<10hex>`. |
| `project_id` | `TEXT` | no | - | FK to `projects.project_id` (declared). In practice new rows reference `v2_projects.project_id`. |
| `title` | `TEXT` | no | - | Serif display title, 1-3 words ideal. |
| `icon` | `TEXT` | no | - | Curated icon id. Enum in `agents/prompts.py:CURATED_ICONS`. |
| `position_x` | `REAL` | no | - | Canvas X coordinate (React Flow world units). |
| `position_y` | `REAL` | no | - | Canvas Y coordinate. |
| `status` | `TEXT` | no | - | `empty`, `in_progress`, or `fleshed_out`. |
| `order_index` | `INTEGER` | no | - | Stable ordering within a project. |
| `origin` | `TEXT` | no | - | `planner_initial`, `planner_proposed`, or `user_manual`. |
| `metadata_json` | `TEXT` | no | `'{}'` | JSON blob; today carries `why_this_topic`. |
| `created_at` | `TEXT` | no | - | ISO 8601 UTC. |
| `updated_at` | `TEXT` | no | - | ISO 8601 UTC. |
| `deleted_at` | `TEXT` | yes | `NULL` | Soft-delete timestamp. |
| `user_id` | `TEXT` | no | `'user-system'` | Retrofitted by `_ensure_user_id_columns`. New rows carry the creating user. |

Indexes:
- `idx_topics_project` on `(project_id, deleted_at)`.
- `idx_topics_status` on `(project_id, status)`.

Foreign keys: `project_id` → `projects(project_id)`.

### `relationships`

Directed dotted edges between topics. Every edge carries a short verb
phrase label.

Source: `services/planning_studio_service/store.py:137`

| column | type | nullable | default | purpose |
|---|---|---|---|---|
| `relationship_id` | `TEXT` | no | - | PK. Format `rel-<10hex>`. |
| `project_id` | `TEXT` | no | - | FK to `projects.project_id`. |
| `source_topic_id` | `TEXT` | no | - | FK to `topics.topic_id`. |
| `target_topic_id` | `TEXT` | no | - | FK to `topics.topic_id`. |
| `label` | `TEXT` | yes | `NULL` | Short verb phrase, 1-3 words (see prompt). Required by the planner, nullable in SQL to keep old rows migratable. |
| `origin` | `TEXT` | no | - | `planner_inferred` or `user_drawn`. |
| `strength` | `TEXT` | yes | `NULL` | `implied` or `confirmed`. Planner sets `confirmed` by default. |
| `created_at` | `TEXT` | no | - | ISO 8601 UTC. |
| `deleted_at` | `TEXT` | yes | `NULL` | Soft-delete timestamp. |
| `user_id` | `TEXT` | no | `'user-system'` | Retrofitted. |

Unique: `(project_id, source_topic_id, target_topic_id)` — prevents
duplicate edges.

Indexes:
- `idx_relationships_project` on `(project_id)`.

Foreign keys: `project_id` → `projects(project_id)`,
`source_topic_id` → `topics(topic_id)`,
`target_topic_id` → `topics(topic_id)`.

### `qna_turns`

Append-only transcript of planner questions and user answers within a
topic. One row per turn.

Source: `services/planning_studio_service/store.py:155`

| column | type | nullable | default | purpose |
|---|---|---|---|---|
| `turn_id` | `TEXT` | no | - | PK. Format `turn-<10hex>`. |
| `topic_id` | `TEXT` | no | - | FK to `topics.topic_id`. |
| `project_id` | `TEXT` | no | - | Denormalized for query speed. |
| `role` | `TEXT` | no | - | `planner` or `user`. |
| `order_index` | `INTEGER` | no | - | Zero-based position within this topic. Assigned by `append_qna_turn` via `MAX(order_index)+1`. |
| `body` | `TEXT` | no | - | The question or answer text. |
| `why_this_matters` | `TEXT` | yes | `NULL` | Planner-only. Short annotation shown under the question. |
| `action` | `TEXT` | yes | `NULL` | Planner-only. One of `ask`, `pressure_test`, `followup`, `suggest_close`. |
| `suggested_responses_json` | `TEXT` | yes | `NULL` | Planner-only. JSON list of `{label, intent}`. |
| `status` | `TEXT` | no | - | `open`, `answered`, `deferred`, or `na`. |
| `parent_turn_id` | `TEXT` | yes | `NULL` | Self-reference for follow-up threads. |
| `attachments_json` | `TEXT` | yes | `NULL` | JSON list of attachment stubs referenced on this turn. |
| `created_at` | `TEXT` | no | - | ISO 8601 UTC. |
| `user_id` | `TEXT` | no | `'user-system'` | Retrofitted. |

Indexes:
- `idx_qna_topic_order` on `(topic_id, order_index)`.
- `idx_qna_project_role` on `(project_id, role)`.

Foreign keys: `topic_id` → `topics(topic_id)`,
`project_id` → `projects(project_id)`.

### `decisions`

Decisions attached to topics. Mutable; retractions use a status flip
rather than a row delete so the audit trail survives.

Source: `services/planning_studio_service/store.py:176`

| column | type | nullable | default | purpose |
|---|---|---|---|---|
| `decision_id` | `TEXT` | no | - | PK. Format `dec-<10hex>`. |
| `topic_id` | `TEXT` | no | - | FK to `topics.topic_id`. |
| `project_id` | `TEXT` | no | - | FK to `projects.project_id`. |
| `statement` | `TEXT` | no | - | The decision prose. |
| `rationale` | `TEXT` | yes | `NULL` | Why the decision. |
| `status` | `TEXT` | no | - | `proposed`, `confirmed`, or `retracted`. Retracted rows are hidden from `list_decisions`. |
| `source_turn_id` | `TEXT` | yes | `NULL` | FK to `qna_turns.turn_id` — the turn this decision was extracted from. |
| `proposed_by` | `TEXT` | no | - | `planner` or `user`. |
| `confirmed_by_user_id` | `TEXT` | yes | `NULL` | The user who confirmed a proposed decision. |
| `created_at` | `TEXT` | no | - | ISO 8601 UTC. |
| `updated_at` | `TEXT` | no | - | ISO 8601 UTC. |
| `retracted_at` | `TEXT` | yes | `NULL` | Set when status → `retracted`. |
| `user_id` | `TEXT` | no | `'user-system'` | Retrofitted. |

Indexes:
- `idx_decisions_topic` on `(topic_id, status)`.
- `idx_decisions_project` on `(project_id)`.

Foreign keys: `topic_id` → `topics(topic_id)`,
`project_id` → `projects(project_id)`,
`source_turn_id` → `qna_turns(turn_id)`.

### `open_questions`

Unanswered questions per topic. Created when the planner flags an
unresolved thread; closed when answered.

Source: `services/planning_studio_service/store.py:197`

| column | type | nullable | default | purpose |
|---|---|---|---|---|
| `question_id` | `TEXT` | no | - | PK. |
| `topic_id` | `TEXT` | no | - | FK to `topics.topic_id`. |
| `project_id` | `TEXT` | no | - | Denormalized. |
| `text` | `TEXT` | no | - | The open question. |
| `status` | `TEXT` | no | - | `open`, `answered`, `deferred`, or `na`. |
| `answer_turn_id` | `TEXT` | yes | `NULL` | FK to the answering turn, when closed. |
| `created_at` | `TEXT` | no | - | ISO 8601 UTC. |
| `updated_at` | `TEXT` | no | - | ISO 8601 UTC. |

No dedicated indexes today. Foreign keys: `topic_id` → `topics(topic_id)`.

### `risks_assumptions`

Risks and assumptions flagged on a topic. Store-only today; no HTTP
surface yet.

Source: `services/planning_studio_service/store.py:210`

| column | type | nullable | default | purpose |
|---|---|---|---|---|
| `risk_id` | `TEXT` | no | - | PK. |
| `topic_id` | `TEXT` | no | - | FK to `topics.topic_id`. |
| `project_id` | `TEXT` | no | - | Denormalized. |
| `kind` | `TEXT` | no | - | `risk` or `assumption`. |
| `text` | `TEXT` | no | - | The flagged item. |
| `severity` | `TEXT` | yes | `NULL` | `low`, `medium`, `high`, or `critical` (risks only). |
| `status` | `TEXT` | no | - | `open`, `resolved`, or `invalidated`. |
| `created_at` | `TEXT` | no | - | ISO 8601 UTC. |
| `updated_at` | `TEXT` | no | - | ISO 8601 UTC. |

Foreign keys: `topic_id` → `topics(topic_id)`.

### `consistency_flags`

Cross-topic contradictions surfaced by the planner when a new decision
clashes with a previously-confirmed one.

Source: `services/planning_studio_service/store.py:224`

| column | type | nullable | default | purpose |
|---|---|---|---|---|
| `flag_id` | `TEXT` | no | - | PK. Format `flag-<10hex>`. |
| `project_id` | `TEXT` | no | - | Scope. |
| `topic_a_id` | `TEXT` | no | - | One side of the conflict. |
| `decision_a_id` | `TEXT` | yes | `NULL` | Optional specific decision on side A. |
| `topic_b_id` | `TEXT` | no | - | Other side. |
| `decision_b_id` | `TEXT` | yes | `NULL` | Optional specific decision on side B. |
| `description` | `TEXT` | no | - | One-sentence description of the clash. |
| `scope` | `TEXT` | no | - | `within_project` or `cross_project`. |
| `status` | `TEXT` | no | - | `open`, `resolved`, `intentional`, or `dismissed`. |
| `resolved_turn_id` | `TEXT` | yes | `NULL` | The turn that resolved the flag. |
| `created_at` | `TEXT` | no | - | ISO 8601 UTC. |
| `updated_at` | `TEXT` | no | - | ISO 8601 UTC. |
| `user_id` | `TEXT` | no | `'user-system'` | Retrofitted. |

Indexes:
- `idx_flags_project_status` on `(project_id, status)`.
- `idx_flags_topic_a` on `(topic_a_id)`.
- `idx_flags_topic_b` on `(topic_b_id)`.

### `summary_versions`

Append-only versioned Plan Summary. Each regeneration produces a new
row; older rows stay for history.

Source: `services/planning_studio_service/store.py:259`

| column | type | nullable | default | purpose |
|---|---|---|---|---|
| `version_id` | `TEXT` | no | - | PK. Format `sum-<10hex>`. |
| `project_id` | `TEXT` | no | - | FK. |
| `version_hash` | `TEXT` | no | - | Content hash for dedup. |
| `content_markdown` | `TEXT` | no | - | Rendered Summary in Markdown. |
| `sections_json` | `TEXT` | no | - | JSON array of `{header, body, cited_*}`. |
| `open_questions_json` | `TEXT` | yes | `NULL` | JSON array of open-question strings. |
| `approval_state` | `TEXT` | no | - | `draft`, `under_review`, or `approved`. |
| `generated_by` | `TEXT` | no | - | `planner_auto` or `user_edit`. |
| `generated_by_user_id` | `TEXT` | yes | `NULL` | The user who triggered this version. |
| `version_note` | `TEXT` | yes | `NULL` | One-sentence note on what changed. |
| `created_at` | `TEXT` | no | - | ISO 8601 UTC. |
| `user_id` | `TEXT` | no | `'user-system'` | Retrofitted. |

Indexes:
- `idx_summary_versions_project` on `(project_id, created_at DESC)`.

Foreign keys: `project_id` → `projects(project_id)`.

### `approval_actions`

Append-only ledger of approve/deny/request/cancel events against a
Summary version. Reserved for the team-plan-with-approvals flow; no HTTP
routes today.

Source: `services/planning_studio_service/store.py:277`

| column | type | nullable | default | purpose |
|---|---|---|---|---|
| `action_id` | `TEXT` | no | - | PK. |
| `project_id` | `TEXT` | no | - | Scope. |
| `summary_version_id` | `TEXT` | no | - | FK to `summary_versions.version_id`. |
| `actor_user_id` | `TEXT` | no | - | Who took the action. |
| `outcome` | `TEXT` | no | - | `approve`, `deny`, `request`, or `cancel`. |
| `comment` | `TEXT` | yes | `NULL` | Free-text justification. |
| `state_before` | `TEXT` | yes | `NULL` | Prior `approval_state`. |
| `state_after` | `TEXT` | yes | `NULL` | New `approval_state`. |
| `ip_address` | `TEXT` | yes | `NULL` | Best-effort source IP. |
| `session_id` | `TEXT` | yes | `NULL` | Correlator. |
| `created_at` | `TEXT` | no | - | ISO 8601 UTC. |

Indexes:
- `idx_approvals_project` on `(project_id, created_at DESC)`.
- `idx_approvals_version` on `(summary_version_id)`.

Foreign keys: `summary_version_id` → `summary_versions(version_id)`.

### `context_sources`

User-attached context items (uploaded files, pasted URLs, repo handles).
Attached at the project level, cited per turn/decision through
`source_references`.

Source: `services/planning_studio_service/store.py:243`

| column | type | nullable | default | purpose |
|---|---|---|---|---|
| `source_id` | `TEXT` | no | - | PK. |
| `project_id` | `TEXT` | no | - | FK. |
| `kind` | `TEXT` | no | - | `upload`, `url`, `github_repo`, or `gitlab_repo`. |
| `display_name` | `TEXT` | no | - | User-visible label. |
| `uri` | `TEXT` | yes | `NULL` | Resolvable reference when applicable. |
| `metadata_json` | `TEXT` | no | `'{}'` | Provider-specific data. |
| `status` | `TEXT` | no | - | `active`, `stale`, or `unreachable`. |
| `added_by_user_id` | `TEXT` | no | - | Who attached it. |
| `created_at` | `TEXT` | no | - | ISO 8601 UTC. |
| `updated_at` | `TEXT` | no | - | ISO 8601 UTC. |

Indexes:
- `idx_sources_project` on `(project_id, status)`.

Foreign keys: `project_id` → `projects(project_id)`.

### `source_references`

Which turn / decision / topic cites which `context_sources` row.

Source: `services/planning_studio_service/store.py:259`

| column | type | nullable | default | purpose |
|---|---|---|---|---|
| `reference_id` | `TEXT` | no | - | PK. |
| `source_id` | `TEXT` | no | - | FK to `context_sources.source_id`. |
| `project_id` | `TEXT` | no | - | Denormalized. |
| `topic_id` | `TEXT` | yes | `NULL` | Optional scope. |
| `turn_id` | `TEXT` | yes | `NULL` | Optional scope. |
| `decision_id` | `TEXT` | yes | `NULL` | Optional scope. |
| `citation_detail` | `TEXT` | yes | `NULL` | Page / line / anchor when applicable. |
| `created_at` | `TEXT` | no | - | ISO 8601 UTC. |

Foreign keys: `source_id` → `context_sources(source_id)`.

## Identity and auth

### `users`

Minimum-viable identity record. Google OAuth profile data will layer on
via `metadata_json` without a schema change.

Source: `services/planning_studio_service/store.py:394`

| column | type | nullable | default | purpose |
|---|---|---|---|---|
| `user_id` | `TEXT` | no | - | PK. Format `user-<12hex>`; sentinel `user-system` for the pre-auth fallback. |
| `email` | `TEXT` | no | - | UNIQUE. Lowercased on write. |
| `password_hash` | `TEXT` | yes | `NULL` | argon2-hashed. `NULL` for OAuth-only or the system user. |
| `display_name` | `TEXT` | yes | `NULL` | Default = left side of email. |
| `metadata_json` | `TEXT` | no | `'{}'` | Extensible; reserved for OAuth profile. |
| `created_at` | `TEXT` | no | - | ISO 8601 UTC. |
| `updated_at` | `TEXT` | no | - | ISO 8601 UTC. |

Indexes:
- `idx_users_email` on `(email)` — covers the `/api/auth/login` lookup.

## Operational tables

### `user_usage`

Per-user daily token accounting. Enforces the
`INSPIRA_USER_DAILY_TOKEN_BUDGET` cap in `api.py:408`.

Source: `services/planning_studio_service/store.py:337`

| column | type | nullable | default | purpose |
|---|---|---|---|---|
| `user_id` | `TEXT` | no | - | Part of composite PK. |
| `day_utc` | `TEXT` | no | - | Part of composite PK. `YYYY-MM-DD` in UTC. |
| `tokens_in` | `INTEGER` | no | `0` | Prompt tokens consumed today. |
| `tokens_out` | `INTEGER` | no | `0` | Completion tokens produced today. |
| `request_count` | `INTEGER` | no | `0` | Observability. |
| `updated_at` | `TEXT` | no | - | ISO 8601 UTC. |

Primary key: `(user_id, day_utc)`.

Indexes:
- `idx_user_usage_day` on `(day_utc)` — supports housekeeping sweeps.

### `suggestions_cache`

One-row-per-user cache for the AI project suggestions call. TTL enforced
in application code (`api.py:71` `SUGGESTIONS_CACHE_TTL_SECONDS`, 4
hours). Invalidated on project create/delete.

Source: `services/planning_studio_service/store.py:351`

| column | type | nullable | default | purpose |
|---|---|---|---|---|
| `user_id` | `TEXT` | no | - | PK. |
| `suggestions_json` | `TEXT` | no | - | JSON array of `{title, why_this, example_idea}`. |
| `generated_at` | `TEXT` | no | - | ISO 8601 UTC. |

### `audit_log`

Comprehensive event log. Append-only. Not yet wired from routes — the
`append_audit_event` helper exists but the handlers do not call it.

Source: `services/planning_studio_service/store.py:297`

| column | type | nullable | default | purpose |
|---|---|---|---|---|
| `event_id` | `TEXT` | no | - | PK. Format `evt-<10hex>`. |
| `workspace_id` | `TEXT` | no | - | Reserved for multi-workspace deployment. |
| `project_id` | `TEXT` | yes | `NULL` | Scope. |
| `actor_user_id` | `TEXT` | no | - | Who did it. |
| `category` | `TEXT` | no | - | Coarse event category. |
| `action` | `TEXT` | no | - | Specific action name. |
| `subject_id` | `TEXT` | yes | `NULL` | Target entity. |
| `before_json` | `TEXT` | yes | `NULL` | Pre-image JSON. |
| `after_json` | `TEXT` | yes | `NULL` | Post-image JSON. |
| `ip_address` | `TEXT` | yes | `NULL` | Source IP. |
| `session_id` | `TEXT` | yes | `NULL` | Correlator. |
| `created_at` | `TEXT` | no | - | ISO 8601 UTC. |

Indexes:
- `idx_audit_workspace` on `(workspace_id, created_at DESC)`.
- `idx_audit_project` on `(project_id, created_at DESC)`.
- `idx_audit_actor` on `(actor_user_id, created_at DESC)`.
- `idx_audit_category` on `(category, action)`.

### `schema_version`

Single-row marker for the shipped schema.

Source: `services/planning_studio_service/store.py:358`

| column | type | nullable | default | purpose |
|---|---|---|---|---|
| `version` | `INTEGER` | no | - | PK. Current value: `2`. |
| `applied_at` | `TEXT` | no | - | Timestamp at insert. |
| `description` | `TEXT` | no | - | Human note, e.g. `"v2 canvas-first schema..."`. |

## v1 deprecated tables

These tables are still created on service boot for backward compatibility
with existing unit tests (`services/tests/test_service.py`) and the
legacy `/api/projects`, `/api/sessions`, `/api/artifacts` endpoints. Do
not read or write from new code paths. Scheduled for removal once the
legacy test surface is retired.

### `projects`

Source: `services/planning_studio_service/store.py:42`

| column | type | nullable | default |
|---|---|---|---|
| `project_id` | `TEXT` PK | no | - |
| `title` | `TEXT` | no | - |
| `summary` | `TEXT` | no | - |
| `stage` | `TEXT` | no | - |
| `owner` | `TEXT` | no | - |
| `metadata_json` | `TEXT` | no | - |
| `created_at` | `TEXT` | no | - |
| `updated_at` | `TEXT` | no | - |

Purpose: v1 project row. Seeded with a single
`project-second-brain-commercialization` row at boot
(`store.py:369`).

### `sessions`

Source: `services/planning_studio_service/store.py:53`

| column | type | nullable | default |
|---|---|---|---|
| `session_id` | `TEXT` PK | no | - |
| `project_id` | `TEXT` | no | - |
| `title` | `TEXT` | no | - |
| `objective` | `TEXT` | no | - |
| `status` | `TEXT` | no | - |
| `transcript_path` | `TEXT` | yes | `NULL` |
| `metadata_json` | `TEXT` | no | - |
| `created_at` | `TEXT` | no | - |
| `updated_at` | `TEXT` | no | - |

Foreign keys: `project_id` → `projects(project_id)`.

Purpose: v1 interview session. Transcripts land on disk under
`<storage_root>/sessions/<session_id>.md`.

### `artifacts`

Source: `services/planning_studio_service/store.py:66`

| column | type | nullable | default |
|---|---|---|---|
| `artifact_id` | `TEXT` PK | no | - |
| `project_id` | `TEXT` | no | - |
| `session_id` | `TEXT` | yes | `NULL` |
| `artifact_type` | `TEXT` | no | - |
| `title` | `TEXT` | no | - |
| `status` | `TEXT` | no | - |
| `artifact_path` | `TEXT` | yes | `NULL` |
| `metadata_json` | `TEXT` | no | - |
| `created_at` | `TEXT` | no | - |
| `updated_at` | `TEXT` | no | - |

Foreign keys: `project_id` → `projects(project_id)`,
`session_id` → `sessions(session_id)`.

Purpose: v1 generated artifact (PRD outline etc.).

## Data ownership

All write paths and all row-level read paths gate on user ownership
before touching SQLite. The gate lives at two layers:

1. **HTTP dependency** (`services/planning_studio_service/api.py`):
   - `_require_owned_project` (`api.py:358`) — checks
     `store.verify_project_ownership`.
   - `_require_owned_topic` (`api.py:366`) — via
     `store.get_topic_with_ownership`.
   - `_require_owned_decision` (`api.py:372`) — via
     `store.get_decision_with_ownership`.
   - `_require_owned_relationship` (`api.py:378`) — via
     `store.get_relationship_with_ownership`.

2. **Store helpers** (`services/planning_studio_service/store.py`):
   - `verify_project_ownership` (`store.py:541`) — the ground-truth
     check; returns `True` iff `v2_projects.user_id == user_id`.
   - `get_topic_with_ownership`, `get_decision_with_ownership`,
     `get_relationship_with_ownership` — re-use the project gate by
     project_id of the parent row.

Behavior on ownership mismatch: the HTTP layer returns `404` — never
`403` — so an attacker cannot enumerate valid IDs by watching status
codes. See the ownership tests at `services/tests/test_ownership.py`.

**Known caveat:** per the April 2026 security audit
(`docs/deployment/security-audit.md` §C2), a number of raw CRUD helpers
in `store.py` still accept `user_id` as a kwarg and intentionally ignore
it (marked `_ = user_id`) because the HTTP layer pre-checks ownership.
Bypassing the HTTP layer and calling store methods directly therefore
does not enforce tenancy — always route through the FastAPI handlers.

## Soft-delete semantics

Four table families use soft-delete:

- `v2_projects.deleted_at` — project soft-delete cascades to
  `topics.deleted_at` and `relationships.deleted_at` within the same
  transaction (`store.py:653`).
- `topics.deleted_at` — topic soft-delete cascades to the touching
  relationships (`store.py:994`).
- `relationships.deleted_at` — plain soft-delete.
- `decisions.status = 'retracted'` (+ `retracted_at`) — decisions use a
  status flip, not a timestamp column, but the effect is the same:
  `list_decisions` filters `status != 'retracted'`.

Listings (`list_topics`, `list_relationships`, `list_v2_projects`)
filter out soft-deleted rows by default. `list_topics` accepts an
`include_deleted=True` escape hatch for admin or diagnostic paths.

Q&A turns, open questions, risks/assumptions, summary versions,
consistency flags, approval actions, and source references stay in the
DB indefinitely — they form the audit trail for the live domain rows and
are not user-deletable today. A scheduled hard-purge job (for soft-
deleted rows past a grace window) is planned but not yet implemented.

## Schema provenance

Two sources of DDL, kept byte-identical on purpose:

1. `services/planning_studio_service/store.py` — `_initialize`,
   `_initialize_v2_schema`, `_initialize_users_schema`,
   `_initialize_v2_projects_schema`, `_ensure_user_id_columns`. Runs on
   every service boot. Uses `CREATE TABLE IF NOT EXISTS` so restart is
   safe. This path predates alembic and will retire once alembic owns
   schema changes.

2. `services/alembic/versions/20260421_0001_baseline.py` — authoritative
   path going forward. `alembic upgrade head` is the supported way to
   set up a fresh database, especially against Postgres (SQLite still
   works for dev).

**Never add new tables or columns to `store.py`'s `_initialize*` path
going forward.** Create a new alembic revision instead:

```bash
alembic -c services/alembic.ini revision -m "add foo column to bar"
```

Both paths are safe to run in either order: store.py's `IF NOT EXISTS`
makes it idempotent against an alembic-bootstrapped DB, and alembic's
baseline is similarly idempotent against a store.py-bootstrapped DB.
