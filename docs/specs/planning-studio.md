# Planning Studio — Build Specification for Codex / OpenClaw

## Purpose
Build a new top-level dashboard page called **Planning Studio** inside the existing Paperclip dashboard.

This feature is a **private, guided product-planning and specification system** for apps, features, automations, internal tools, migrations, and client projects. It should help the operator think through scenarios, constraints, edge cases, architecture, and execution before work is handed to agents.

This is inspired by products like CodeSpring, but it must be adapted to the current Paperclip + OpenClaw environment rather than cloned literally.

The defining behavior is:

> The system should not jump straight to writing a PRD. It should first walk the user through the problem, ask intelligent follow-up questions, identify missing decisions, expand scenarios, and only then compile the final artifacts.

---

## Product outcome
When the user opens **Planning Studio**, they should be able to:

1. Start from an idea, a repo, a feature request, a bug, or a client brief.
2. Be guided through structured questioning instead of dumping one giant prompt.
3. Upload or attach supporting files, links, prior notes, and optional repo context.
4. Generate a scenario-rich planning package, not just a shallow PRD.
5. See architecture, dependencies, and affected areas visually.
6. Convert an approved plan into Paperclip goals, issues, and agent assignments.
7. Re-open the plan later, compare it against code or task progress, and manage drift.
8. Keep planning artifacts private by default and outside git unless explicitly exported.

The key design principle is:

> Interview first. Structure second. Artifacts third. Execution last.

---

## What this is not
This is **not**:

- a generic chatbot page,
- a pure PRD generator,
- a clone of CodeSpring’s UX,
- the same thing as Knowledge Exchange,
- the same thing as Company Knowledge,
- the same thing as agent episodic memory,
- an automatic code-writer page,
- a git-backed docs folder by default.

---

## Relationship to existing systems
Keep these systems distinct:

1. **Planning Studio** = active planning, questioning, scenario expansion, spec generation, execution handoff.
2. **Knowledge Exchange** = private second brain / compiled personal knowledge vault.
3. **Company Knowledge** = shared institutional context for agents.
4. **Agent Memory** = runtime episodic and procedural memory.

### Allowed interactions
Planning Studio may:
- reference selected Knowledge Exchange pages,
- reference selected Company Knowledge pages,
- reference repo snapshots,
- export approved artifacts into Paperclip goals/issues,
- optionally publish selected final docs elsewhere.

### Disallowed by default
Planning Studio must **not**:
- automatically dump all planning artifacts into Knowledge Exchange,
- automatically expose private planning sessions to agents,
- automatically commit plan docs into the application repo,
- automatically rewrite Company Knowledge.

---

## Why this exists in Paperclip
This feature should be built as a first-class Paperclip page because it directly fills a gap between:

- ideation,
- structured planning,
- agent execution,
- and governance.

Paperclip already acts as the control plane for coordinated agents, budgets, approvals, goals, and tasks. Planning Studio should become the layer that turns messy ideas into approved execution packets.

This is also a strong standalone product surface and should be architected so it could later be extracted or offered separately without a redesign.

---

## Non-negotiable constraints

### Privacy and storage
- Do **not** automatically store Planning Studio artifacts in the application git repository.
- Do **not** automatically commit or push planning artifacts anywhere.
- Store artifacts under the Paperclip runtime data root by default.
- Any export into a repo must be an explicit user action.
- Planning sessions are private by default.

### Repo fit
- Use existing Paperclip monorepo conventions.
- Extend existing server, ui, db, auth, and agent orchestration patterns.
- Avoid a parallel app shell or separate deployment unit for v1.
- Avoid introducing an unnecessary Python/LangGraph sidecar for v1.

### User experience
- The core experience must be **guided**.
- The system must ask follow-up questions when the plan is incomplete.
- The user should always be able to see:
  - what is known,
  - what is assumed,
  - what is undecided,
  - what is risky,
  - what is blocked.

### Drift handling
- Do **not** blindly auto-rewrite specs on every code change.
- Generate drift reports and suggested updates.
- Require review before major spec mutation.

### MCP usage
- MCP support is desirable, but it is **not** a blocker for v1 usability.
- V1 must work even if the user never connects an IDE via MCP.
- MCP should be treated as an export/integration surface, not the only way the system works.

---

## Recommended naming
Default product name: **Planning Studio**

Good alternatives:
- Spec Studio
- Project Planner
- Build Planner
- Product Lab

Do **not** hardcode “PRD Generator” as the feature name. The system is broader than PRDs.

---

## Core user journeys

### 1) New app planning
The user has an idea for a new app.
The system should:
- ask what the app is,
- ask who it is for,
- ask what the first version must do,
- force prioritization,
- identify critical workflows,
- generate a launchable v1 plan,
- produce an execution handoff.

### 2) New feature planning
The user wants to add a feature to an existing repo.
The system should:
- inspect the repo,
- identify affected areas,
- ask how the feature should work,
- identify dependencies and migration risk,
- suggest file targets,
- produce a feature PRD + technical spec + issue pack.

### 3) Refactor / migration planning
The user wants to restructure code or migrate architecture.
The system should:
- map the current system,
- identify dangerous blast radius,
- ask for invariants and rollback strategy,
- produce phased migration steps,
- generate ADRs and risk logs.

### 4) Internal tool / automation planning
The user wants to build internal ops software or an agent workflow.
The system should:
- capture the business process,
- identify actors and triggers,
- define inputs/outputs,
- cover failure modes and audit trails,
- create specs and tasks suitable for Paperclip agents.

### 5) Client project planning
The user receives an external brief.
The system should:
- convert vague requirements into a structured plan,
- identify missing client decisions,
- produce a “questions for client” artifact,
- generate a deliverables map.

---

## The right product shape
This should be an **interview-first planning workspace**, not a graph toy.

### Mandatory surfaces for v1
1. **Session Chat / Interview**
2. **Structured Outline / Coverage Checklist**
3. **Scenario Matrix**
4. **PRD + Technical Spec Artifacts**
5. **Task / Issue Handoff**
6. **Repo Context Summary**
7. **Decision Log**
8. **Drift Report**

### Useful but not blocking for v1
1. Visual architecture graph
2. Live multi-user collaboration
3. Security scan generation
4. Full MCP export server
5. Boilerplate library marketplace

### Important product principle
The visual graph is valuable, but it is **not** the primary UX.
The primary UX is a high-quality guided planning conversation that results in structured, reusable artifacts.

---

## Core workflow

### Phase A — Intake
User creates a planning session from one of these sources:
- blank idea,
- repo,
- uploaded brief,
- issue/request,
- client note,
- existing plan.

The system asks the user to choose a planning mode:
- New Product
- New Feature
- Refactor / Migration
- Internal Automation
- Bug / Incident -> Fix Plan
- Client Build

### Phase B — Guided interrogation
A planner agent asks targeted questions until minimum coverage is reached.

Required coverage areas:
- problem
- target users / actors
- desired outcome
- non-goals
- constraints
- assumptions
- dependencies
- data model changes
- permissions / roles
- happy path flows
- edge cases
- failure modes
- rollout / migration
- metrics / success criteria
- risks
- open questions

The agent must not finalize the PRD until coverage passes a threshold.

### Phase C — Repo and source grounding
If a repo is attached, the system must:
- detect frameworks/languages,
- map routes/pages/services/models,
- summarize existing architecture,
- suggest affected files and modules,
- identify conventions already in use,
- highlight uncertainty.

If supporting docs are attached, the system should extract and summarize them into the planning session.

### Phase D — Scenario expansion
The system generates a scenario matrix covering:
- primary happy paths,
- alternate paths,
- edge cases,
- invalid input,
- empty states,
- permission failures,
- operational failures,
- analytics and observability,
- rollback and recovery,
- human override/admin flows,
- support and debugging cases.

### Phase E — Artifact compilation
The system compiles the interview + repo analysis into a planning bundle.

### Phase F — Review and approval
The user reviews:
- assumptions,
- unresolved questions,
- risks,
- architecture,
- tasks.

The user can approve, revise, or mark sections as tentative.

### Phase G — Execution handoff
On approval, the system can:
- generate Paperclip goals,
- generate Paperclip issues,
- attach artifacts to work items,
- assign work to agents,
- export a bundle for Codex / OpenClaw / Claude / Cursor.

### Phase H — Drift management
After execution begins, the system compares:
- approved plan,
- current issue/task state,
- repo changes.

It generates:
- aligned changes,
- divergent changes,
- missing updates,
- proposed plan revisions.

---

## Required artifacts
Every approved session should produce a stable artifact set on disk.

### Required markdown outputs
- `00-overview.md`
- `01-problem-and-goals.md`
- `02-scenarios.md`
- `03-prd.md`
- `04-technical-spec.md`
- `05-risk-register.md`
- `06-open-questions.md`
- `07-decision-log.md`
- `08-issue-pack.md`
- `09-handoff.md`
- `10-drift-report.md` (created later as needed)

### Optional outputs
- `architecture.canvas`
- `architecture.graph.json`
- `repo-summary.md`
- `adr/ADR-*.md`
- `client-questions.md`
- `launch-checklist.md`
- `test-plan.md`
- `api-contracts/`
- `schemas/`

### Required machine-readable exports
- `session.json`
- `requirements.json`
- `scenarios.json`
- `tasks.json`
- `repo-context.json`
- `trace.json`

---

## Recommended artifact format
All generated markdown artifacts should include YAML frontmatter.

Example:

```yaml
---
id: plan_feature_auth_v1
project_id: proj_123
session_id: sess_456
type: prd
status: draft
planning_mode: feature
repo_attached: true
approval_state: pending
created_at: 2026-04-15T00:00:00Z
updated_at: 2026-04-15T00:00:00Z
source_refs:
  - repo:paperclip-main
  - knowledge:ke_page_42
  - upload:file_abc
---
```

---

## Storage layout
Use a runtime path aligned with Paperclip instance storage.

Recommended default root:

```text
${PAPERCLIP_HOME}/instances/${PAPERCLIP_INSTANCE_ID}/planning-studio/
```

Per project/session:

```text
planning-studio/
  <projectId>/
    AGENTS.md
    sessions/
      <sessionId>/
        interview/
          transcript.md
          turns.json
          coverage.json
        source-context/
          uploads/
          repo/
          knowledge-links.json
          company-links.json
        artifacts/
          00-overview.md
          01-problem-and-goals.md
          02-scenarios.md
          03-prd.md
          04-technical-spec.md
          05-risk-register.md
          06-open-questions.md
          07-decision-log.md
          08-issue-pack.md
          09-handoff.md
          10-drift-report.md
        json/
          session.json
          requirements.json
          scenarios.json
          tasks.json
          repo-context.json
          trace.json
        graph/
          architecture.graph.json
          architecture.canvas
        exports/
          codex/
          openclaw/
          cursor/
          claude/
        logs/
          planner.log
```

Rules:
- All planning artifacts live here first.
- Repo export is explicit.
- Session files are append-friendly and recoverable.

---

## Repo-aware implementation guidance
This must fit the existing Paperclip architecture.

Use the repo’s current patterns:
- `server/` for routes, orchestration, ingestion, artifact generation, drift jobs
- `ui/` for dashboard pages and React components
- `packages/db/` for schema and migrations
- existing auth / company scoping / permissions patterns
- existing agent adapter patterns for OpenClaw, Codex, HTTP, process-based agents

Implementation rules:
- Extend the existing dashboard navigation.
- Reuse existing agent/work item concepts where possible.
- Keep it company-scoped for v1 if user-scoping is not ready yet.
- Make privacy the default.
- Prefer incremental adoption over a giant rewrite.

---

## Technical architecture

### 1) Planning session engine
Responsible for:
- intake state,
- interview turns,
- coverage scoring,
- assumptions,
- open questions,
- plan status.

### 2) Repo context engine
Responsible for:
- file tree snapshots,
- framework detection,
- route/model/service summaries,
- path suggestions,
- affected-area inference,
- convention summaries.

### 3) Scenario engine
Responsible for:
- generating edge cases,
- identifying missing flows,
- mapping actors and permissions,
- creating a scenario matrix.

### 4) Artifact compiler
Responsible for:
- PRD generation,
- technical spec generation,
- issue pack generation,
- handoff bundle generation,
- ADR generation.

### 5) Drift engine
Responsible for:
- comparing approved plan to repo/task changes,
- identifying drift,
- proposing updates,
- generating drift reports.

### 6) Export / integration layer
Responsible for:
- Paperclip issue creation,
- artifact attachments,
- external export bundles,
- optional MCP exposure later.

---

## The most important improvement over the Gemini draft
Do **not** treat this as “visual DAG first, everything else second.”

For this system, the most important differentiator is the **planner interview engine**.

The planning agent must actively do all of the following:
- ask clarifying questions,
- detect when the user is skipping critical decisions,
- force prioritization,
- separate must-have from nice-to-have,
- identify hidden stakeholders,
- identify integration and rollout risks,
- surface contradictory requirements,
- keep a visible list of open questions.

Without this, the page becomes a fancy markdown generator.

---

## UI design
Add a top-level nav item:
- `Planning Studio`

### Primary layout
Three-pane desktop layout:

#### Left pane — Session / Interview
- conversation thread
- current planning mode
- next recommended questions
- assumptions
- unresolved questions

#### Center pane — Working surface
Tabbed:
- Outline
- Scenarios
- Architecture
- PRD
- Technical Spec
- Tasks
- Drift

#### Right pane — Context / Controls
- repo summary
- attached files
- selected Knowledge Exchange references
- selected Company Knowledge references
- coverage score
- approval controls
- export controls

### Mobile / narrow layout
Collapse to stacked sections. Preserve core interview UX before graph UX.

---

## Required planner behaviors
The planner agent should behave like a strong product manager + systems analyst.

### It must
- ask one or more follow-up questions when ambiguity is high,
- group questions by category,
- show why each question matters,
- propose default assumptions when useful,
- allow the user to accept or reject assumptions,
- keep planning artifacts consistent,
- preserve a trace of major decisions.

### It must not
- rush to finalize,
- bury important uncertainties,
- invent repo structure with confidence when uncertain,
- silently mutate approved requirements,
- convert every conversation into tasks automatically.

---

## Guided questioning model
Use a structured question bank with branching.

### Required question categories
- user / actor
- business value
- workflow
- permissions
- data
- integrations
- UI expectations
- performance expectations
- analytics
- rollout
- migration
- testing
- support / admin
- failure handling
- legal/compliance/security

### Planning completeness score
Track a visible completeness score by category:
- complete
- partial
- missing
- not applicable

The PRD should not be marked “ready” until critical categories are at least partial and the user explicitly approves any remaining gaps.

---

## Scenario matrix requirements
Every session must generate a scenario matrix.

Minimum categories:
- happy path
- alternate path
- edge case
- invalid input
- dependency failure
- permission failure
- admin/support path
- migration path
- analytics/observability
- rollback/recovery

Each scenario entry should include:
- actor
- trigger
- preconditions
- expected behavior
- error behavior
- logging / tracking needs
- unresolved questions

---

## Repo analysis rules
When a repo is attached:
- never assume a framework without evidence,
- summarize detected languages and frameworks,
- identify probable routes, models, services, migrations, and tests,
- suggest candidate file targets,
- show confidence levels,
- allow the user to override suggestions.

The system should provide:
- a concise repo summary,
- an affected-area map,
- a list of relevant files,
- suggested implementation zones,
- conventions already in use.

---

## Artifact generation rules

### PRD
The PRD must include:
- problem
- goals
- non-goals
- users/actors
- flows
- scenarios
- acceptance criteria
- metrics
- constraints
- rollout notes
- risks
- open questions

### Technical spec
The technical spec must include:
- current architecture summary
- affected areas
- proposed architecture
- data model impact
- API / event changes
- frontend changes
- backend changes
- migration strategy
- test strategy
- observability
- security considerations
- rollback plan

### Issue pack
The task pack must include:
- epics or parent tasks
- child tasks
- dependency ordering
- owner suggestions
- definition of done
- linked artifacts

---

## Paperclip integration
This feature should integrate natively with Paperclip workflows.

### On approval, support these actions
- create a goal from the planning session,
- create one or more issues from the issue pack,
- attach planning artifacts to the goal/issues,
- assign issues to agents or humans,
- set budgets/approval requirements if desired.

### Do not do automatically
- do not auto-create issues before review,
- do not auto-run coding agents from a draft PRD,
- do not bypass Paperclip’s governance model.

---

## Knowledge Exchange integration
Planning Studio and Knowledge Exchange should remain separate but interoperable.

### Allowed behavior
The user may attach:
- one or more Knowledge Exchange pages,
- one or more private source files,
- one or more company knowledge records.

The planning session should store **references** to those sources, not silently absorb or rewrite them.

### Optional future feature
“Promote final plan to Knowledge Exchange” as an explicit export action.

---

## MCP strategy

### V1
Do not block the feature on MCP.
Instead, provide:
- local markdown/json artifacts,
- Paperclip-native viewing,
- downloadable handoff bundles,
- agent-readable session folders.

### V2
Add an optional MCP server exposing:
- resources for final artifacts,
- prompts for common planning workflows,
- tools for listing sessions, reading specs, and exporting approved plans.

### MCP design rule
When MCP is added, prefer a **minimal, focused server** exposing planning artifacts and workflow actions. Do not expose an uncontrolled kitchen sink of tools.

---

## External documentation retrieval
This system should support grounded retrieval of official technical docs.

### V1 approach
Use a server-side doc resolver that:
- fetches official docs from allowlisted domains,
- stores a citation trail in the session,
- summarizes only what is relevant to the active plan.

### Do not require in v1
- third-party MCP dependency chains,
- multiple external context servers,
- uncontrolled web search inside every planning run.

---

## Drift strategy
Replace “auto-sync everything all the time” with a safer model.

### Drift sources
- repo changes
- changed requirements
- issue/task outcome changes
- architecture decisions made during implementation

### Drift report should classify changes as
- aligned
- approved divergence
- unapproved divergence
- missing documentation
- stale documentation

### Drift action options
- accept current code as truth and update plan
- restore implementation toward approved plan
- create follow-up planning session

---

## Data model
Create explicit domain tables.

### Required tables

#### `planning_projects`
Represents a plan-capable project space.

Suggested fields:
- `id`
- `company_id`
- `name`
- `slug`
- `description`
- `status`
- `visibility`
- `storage_root`
- `created_at`
- `updated_at`

#### `planning_sessions`
One record per planning run.

Suggested fields:
- `id`
- `project_id`
- `title`
- `planning_mode`
- `status` (`draft`, `review`, `approved`, `archived`)
- `approval_state`
- `coverage_json`
- `repo_snapshot_id` nullable
- `current_artifact_root`
- `created_by`
- `created_at`
- `updated_at`

#### `planning_sources`
Attached context for a session.

Suggested fields:
- `id`
- `session_id`
- `source_type` (`upload`, `repo`, `knowledge_exchange`, `company_knowledge`, `issue`, `url`)
- `source_ref`
- `display_name`
- `status`
- `metadata_json`
- `created_at`

#### `planning_questions`
Questions raised during planning.

Suggested fields:
- `id`
- `session_id`
- `category`
- `question`
- `importance`
- `status` (`open`, `answered`, `deferred`, `n_a`)
- `answer_text`
- `created_at`
- `updated_at`

#### `planning_decisions`
Explicit planning decisions.

Suggested fields:
- `id`
- `session_id`
- `category`
- `decision`
- `rationale`
- `status`
- `created_at`

#### `planning_artifacts`
Generated artifacts and exports.

Suggested fields:
- `id`
- `session_id`
- `artifact_type`
- `path`
- `format`
- `status`
- `version`
- `created_at`

#### `planning_nodes`
Optional graph nodes.

Suggested fields:
- `id`
- `session_id`
- `node_type`
- `label`
- `payload_json`
- `position_json`

#### `planning_edges`
Optional graph edges.

Suggested fields:
- `id`
- `session_id`
- `source_node_id`
- `target_node_id`
- `edge_type`
- `payload_json`

#### `planning_exports`
Tracks conversion into issues/goals or external bundles.

Suggested fields:
- `id`
- `session_id`
- `export_type`
- `target_ref`
- `status`
- `metadata_json`
- `created_at`

---

## Recommended implementation stack

### Prefer for v1
- existing Paperclip Node.js / TypeScript backend
- existing React UI
- existing DB / Drizzle migrations
- React Flow only where spatial editing adds clear value
- OpenClaw or existing agent runner patterns for synthesis steps
- simple composable orchestration rather than an extra framework-heavy service

### Avoid for v1 unless clearly necessary
- a dedicated Python LangGraph microservice
- mandatory MCP dependency
- auto-syncing every code change directly into approved specs
- full multiplayer presence infrastructure
- complex vector/RAG stack just to ask planning questions

---

## Suggested phases

### Phase 1 — Usable planning MVP
Ship this first.

Must include:
- Planning Studio page
- planning session creation
- guided question flow
- coverage tracking
- repo summary
- scenario matrix
- PRD generation
- technical spec generation
- issue pack generation
- approval flow
- create Paperclip goals/issues
- private artifact storage

### Phase 1.5 — Stronger execution handoff
Add:
- export bundles for Codex/OpenClaw/Claude/Cursor
- drift report generation
- richer repo affected-area mapping
- templates for common session types

### Phase 2 — Advanced planning UX
Add:
- editable architecture graph
- optional MCP server
- ADR automation
- doc resolver / citations
- reusable plan templates
- limited multi-user collaboration

### Phase 3 — Sellable product surface
Add:
- tenant-aware packaging
- billing hooks
- workspace templates
- polished exports
- role-based collaboration
- plugin surface for third-party sources

---

## Acceptance criteria
The build is complete when all of the following are true:

1. A user can start a new planning session from a blank idea and be guided through structured questions.
2. A user can attach a repo and get a credible repo summary plus suggested affected areas.
3. The system produces a scenario matrix, PRD, technical spec, issue pack, and decision log.
4. The user can review assumptions and open questions before approval.
5. Approved plans can become Paperclip goals/issues.
6. Planning artifacts remain private and outside git by default.
7. The system can later compare task/code changes against the approved plan and produce a drift report.
8. The feature works without requiring MCP.

---

## Embedded AGENTS.md behavior for the planning worker
Place this in the Planning Studio workspace root as the planner behavior contract.

```md
# Planning Studio Agent Contract

You are the planning agent for Planning Studio.
Your job is to transform vague ideas into reviewable execution artifacts.

## Core behavior
- Ask clarifying questions before writing final specs.
- Prefer structured progress over premature completeness.
- Track assumptions explicitly.
- Track unresolved questions explicitly.
- Track risks explicitly.
- Do not silently invent certainty.
- Do not silently change approved requirements.

## Required outputs
You maintain these artifacts:
- overview
- problem and goals
- scenarios
- PRD
- technical spec
- risk register
- open questions
- decision log
- issue pack
- handoff
- drift report

## Working style
- First identify the planning mode.
- Then identify missing context.
- Then ask focused questions in batches.
- Then generate scenario coverage.
- Then compile artifacts.
- Then request review.
- Only convert to execution artifacts after review or approval.

## Repo awareness
If a repo is attached:
- summarize what is actually present,
- infer with caution,
- mark confidence levels,
- suggest file targets without pretending certainty where none exists.

## Quality bar
A plan is not complete until it includes:
- goals and non-goals
- actors
- flows
- edge cases
- risks
- rollout thinking
- measurable acceptance criteria
- open questions

## Constraints
- Keep planning artifacts private by default.
- Do not auto-commit to git.
- Do not mix Planning Studio content into Knowledge Exchange or Company Knowledge unless explicitly instructed.
- Do not trigger execution agents automatically from a draft.
```

---

## Final directive to Codex / OpenClaw
Implement **Planning Studio** as a Paperclip-native planning workspace that is:
- interview-first,
- artifact-driven,
- repo-aware,
- scenario-rich,
- approval-aware,
- execution-connected,
- and private by default.

Do not optimize for cloning CodeSpring’s marketing surface.
Optimize for making the user materially better at planning real projects inside the system they already use.
