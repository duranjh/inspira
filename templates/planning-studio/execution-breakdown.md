# Execution Breakdown Template

## When to use
Use after PRD approval to create build sequencing.

## Input prompt
```md
Convert the approved PRD into an execution plan.

Break the work into implementation slices.
Each slice should be small, testable, and reviewable.
Identify dependencies, migrations, backend work, frontend work, and validation steps.
```

## Output format
```md
# Execution Plan

## Build Strategy
- ...

## Work Slices
### Slice 1
- Goal:
- Files / systems affected:
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

