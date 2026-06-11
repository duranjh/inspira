# Operator Template Pack for Planning Studio

## Purpose

This file defines a compact, reusable template pack for Planning Studio. It is operator-facing: the point is to help a human founder, PM, strategist, or department lead move from a vague idea to a buildable plan without skipping important reasoning steps.

This pack is intentionally global and domain-agnostic. It is not a replacement for department-specific frameworks such as Product Protocol scoring, ad-research SOPs, or other specialized evaluation systems. Those remain optional downstream modules.

Use these templates to standardize:
- discovery interviews
- scenario exploration
- assumptions and constraints
- PRD structure
- execution breakdowns
- risk review

## Design rules

1. Ask questions before drafting solutions when the request is still ambiguous.
2. Separate planning from evaluation. Planning clarifies what should be built; evaluation decides whether it is worth doing.
3. Capture assumptions explicitly.
4. Force normal flows, edge cases, and failure states.
5. Default to markdown outputs that can be saved, searched, diffed, and promoted into Knowledge Exchange.
6. Keep templates short enough to be useful in live operator sessions.
7. Make every output easy to hand off to Codex/OpenClaw.

---

## Template 01 — Project Kickoff

### When to use
Use at the start of any new project, feature, workflow, tool, or department initiative.

### Input prompt
```md
Start a Project Kickoff session.

Help me define:
- what we are building
- why it matters
- who it is for
- what success looks like
- what constraints we must respect
- what is explicitly out of scope

Do not write a PRD yet. Ask targeted questions first, then return a clean kickoff brief.
```

### Output format
```md
# Project Kickoff Brief

## Project
- Name:
- Type:
- Department:
- Owner:

## Objective
- Primary objective:
- Why now:
- Business value:

## User / Operator
- Primary user:
- Secondary users:
- Core pain/problem:

## Success Criteria
- Desired outcome:
- Metrics / signals:
- Definition of done:

## Constraints
- Technical:
- Operational:
- Legal / privacy:
- Timeline / budget:

## In Scope
- ...

## Out of Scope
- ...

## Open Questions
- ...
```

---

## Template 02 — Discovery Interview

### When to use
Use when the operator wants to be guided through ideas, scenarios, workflows, and requirements before any build spec is written.

### Input prompt
```md
Run a Discovery Interview.

Your job is to interview me like a strong PM + systems designer.
Ask one focused block of questions at a time.
Prioritize:
- user journey
- edge cases
- failure states
- permissions
- data flow
- notifications
- review/approval needs
- things that should never happen

Do not jump to implementation until the problem is clearly framed.
```

### Output format
```md
# Discovery Interview Summary

## Problem Statement
- ...

## Actors
- Primary actor:
- Supporting actors:
- Admin / reviewer roles:

## Core Workflows
1. ...
2. ...
3. ...

## Inputs
- ...

## Outputs
- ...

## Rules / Constraints
- ...

## Edge Cases
- ...

## Failure Modes
- ...

## Permissions / Access
- ...

## Decisions Made
- ...

## Unresolved Questions
- ...
```

---

## Template 03 — Scenario Matrix

### When to use
Use after discovery and before PRD drafting. This is the anti-handwave template.

### Input prompt
```md
Create a Scenario Matrix for this feature/project.

Cover:
- happy path
- alternate valid paths
- edge cases
- failure states
- abuse or misuse cases
- recovery behavior
- observability/admin needs

Return the result as a markdown table plus a short narrative summary.
```

### Output format
```md
# Scenario Matrix

| Scenario ID | Scenario Type | Trigger | Expected System Behavior | User Feedback | Admin/Logging Need | Notes |
|---|---|---|---|---|---|---|
| S1 | Happy path | ... | ... | ... | ... | ... |
| S2 | Edge case | ... | ... | ... | ... | ... |
| S3 | Failure state | ... | ... | ... | ... | ... |

## Narrative Summary
- Biggest risk areas:
- Most important recovery behaviors:
- Scenarios that need tests first:
```

---

## Template 04 — Assumptions and Challenge Review

### When to use
Use before greenlighting implementation. This forces pushback.

### Input prompt
```md
Run an Assumptions and Challenge Review.

Challenge this idea/spec as if you are trying to prevent a bad build.
Identify:
- hidden assumptions
- contradictory requirements
- likely user confusion
- technical traps
- privacy/security concerns
- workflow friction
- missing decisions
- simpler alternatives

Be direct. Prefer clarity over politeness.
```

### Output format
```md
# Assumptions and Challenge Review

## Hidden Assumptions
- ...

## Contradictions / Ambiguities
- ...

## Main Risks
- Product risk:
- UX risk:
- Technical risk:
- Data/privacy risk:
- Operational risk:

## Simpler Alternatives
- ...

## Questions That Must Be Resolved Before Build
- ...

## Recommendation
- Proceed / revise / stop
- Why:
```

---

## Template 05 — PRD Generator

### When to use
Use once kickoff, interview, and scenario review are sufficiently complete.

### Input prompt
```md
Generate a build-ready PRD.

Use the information already gathered.
The PRD must be specific enough for Codex/OpenClaw implementation.
Include:
- user/problem context
- scope
- workflows
- UI requirements
- backend/API requirements
- data/storage requirements
- permissions
- telemetry/logging needs
- acceptance criteria
- explicit non-goals

Avoid vague product language. Be concrete.
```

### Output format
```md
# Product Requirements Document

## Summary
- ...

## Problem / Goal
- ...

## Users / Roles
- ...

## In Scope
- ...

## Out of Scope
- ...

## Functional Requirements
1. ...
2. ...
3. ...

## UX / UI Requirements
- ...

## Backend / API Requirements
- ...

## Data / Storage Requirements
- ...

## Permissions / Security
- ...

## Telemetry / Logging
- ...

## Acceptance Criteria
- ...

## Risks / Open Questions
- ...
```

---

## Template 06 — Execution Breakdown

### When to use
Use after PRD approval to create build sequencing.

### Input prompt
```md
Convert the approved PRD into an execution plan.

Break the work into implementation slices.
Each slice should be small, testable, and reviewable.
Identify dependencies, migrations, backend work, frontend work, and validation steps.
```

### Output format
```md
# Execution Plan

## Build Strategy
- ...

## Work Slices
### Slice 1
- Goal:
- Files/systems affected:
- Backend:
- Frontend:
- Tests:
- Acceptance check:

### Slice 2
- ...

## Dependencies
- ...

## Migration / Rollout Notes
- ...

## Post-Launch Validation
- ...
```

---

## Template 07 — Promotion to Knowledge Exchange

### When to use
Use when a planning artifact becomes durable knowledge worth preserving.

### Input prompt
```md
Convert this project artifact into a Knowledge Exchange entry.

Create:
- a durable summary
- links to related concepts/projects
- key decisions
- assumptions that were validated or invalidated
- follow-up questions worth tracking
```

### Output format
```md
# Knowledge Exchange Entry

## Title
- ...

## Summary
- ...

## Related Projects / Concepts
- ...

## Important Decisions
- ...

## What We Learned
- ...

## Follow-up Questions
- ...

## Source Artifacts
- ...
```

---

## Recommended template flow

### Global default flow
1. Project Kickoff
2. Discovery Interview
3. Scenario Matrix
4. Assumptions and Challenge Review
5. PRD Generator
6. Execution Breakdown
7. Promotion to Knowledge Exchange

### Fast path for small features
1. Project Kickoff
2. Scenario Matrix
3. PRD Generator
4. Execution Breakdown

### Recovery path for messy projects
1. Discovery Interview
2. Assumptions and Challenge Review
3. Rewrite Kickoff Brief
4. PRD Generator

---

## Notes for implementation inside Planning Studio

The UI should present these templates as operator-selectable modes, not as static docs only.

Recommended features:
- template picker
- editable prompt seed
- structured markdown output preview
- save as artifact
- promote to Knowledge Exchange
- export PRD
- version history
- compare revisions

The storage model should remain markdown-first so templates and outputs stay portable.
