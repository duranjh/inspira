"""W2 — Send-to-Linear / Send-to-GitHub export surface.

Two backend routes:

- ``POST /api/v2/projects/{id}/export/linear``
- ``POST /api/v2/projects/{id}/export/github``

Each consumes a project canvas's title + topics + decisions and
projects them into the workspace's configured tracker as a parent
issue with one sub-issue (Linear) / checkbox task (GitHub) per topic.
Reuses the connectors layer (credentials + provider clients) — no
LLM calls, pure formatting + outbound API.
"""
