# Planning Studio Repo Boundary

## Own here

- planning session UX
- guided interview engine behavior
- scenario matrix generation
- PRD and technical-spec artifact compilation
- task / issue handoff generation
- drift reporting
- operator template pack ownership
- Planning Studio desktop packaging under `app/src-tauri/`

## Do not own here

- Mission Control shell and company-wide dashboard chrome
- Knowledge Exchange private memory domain internals
- host runtime bootstrap for OpenClaw/Hermes

## Integration expectation

Mission Control should integrate with Planning Studio through stable product contracts rather than hosting the long-term planning logic directly.

## Current UI note

The standalone app is currently reset to a blank baseline. Future UI work should start fresh in this repo rather than inheriting the earlier extracted shell.
