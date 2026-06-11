<!--
Thanks for contributing to Inspira. A short, honest description beats a polished one — the goal is to give a reviewer enough context to move quickly.
-->

## What changed

A plain-English description of the change. One or two paragraphs is usually enough. Prefer prose over a bulleted list of file names — reviewers can read the diff.

## Why

What user problem, bug, or internal friction does this address? Link the issue or discussion if one exists (`Closes #123`).

## Screenshots

For any user-facing change, drop in before / after screenshots or a short recording. For backend-only changes, you can remove this section.

## Test plan

How a reviewer (or you, a week from now) can verify this works. Cover both the happy path and any edge cases you thought about.

- [ ]
- [ ]
- [ ]

## Rollout notes

Anything special the person merging or deploying this needs to know — migrations, env var changes, feature flags, coordination with the marketing site, follow-up PRs, etc. If there's nothing to call out, say "none."

## Checklist

- [ ] I ran the test suite locally and it passes
- [ ] I updated docs, comments, or CHANGELOG entries where the behavior changed
- [ ] No new runtime dependencies (or, if there are, I've explained why in "What changed")
