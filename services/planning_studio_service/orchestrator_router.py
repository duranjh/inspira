"""FastAPI routers for the W3 orchestrator + Promote/SSE surface.

This factory builds two routers:

``/api/v2/orchestrator/*`` — the original orchestrator surface (6 routes):

- ``POST /prioritize``                            (admin+)
- ``GET  /prioritization-runs/{run_id}``          (member+)
- ``POST /run``                                   (admin+)
- ``GET  /runs``                                  (member+) — list endpoint
- ``GET  /runs/{run_id}``                         (member+)
- ``GET  /runs/{run_id}/events``                  (member+, SSE)

``/api/v2/projects/*`` — the gap-fill surface the frontend depends on
(closes #115 + #116):

- ``POST /projects/promote-from-cluster``         (admin+) — synthesizes a
  one-cluster prioritization_run, spawns the orchestrator with ``top_n=1``,
  polls for the v2_projects row the orchestrator writes early in the
  sub-agent, returns
  the project envelope ``PromoteToProjectDialog`` consumes.
- ``GET  /projects/{project_id}/events``          (member+, SSE) — proxies
  the existing run-id keyed SSE stream, looking up
  ``orchestrator_run_id`` from ``v2_projects.metadata_json``. Reuses the
  Queue + replay machinery via the same shared helper as the run-id route.

Async lifecycle for ``/run`` and ``/promote-from-cluster``:

1. Validate inputs (prioritization_run for /run; cluster + title for /promote).
2. UPSERT ``orchestrator_runs`` on ``(workspace_id, prioritization_run_id)``.
   Idempotent — repeat callers get the existing run_id.
3. Create an ``asyncio.Queue`` and register it in
   ``request.app.state.orchestrator_event_queues[run_id]``.
4. Spawn ``orchestrator.run`` as an ``asyncio.create_task``. The task uses
   a ``try/finally`` to pop the queue on completion so the dict doesn't
   grow without bound.

The SSE endpoints read from the queue if present; fall back to a trimmed
replay (``orchestrator.build_replay_events``) if the run already finished.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import TYPE_CHECKING, Any, AsyncIterator, Callable

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Request,
)
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .agents import orchestrator as orchestrator_agent
from .store import now_timestamp
from .orchestrator_store import (
    complete_prioritization_run,
    count_active_sub_agents,
    create_orchestrator_run,
    create_prioritization_run,
    get_orchestrator_run,
    get_prioritization_run,
    list_orchestrator_runs,
    list_sub_agent_runs,
)
from .agents.tiers import CONCURRENT_SUBAGENTS_BY_PLAN
from .sse import format_sse, sse_stream
from .workspaces.models import Role, WorkspaceMember

if TYPE_CHECKING:
    from .store import PlanningStudioStore


logger = logging.getLogger(__name__)


_CurrentWorkspaceMemberFactory = Callable[..., Callable[..., WorkspaceMember]]
_CurrentUserCallable = Callable[..., dict[str, Any]]


# Polling constants for /promote-from-cluster. Module-level so tests can
# monkeypatch (the test suite drops these to ~0.05s/0.5s to verify the
# 504 / 502 paths without a 30s wait per case).
#
# FastAPI/uvicorn handles the long poll asynchronously so it doesn't block
# other requests, but Fly.io's edge proxy idle timeout is ~60s — don't
# raise this past ~50s without a proxy config change. The orchestrator's _create_canvas
# typically writes the v2_projects row within 50-200ms of orchestrator
# spawn, well before the LLM round-trip; the 30s ceiling is the worst-case
# safety net for a slow / cold-start environment.
_PROMOTE_TIMEOUT_S: float = 30.0
_PROMOTE_POLL_INTERVAL_S: float = 0.25


class RunBody(BaseModel):
    """Body for ``POST /api/v2/orchestrator/run``."""

    prioritization_run_id: str = Field(..., min_length=1)
    top_n: int = Field(default=5, ge=1, le=10)


class TopicSeedBody(BaseModel):
    """One user-edited topic seed sent from the Promote dialog.

    No ``id`` field — the frontend assigns client-side ids for React keys
    only and strips them before posting. We stash the seeds in the
    prioritization_run input snapshot + v2_projects metadata for future
    use; threading them into ``extract_topics_and_decisions_for_theme``
    is a follow-up (~3-file ripple).
    """

    name: str
    desc: str


class PromoteFromClusterBody(BaseModel):
    """Body for ``POST /api/v2/projects/promote-from-cluster``.

    ``cluster_id`` and ``project_title`` are nullable / unconstrained at
    the schema level so the handler owns the 400 error path. Pydantic v2
    with ``str = Field(..., min_length=1)`` would 422 on type validation
    BEFORE our handler runs, returning a generic FastAPI validation error
    and never firing the friendly ``cluster_required`` / ``title_required``
    400 codes. Same defensive treatment for both fields.
    """

    cluster_id: str | None = None
    project_title: str | None = None
    topic_seeds: list[TopicSeedBody] = Field(default_factory=list, max_length=20)
    feedback_item_id: str | None = None


class StartCanvasBody(BaseModel):
    """Body for ``POST /api/v2/projects/{id}/start-canvas``.

    ``correction_note``: when a user drags
    a card back into "In Progress" via the Kanban override
    dialog with the rerun toggle on, they type a required note
    explaining what was wrong / what to improve. The route stashes
    this on the synthetic theme that drives the orchestrator; the
    sub-agent's prompt template injects it as a PARTNER_CORRECTION
    fence so the redraft actually responds. Empty / omitted →
    behaves exactly as before (no note, no fence).
    """

    correction_note: str = Field(default="", max_length=2000)


def is_orchestrator_enabled() -> bool:
    """Env gate: production deploys flip ``INSPIRA_ORCHESTRATOR_ENABLED=1``
    once smoke passes; tests + dev default to enabled.

    The intent is to keep the route surface dark on Fly until live
    smoke confirms it works end-to-end. When False, every endpoint
    returns 503 ``orchestrator_disabled``.
    """
    raw = os.environ.get("INSPIRA_ORCHESTRATOR_ENABLED", "").strip()
    if not raw:
        # Default behaviour: enabled in tests (PYTEST_CURRENT_TEST is set),
        # disabled in prod until ops flips the gate.
        if os.environ.get("PYTEST_CURRENT_TEST"):
            return True
        return False
    return raw == "1"


def _require_enabled() -> None:
    if not is_orchestrator_enabled():
        raise HTTPException(
            status_code=503,
            detail={
                "error": "orchestrator_disabled",
                "message": (
                    "Set INSPIRA_ORCHESTRATOR_ENABLED=1 to enable the "
                    "W3 orchestrator surface."
                ),
            },
        )


def make_orchestrator_router(
    store: "PlanningStudioStore",
    current_user: _CurrentUserCallable,
    current_workspace_member: _CurrentWorkspaceMemberFactory,
) -> tuple[APIRouter, APIRouter]:
    """Build the orchestrator + projects routers with closed-over deps.

    Returns ``(orchestrator_router, projects_router)``. ``api.py`` includes
    both. Two routers (rather than one with a path override) keeps prefixes
    clean and lets the surfaces evolve independently.
    """
    router = APIRouter(
        prefix="/api/v2/orchestrator",
        tags=["orchestrator"],
    )
    projects_router = APIRouter(
        prefix="/api/v2/projects",
        tags=["orchestrator", "projects"],
    )

    # -----------------------------------------------------------
    # POST /prioritize  (admin+)
    # -----------------------------------------------------------

    @router.post("/prioritize")
    def prioritize_route(
        background_tasks: BackgroundTasks,
        member: WorkspaceMember = Depends(
            current_workspace_member(Role.admin)
        ),
    ) -> dict[str, Any]:
        _require_enabled()
        # Create the prioritization_runs row inline so the caller has a
        # run_id to poll. The actual ROI scoring (LLM call + theme
        # backfill) runs in BackgroundTasks, bound to this run_id.
        from .feedback_items.cluster import list_clusters_with_distribution

        clusters = list_clusters_with_distribution(
            store, workspace_id=member.workspace_id,
        )
        run_id = create_prioritization_run(
            store,
            workspace_id=member.workspace_id,
            triggered_by=member.user_id,
            input_snapshot={
                "cluster_ids": [c["cluster_id"] for c in clusters],
                "cluster_count": len(clusters),
            },
        )
        background_tasks.add_task(
            _run_prioritization_with_run_id,
            store,
            workspace_id=member.workspace_id,
            run_id=run_id,
        )
        return {"run_id": run_id, "status": "running"}

    # -----------------------------------------------------------
    # GET /prioritization-runs/{run_id}  (member+)
    # -----------------------------------------------------------

    @router.get("/prioritization-runs/{run_id}")
    def get_prioritization_run_route(
        run_id: str,
        member: WorkspaceMember = Depends(
            current_workspace_member(Role.viewer)
        ),
    ) -> dict[str, Any]:
        _require_enabled()
        prio = get_prioritization_run(
            store,
            workspace_id=member.workspace_id,
            run_id=run_id,
        )
        if prio is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": "prioritization_run_not_found",
                    "message": run_id,
                },
            )
        return prio

    # -----------------------------------------------------------
    # POST /run  (admin+)
    # -----------------------------------------------------------

    @router.post("/run")
    async def orchestrator_run_route(
        body: RunBody,
        request: Request,
        member: WorkspaceMember = Depends(
            current_workspace_member(Role.admin)
        ),
    ) -> dict[str, Any]:
        _require_enabled()
        prio = get_prioritization_run(
            store,
            workspace_id=member.workspace_id,
            run_id=body.prioritization_run_id,
        )
        if prio is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": "prioritization_run_not_found",
                    "message": body.prioritization_run_id,
                },
            )
        if prio["status"] != "completed":
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "prioritization_run_not_complete",
                    "message": (
                        f"prioritization_run {body.prioritization_run_id} "
                        f"is in state '{prio['status']}'; wait for completion."
                    ),
                },
            )
        run_id, is_new = await _spawn_orchestrator_run(
            store,
            request=request,
            workspace_id=member.workspace_id,
            user_id=member.user_id,
            prioritization_run_id=body.prioritization_run_id,
            top_n=body.top_n,
        )
        return {
            "run_id": run_id,
            "status": "running",
            "idempotent_hit": not is_new,
        }

    # -----------------------------------------------------------
    # GET /runs  (member+) — workspace-scoped list for the AI
    # Status chip's polling hook. Returns most recent N runs,
    # optionally filtered by status. Each run embeds sub_agents
    # with theme_label joined from feedback_clusters.
    # -----------------------------------------------------------

    @router.get("/runs")
    def list_orchestrator_runs_route(
        status: str | None = None,
        limit: int = 5,
        member: WorkspaceMember = Depends(
            current_workspace_member(Role.viewer)
        ),
    ) -> dict[str, Any]:
        _require_enabled()
        if status is not None and status not in {"running", "completed", "error"}:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "invalid_status",
                    "message": (
                        f"status must be one of running, completed, error; "
                        f"got '{status}'"
                    ),
                },
            )
        runs = list_orchestrator_runs(
            store,
            workspace_id=member.workspace_id,
            status=status,
            limit=max(1, min(limit, 25)),
        )
        return {"runs": runs}

    # -----------------------------------------------------------
    # GET /runs/{run_id}  (member+)
    # -----------------------------------------------------------

    @router.get("/runs/{run_id}")
    def get_orchestrator_run_route(
        run_id: str,
        member: WorkspaceMember = Depends(
            current_workspace_member(Role.viewer)
        ),
    ) -> dict[str, Any]:
        _require_enabled()
        orch = get_orchestrator_run(
            store,
            workspace_id=member.workspace_id,
            run_id=run_id,
        )
        if orch is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": "orchestrator_run_not_found",
                    "message": run_id,
                },
            )
        return orch

    # -----------------------------------------------------------
    # GET /runs/{run_id}/events  (member+, SSE)
    # -----------------------------------------------------------

    @router.get("/runs/{run_id}/events")
    async def orchestrator_run_events_route(
        run_id: str,
        request: Request,
        member: WorkspaceMember = Depends(
            current_workspace_member(Role.viewer)
        ),
    ) -> StreamingResponse:
        _require_enabled()
        return _orchestrator_event_stream_response(
            store,
            request=request,
            workspace_id=member.workspace_id,
            run_id=run_id,
        )

    # -----------------------------------------------------------
    # POST /projects/promote-from-cluster  (admin+)
    # -----------------------------------------------------------

    @projects_router.post("/promote-from-cluster")
    async def promote_from_cluster_route(
        body: PromoteFromClusterBody,
        request: Request,
        member: WorkspaceMember = Depends(
            current_workspace_member(Role.admin)
        ),
    ) -> dict[str, Any]:
        _require_enabled()
        # 1. Validate cluster_id + project_title at handler level (Pydantic
        #    permits null so we own the 400 envelope; see body docstring).
        if not body.cluster_id:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "cluster_required",
                    "message": (
                        "cluster_id is required. The source feedback item "
                        "may not be clustered yet — try again in a moment."
                    ),
                },
            )
        if not body.project_title or not body.project_title.strip():
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "title_required",
                    "message": "project_title is required.",
                },
            )
        title = body.project_title.strip()

        # 2. Validate cluster exists in caller's workspace. 404 (not 403)
        #    on cross-workspace per orchestrator_store convention — don't
        #    leak existence across workspace boundaries.
        from .feedback_items.cluster import list_clusters_with_distribution

        clusters = list_clusters_with_distribution(
            store, workspace_id=member.workspace_id,
        )
        cluster = next(
            (c for c in clusters if c["cluster_id"] == body.cluster_id),
            None,
        )
        if cluster is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": "cluster_not_found",
                    "message": body.cluster_id,
                },
            )

        # 3. Synthesize a completed prioritization_run with this single
        #    cluster as its only theme. Bypasses the F6 LLM call — every
        #    orchestrator_run still has a prio_run parent (a schema
        #    invariant) but we skip the ROI scoring detour for a known cluster.
        seeds_payload = [
            {"name": s.name, "desc": s.desc} for s in body.topic_seeds
        ]

        # Product decision: pull fresh repo context from
        # GitHub before every canvas spawn so the orchestrator's
        # sub-agents can ground their decisions in the actual repo
        # layout (README, package.json, top-level files). Returns None
        # silently when no GitHub credential is wired — the
        # orchestrator falls through to a non-repo-aware prompt.
        from .connectors.github.repo_context import fetch_repo_context

        try:
            repo_context = await fetch_repo_context(
                store, workspace_id=member.workspace_id,
            )
        except Exception:  # noqa: BLE001
            # Defense in depth — repo_context.fetch_repo_context already
            # swallows upstream failures, but keep the canvas-spawn
            # resilient if some other path raises.
            repo_context = None

        input_snapshot = {
            "source": "promote_from_cluster",
            "cluster_id": body.cluster_id,
            "project_title": title,
            "topic_seeds": seeds_payload,
            "feedback_item_id": body.feedback_item_id,
            "repo_context": repo_context,
        }
        prio_run_id = create_prioritization_run(
            store,
            workspace_id=member.workspace_id,
            triggered_by=member.user_id,
            input_snapshot=input_snapshot,
        )
        synthetic_theme = {
            "cluster_id": body.cluster_id,
            "rank": 1,
            "score": 100.0,
            "rationale": "User-promoted via Promote dialog.",
            "suggested_theme_label": cluster.get("theme") or title,
            "provenance": {
                "item_count": cluster.get("item_count", 0),
                "category_counts": cluster.get("category_counts", {}),
                "most_recent_ingested_at": cluster.get(
                    "most_recent_ingested_at"
                ),
                "sample_item_ids": cluster.get("sample_item_ids", []),
            },
        }
        complete_prioritization_run(
            store,
            workspace_id=member.workspace_id,
            run_id=prio_run_id,
            output={
                "themes": [synthetic_theme],
                "model": "user-promote",
                "input_cluster_count": 1,
            },
        )

        # 4. Spawn the orchestrator with top_n=1.
        run_id, _is_new = await _spawn_orchestrator_run(
            store,
            request=request,
            workspace_id=member.workspace_id,
            user_id=member.user_id,
            prioritization_run_id=prio_run_id,
            top_n=1,
        )

        # 5. Poll for the v2_projects row the orchestrator's _create_canvas writes early
        #    in the sub-agent (typically within 50-200ms of spawn — well
        #    before the LLM call). Two failure modes to handle alongside
        #    the timeout:
        #
        #    (a) "fast-fail before canvas": if the orchestrator blew up before _create_canvas
        #        ran, every sub_agent_runs row is terminal-failed and no
        #        v2_projects row matches. Return 502 promptly.
        #
        #    (b) "canvas exists but generation failed": the orchestrator writes the canvas
        #        with metadata.state="generating", then on LLM failure
        #        UPDATEs to metadata.state="generation_failed". Polling
        #        sees the canvas first; check the state and 502 instead of
        #        returning a "successful" envelope pointing at a dead canvas.
        #
        #    The user-edited topic_seeds + feedback_item_id we received are
        #    durably stored on the prio_run's input_snapshot_json (step 3).
        #    They intentionally don't land on v2_projects.metadata because
        #    the orchestrator's success-path write (orchestrator.py:469-484) hard-overwrites
        #    metadata_json with a fixed 4-key dict — any seeds we wrote here
        #    would be silently clobbered. Future LLM threading reads from the
        #    prio_run, which is the durable home.
        max_polls = max(1, int(_PROMOTE_TIMEOUT_S / _PROMOTE_POLL_INTERVAL_S))
        for _ in range(max_polls):
            project = _find_promoted_v2_project(
                store,
                workspace_id=member.workspace_id,
                orchestrator_run_id=run_id,
                theme_id=body.cluster_id,
            )
            if project is not None:
                state = (project.get("metadata") or {}).get("state")
                if state == "generation_failed":
                    raise HTTPException(
                        status_code=502,
                        detail={
                            "error": "promote_orchestrator_failed",
                            "message": (
                                "Canvas generation failed; check the workspace "
                                "home for the failed run."
                            ),
                        },
                    )
                # state is "generating" (still in flight) or "pending_review"
                # (sub-agent finished). Both are 200 — frontend's useSSE picks
                # up live progress from /events.
                return {"project": project}

            sa_runs = list_sub_agent_runs(
                store,
                workspace_id=member.workspace_id,
                orchestrator_run_id=run_id,
            )
            if sa_runs and all(
                sa["status"] in ("failed", "error") for sa in sa_runs
            ):
                raise HTTPException(
                    status_code=502,
                    detail={
                        "error": "promote_orchestrator_failed",
                        "message": (
                            "Canvas generation failed; check the workspace "
                            "home for the failed run."
                        ),
                    },
                )
            await asyncio.sleep(_PROMOTE_POLL_INTERVAL_S)

        raise HTTPException(
            status_code=504,
            detail={
                "error": "promote_timeout",
                "message": (
                    "Canvas generation took too long; check the workspace "
                    "home for the in-progress canvas."
                ),
            },
        )

    # -----------------------------------------------------------
    # GET /projects/{project_id}/events  (member+, SSE)
    # -----------------------------------------------------------

    @projects_router.get("/{project_id}/events")
    async def project_events_route(
        project_id: str,
        request: Request,
        member: WorkspaceMember = Depends(
            current_workspace_member(Role.viewer)
        ),
    ) -> StreamingResponse:
        _require_enabled()
        # Look up the project, scoped to caller's workspace. 404 (not 403)
        # on cross-workspace conflates the existence check with the not-
        # found path — don't leak existence across boundaries.
        with store._connect() as connection:
            row = connection.execute(
                "SELECT metadata_json FROM v2_projects "
                "WHERE project_id = ? AND workspace_id = ? "
                "AND deleted_at IS NULL",
                (project_id, member.workspace_id),
            ).fetchone()
        if row is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": "project_not_found",
                    "message": project_id,
                },
            )
        metadata = json.loads(row[0] or "{}")
        run_id = metadata.get("orchestrator_run_id")
        if not run_id:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": "no_orchestrator_run",
                    "message": (
                        "This project wasn't generated by the orchestrator "
                        "and has no event stream."
                    ),
                },
            )
        return _orchestrator_event_stream_response(
            store,
            request=request,
            workspace_id=member.workspace_id,
            run_id=run_id,
        )

    @projects_router.post("/{project_id}/start-canvas", status_code=202)
    async def start_canvas_route(
        project_id: str,
        request: Request,
        body: StartCanvasBody | None = None,
        member: WorkspaceMember = Depends(
            current_workspace_member(Role.member)
        ),
    ) -> dict[str, Any]:
        """Spawn the orchestrator for an auto-promoted Draft project.

        Product decision: auto-promoted Kanban shells
        should transition to "AI thinking" automatically — clicking a
        tile in `In queue` triggers the orchestrator, which writes its
        topics + decisions back into the SAME project_id (no
        duplicate). Project state flips pending_review → in_review so
        useKanbanData reclassifies it into the AI-thinking column.

        Idempotent: a second call within the same run window returns
        the existing run_id rather than spawning a duplicate. (The
        orchestrator's per-prio_run guard handles this.)

        Returns 202 + {run_id, project_id, status: "thinking"}.
        """
        _require_enabled()
        # 1. Resolve project + cluster_id from metadata.
        proj = store._get_v2_project(project_id)
        if proj is None or proj.get("workspace_id") != member.workspace_id:
            raise HTTPException(
                status_code=404,
                detail={"error": "project_not_found"},
            )
        # `_get_v2_project` pre-parses metadata_json into proj["metadata"]
        # — read the dict directly, not the raw column.
        md = proj.get("metadata") or {}
        if not isinstance(md, dict):
            md = {}
        cluster_id = md.get("cluster_id")
        if not cluster_id:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "no_cluster_attached",
                    "message": (
                        "This project isn't linked to a feedback cluster, so "
                        "the orchestrator has nothing to draft topics from."
                    ),
                },
            )
        # If the project was already orchestrated (or is in flight),
        # return the existing run_id idempotently — UNLESS the caller
        # explicitly asked for a rerun by providing a correction_note.
        # Rerun intent overrides
        # idempotency; we clear the existing run pointer below + let
        # the orchestrator spawn fresh with the new note in context.
        correction_note_preview = (body.correction_note if body else "").strip()
        existing_run = md.get("orchestrator_run_id")
        if existing_run and not correction_note_preview:
            return {
                "run_id": existing_run,
                "project_id": project_id,
                "status": "already_running",
            }

        # 1b. Concurrent sub-agent cap.
        # Free=1 / Pro=3 / Frontier=50 / Enterprise=100. Idempotent
        # re-spawn doesn't count against the cap (caught above), so
        # the only path that lands here is a brand-new run.
        from .workspaces.store import get_workspace
        workspace = get_workspace(store, member.workspace_id)
        plan_slug = (workspace.plan_tier if workspace else "free").lower()
        cap = CONCURRENT_SUBAGENTS_BY_PLAN.get(plan_slug, 1)
        active = count_active_sub_agents(
            store, workspace_id=member.workspace_id,
        )
        if active >= cap:
            raise HTTPException(
                status_code=429,
                detail={
                    "error": "subagent_cap_reached",
                    "plan": plan_slug,
                    "cap": cap,
                    "active": active,
                    "message": (
                        f"Your plan allows {cap} concurrent AI sub-agent"
                        f"{'s' if cap != 1 else ''}. "
                        f"{active} already running — wait for one to "
                        "finish or upgrade for more parallelism."
                    ),
                },
            )

        # 2. Validate cluster exists in the workspace.
        from .feedback_items.cluster import list_clusters_with_distribution
        clusters = list_clusters_with_distribution(
            store, workspace_id=member.workspace_id,
        )
        cluster = next(
            (c for c in clusters if c["cluster_id"] == cluster_id), None,
        )
        if cluster is None:
            raise HTTPException(
                status_code=404,
                detail={"error": "cluster_not_found", "message": cluster_id},
            )

        # 3. Pull fresh repo context (may be None — orchestrator falls
        # through gracefully).
        from .connectors.github.repo_context import fetch_repo_context
        try:
            repo_context = await fetch_repo_context(
                store, workspace_id=member.workspace_id,
            )
        except Exception:  # noqa: BLE001
            repo_context = None

        # 4. Synthesize a one-cluster prio_run with target_project_id
        # so the orchestrator UPDATES the existing v2_project instead
        # of creating a duplicate.
        input_snapshot = {
            "source": "start_canvas",
            "cluster_id": cluster_id,
            "target_project_id": project_id,
            "project_title": proj.get("title", ""),
            "repo_context": repo_context,
        }
        prio_run_id = create_prioritization_run(
            store,
            workspace_id=member.workspace_id,
            triggered_by=member.user_id,
            input_snapshot=input_snapshot,
        )
        correction_note = (body.correction_note if body else "").strip()
        synthetic_theme = {
            "cluster_id": cluster_id,
            "target_project_id": project_id,
            "rank": 1,
            "score": 100.0,
            "rationale": "Auto-spawned from Kanban tile click.",
            "suggested_theme_label": (
                cluster.get("theme") or proj.get("title") or cluster_id
            ),
            "provenance": {
                "item_count": cluster.get("item_count", 0),
                "category_counts": cluster.get("category_counts", {}),
            },
            # Threaded into _run_sub_agent_for_theme → sub_agent's
            # PARTNER_CORRECTION fence in the user prompt. Empty = no
            # fence emitted, behaves exactly as before.
            "correction_note": correction_note,
        }
        complete_prioritization_run(
            store,
            workspace_id=member.workspace_id,
            run_id=prio_run_id,
            output={
                "themes": [synthetic_theme],
                "model": "start-canvas",
                "input_cluster_count": 1,
            },
        )

        # 4b. Optimistically stamp ``ai_review_in_progress: True`` onto
        # the shell so the Kanban moves the card from "In queue" →
        # "AI thinking" on the next refetch (~1.5s), instead of waiting
        # for the background orchestrator to land its first metadata
        # write. The orchestrator clears this flag on completion (both
        # success and failure paths in agents/orchestrator.py).
        # Read-modify-write preserves cluster_id / auto_promoted /
        # dominant_category from the shell.
        #
        # Rerun path (correction_note present): also clear the project's
        # existing topics / decisions / relationships so the orchestrator
        # writes onto a blank canvas instead of stacking new rows on top
        # of the previous draft. The
        # orchestrator_run_id is left in metadata until the new run
        # stamps over it; that's harmless because the new run starts
        # immediately.
        shell_md = dict(md)
        shell_md["ai_review_in_progress"] = True
        with store._connect() as connection:
            if correction_note_preview:
                connection.execute(
                    "DELETE FROM topics WHERE project_id = ?",
                    (project_id,),
                )
                connection.execute(
                    "DELETE FROM relationships WHERE project_id = ?",
                    (project_id,),
                )
                connection.execute(
                    "DELETE FROM decisions WHERE project_id = ?",
                    (project_id,),
                )
            connection.execute(
                "UPDATE v2_projects SET metadata_json = ?, updated_at = ? "
                "WHERE project_id = ?",
                (json.dumps(shell_md), now_timestamp(), project_id),
            )
            connection.commit()

        # 5. Spawn orchestrator.
        run_id, _is_new = await _spawn_orchestrator_run(
            store,
            request=request,
            workspace_id=member.workspace_id,
            user_id=member.user_id,
            prioritization_run_id=prio_run_id,
            top_n=1,
        )

        return {
            "run_id": run_id,
            "project_id": project_id,
            "status": "thinking",
        }

    return router, projects_router


# ---------------------------------------------------------------------
# Module-level helpers shared by /run and /promote-from-cluster, and by
# /runs/{run_id}/events and /projects/{project_id}/events.
#
# These are FastAPI-bound (they read/write request.app.state) so they
# don't belong in orchestrator_store.py. They're module-level rather
# than nested in make_orchestrator_router so unit tests can call them
# directly with a stub Request if needed.
# ---------------------------------------------------------------------


async def _spawn_orchestrator_run(
    store: "PlanningStudioStore",
    *,
    request: Request,
    workspace_id: str,
    user_id: str,
    prioritization_run_id: str,
    top_n: int,
) -> tuple[str, bool]:
    """Idempotent spawn of an orchestrator run + queue + task registration.

    Returns ``(run_id, is_new)``. When ``is_new`` is False, the caller
    skips the "spawning" UX bit (run is already in flight from an earlier
    request).
    """
    run_id, is_new = create_orchestrator_run(
        store,
        workspace_id=workspace_id,
        prioritization_run_id=prioritization_run_id,
        triggered_by=user_id,
        top_n=top_n,
    )
    if not is_new:
        return run_id, False

    # Register an event queue for SSE subscribers.
    queues: dict[str, asyncio.Queue[tuple[str, dict[str, Any]]]] = (
        getattr(request.app.state, "orchestrator_event_queues", None)
        or {}
    )
    request.app.state.orchestrator_event_queues = queues
    queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue()
    queues[run_id] = queue

    # Spawn the orchestrator. Keep the task in app.state so the SSE
    # endpoint can wait on it if needed (and so it isn't GC'd).
    async def _run_with_cleanup() -> None:
        try:
            await orchestrator_agent.run(
                store,
                workspace_id=workspace_id,
                orchestrator_run_id=run_id,
                prioritization_run_id=prioritization_run_id,
                top_n=top_n,
                event_queue=queue,
                pre_warm_artifacts=True,
            )
        finally:
            # Pop the queue so subsequent GET /events calls fall through
            # to the trimmed replay path. Best-effort.
            queues.pop(run_id, None)

    tasks: dict[str, asyncio.Task[None]] = (
        getattr(request.app.state, "orchestrator_tasks", None) or {}
    )
    request.app.state.orchestrator_tasks = tasks
    tasks[run_id] = asyncio.create_task(
        _run_with_cleanup(), name=f"orchestrator-run-{run_id}"
    )
    return run_id, True


def _orchestrator_event_stream_response(
    store: "PlanningStudioStore",
    *,
    request: Request,
    workspace_id: str,
    run_id: str,
) -> StreamingResponse:
    """Build the SSE response for an orchestrator run.

    Live path (queue exists in app.state) → stream from queue with 60s
    heartbeats. Replay path (run finished, queue popped) → stream the
    trimmed replay events from ``orchestrator_agent.build_replay_events``.

    Authorizes that the run belongs to ``workspace_id`` before returning
    a stream. 404 on cross-workspace (don't leak existence).
    """
    orch = get_orchestrator_run(
        store,
        workspace_id=workspace_id,
        run_id=run_id,
    )
    if orch is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "orchestrator_run_not_found",
                "message": run_id,
            },
        )
    queues: dict[str, asyncio.Queue[tuple[str, dict[str, Any]]]] = (
        getattr(request.app.state, "orchestrator_event_queues", None)
        or {}
    )
    queue = queues.get(run_id)

    async def _live_stream() -> AsyncIterator[str]:
        assert queue is not None
        terminal_events = {"orchestrator.completed", "error"}
        while True:
            try:
                event, payload = await asyncio.wait_for(
                    queue.get(), timeout=60.0,
                )
            except asyncio.TimeoutError:
                # Heartbeat keeps the connection open through proxies.
                yield format_sse(
                    "heartbeat",
                    {"run_id": run_id, "status": "running"},
                )
                continue
            yield format_sse(event, payload)
            if event in terminal_events:
                return

    async def _replay_stream() -> AsyncIterator[str]:
        events = orchestrator_agent.build_replay_events(
            store,
            workspace_id=workspace_id,
            orchestrator_run_id=run_id,
        )
        for event, payload in events:
            yield format_sse(event, payload)

    if queue is not None:
        return sse_stream(_live_stream())
    return sse_stream(_replay_stream())


def _find_promoted_v2_project(
    store: "PlanningStudioStore",
    *,
    workspace_id: str,
    orchestrator_run_id: str,
    theme_id: str,
) -> dict[str, Any] | None:
    """Locate the v2_projects row the orchestrator's ``_create_canvas`` writes for this run.

    The orchestrator stamps ``metadata_json`` with ``orchestrator_run_id`` + ``theme_id``
    immediately on canvas creation (within ~50ms of orchestrator spawn,
    well before any LLM call). This polling helper scans the workspace's
    most-recent canvases and returns the matching one.

    LIMIT 50 is generous headroom — the new canvas is typically the most
    recently created row in the workspace by the time this fires; the
    cap exists to bound the scan even on a workspace with bursts of
    parallel orchestrator runs.

    Returns the project dict (with ``metadata`` parsed) or None if not
    yet visible. Reads rows by index because the SELECT column order is
    fixed and we want zero dependency on the connection's ``row_factory``
    setting.
    """
    with store._connect() as connection:
        rows = connection.execute(
            "SELECT project_id, user_id, title, metadata_json, "
            "created_at, updated_at, archived_at, deleted_at, shelf_id, "
            "workspace_id, project_state, priority_order, roi_score "
            "FROM v2_projects "
            "WHERE workspace_id = ? AND deleted_at IS NULL "
            "ORDER BY created_at DESC LIMIT 50",
            (workspace_id,),
        ).fetchall()
    for row in rows:
        try:
            metadata = json.loads(row[3] or "{}")
        except (TypeError, ValueError):
            continue
        if (
            metadata.get("orchestrator_run_id") == orchestrator_run_id
            and metadata.get("theme_id") == theme_id
        ):
            return {
                "project_id": row[0],
                "user_id": row[1],
                "title": row[2],
                "metadata": metadata,
                "created_at": row[4],
                "updated_at": row[5],
                "archived_at": row[6],
                "deleted_at": row[7],
                "shelf_id": row[8],
                "workspace_id": row[9],
                "project_state": row[10],
                "priority_order": row[11],
                "roi_score": row[12],
            }
    return None


def _run_prioritization_with_run_id(
    store: "PlanningStudioStore",
    *,
    workspace_id: str,
    run_id: str,
) -> None:
    """BackgroundTasks worker: complete an already-created prioritization_runs row.

    Runs F6's scoring + theme backfill against the existing run_id
    (the route created the row pre-emptively to give the client a
    polling handle). Any exception here is caught by F6 itself and
    written as ``status='error'`` on the row.
    """
    try:
        # Re-read the input snapshot from the row — the route already
        # rolled-up the cluster list. F6's `run()` would re-create a
        # fresh row; we instead invoke the scoring path directly so
        # the existing run_id is reused.
        prio = get_prioritization_run(
            store,
            workspace_id=workspace_id,
            run_id=run_id,
        )
        if prio is None:
            logger.warning(
                "_run_prioritization_with_run_id: run %s vanished",
                run_id,
            )
            return
        from .agents.prioritization import (  # noqa: PLC0415
            _backfill_cluster_themes,
            rank_clusters,
        )
        from .feedback_items.cluster import list_clusters_with_distribution

        clusters = list_clusters_with_distribution(
            store, workspace_id=workspace_id
        )
        clusters_by_id = {c["cluster_id"]: c for c in clusters}
        ranked, model_used = rank_clusters(clusters)
        for entry in ranked:
            cluster = clusters_by_id.get(entry["cluster_id"], {})
            entry["provenance"] = {
                "item_count": cluster.get("item_count", 0),
                "category_counts": cluster.get("category_counts", {}),
                "most_recent_ingested_at": cluster.get(
                    "most_recent_ingested_at"
                ),
                "sample_item_ids": cluster.get("sample_item_ids", []),
            }
        _backfill_cluster_themes(
            store,
            workspace_id=workspace_id,
            ranked=ranked,
            clusters_by_id=clusters_by_id,
        )
        complete_prioritization_run(
            store,
            workspace_id=workspace_id,
            run_id=run_id,
            output={
                "themes": ranked,
                "model": model_used,
                "input_cluster_count": len(clusters),
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "_run_prioritization_with_run_id failed (run_id=%s)", run_id,
        )
        try:
            complete_prioritization_run(
                store,
                workspace_id=workspace_id,
                run_id=run_id,
                error=f"{type(exc).__name__}: {exc}",
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "also failed to mark prio run errored (run_id=%s)", run_id
            )
