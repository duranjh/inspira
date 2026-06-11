# Scenario Matrix Template

## When to use
Use after discovery and before PRD drafting. This is the anti-handwave template.

## Input prompt
```md
Create a Scenario Matrix for this feature or project.

Cover:
- happy path
- alternate valid paths
- edge cases
- failure states
- abuse or misuse cases
- recovery behavior
- observability / admin needs

Return the result as a markdown table plus a short narrative summary.
```

## Output format
```md
# Scenario Matrix

| Scenario ID | Scenario Type | Trigger | Expected System Behavior | User Feedback | Admin / Logging Need | Notes |
|---|---|---|---|---|---|---|
| S1 | Happy path | ... | ... | ... | ... | ... |
| S2 | Edge case | ... | ... | ... | ... | ... |
| S3 | Failure state | ... | ... | ... | ... | ... |

## Narrative Summary
- Biggest risk areas:
- Most important recovery behaviors:
- Scenarios that need tests first:
```

