"""Tests for the Artifact Viewer endpoints.

Three routes under test:
- ``POST /api/v2/projects/{id}/artifact/generate/stream``
- ``GET  /api/v2/projects/{id}/artifact``
- ``POST /api/v2/projects/{id}/artifact/edit/stream``

Coverage focus:
- State gate: ``project_state == "approved"`` is required (409 otherwise).
- Domain gate: software-adjacent only (422 otherwise).
- Entitlements gate: Pro+ only (402 otherwise).
- Tier dispatch: FRONTIER + ENTERPRISE route to Claude with
  ``model_override=CLAUDE_CODEGEN_MODEL``; BASE/PRO route to OpenAI.
- Fallback: FRONTIER without an Anthropic adapter falls through to
  OpenAI rather than 500'ing.
- Cross-workspace GET returns 404 (never 403 — don't leak existence).
- Persistence: generate writes both a scaffold row AND the
  ``metadata.artifact`` overlay; edit appends user + assistant chat
  turns and bumps ``latest_scaffold_id``.

SSE plumbing reuses the ``_parse_sse_frames`` helper pattern from
``test_streaming_routes.py`` — verbatim copy here so the file is
self-contained.
"""
from __future__ import annotations

import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

try:
    from ._helpers import (
        fake_kickoff_response,
        make_test_app,
        signup_and_login,
    )
except ImportError:  # pragma: no cover
    from _helpers import (  # type: ignore[no-redef]
        fake_kickoff_response,
        make_test_app,
        signup_and_login,
    )

from planning_studio_service.agents.tiers import CLAUDE_CODEGEN_MODEL
from planning_studio_service.billing import NoopBillingProvider


def _parse_sse_frames(body: str) -> list[tuple[str, dict]]:
    """Split an SSE response body into (event_name, json_payload) pairs."""
    frames: list[tuple[str, dict]] = []
    for raw in body.split("\n\n"):
        if not raw.strip():
            continue
        event = "message"
        data_lines: list[str] = []
        for line in raw.split("\n"):
            if not line or line.startswith(":"):
                continue
            if ":" not in line:
                continue
            field, _, value = line.partition(":")
            if value.startswith(" "):
                value = value[1:]
            if field == "event":
                event = value
            elif field == "data":
                data_lines.append(value)
        if data_lines:
            frames.append((event, json.loads("\n".join(data_lines))))
    return frames


def _ok_manifest() -> dict:
    return {
        "framework": "react-vite",
        "language": "typescript",
        "files": [
            {"path": "README.md", "content": "# Notes\n"},
            {"path": "src/main.tsx", "content": "console.log('hi')\n"},
        ],
        "readme_preview": "Notes app.",
        "post_install_steps": ["npm install", "npm run dev"],
        "truncation_note": "",
    }


def _ok_edit_manifest() -> dict:
    manifest = _ok_manifest()
    manifest["explanation"] = (
        "I added a 100ms debounce on the storage event listener."
    )
    return manifest


def _seed_software_project(
    client, kickoff_adapter, project_id: str,
) -> None:
    """Kickoff a software-domain project so the artifact endpoint's
    domain gate passes.

    Note: kickoff'd projects default to ``project_state='pending_review'``
    via the SQL CHECK column default — callers that want the state gate
    to pass must follow up with :func:`_set_state(project_id,
    "approved")` to walk the row to terminal state. Doing this via raw
    SQL rather than ``update_v2_project_state`` because the store helper
    requires a real ``workspace_id`` and these test rows aren't
    workspace-scoped.
    """
    response = fake_kickoff_response()
    response["domain"] = "software"
    kickoff_adapter.kickoff.return_value = response
    resp = client.post(
        f"/api/v2/projects/{project_id}/kickoff",
        json={"user_idea": "A note-taking app."},
    )
    resp.raise_for_status()


def _set_state(store, project_id: str, state: str) -> None:
    """Flip ``project_state`` directly via SQL. Test-only setup helper."""
    with store._connect() as conn:
        conn.execute(
            "UPDATE v2_projects SET project_state = ? WHERE project_id = ?",
            (state, project_id),
        )
        conn.commit()


class _BaseArtifactTest(unittest.TestCase):
    """Shared test fixture: signed-in Pro user with a software project."""

    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        signup_and_login(self.client, email="artifact@example.com")
        me = self.client.get("/api/auth/me").json()
        self.user_id = me["user_id"]
        # Pro tier unlocks the scaffold feature flag (also gates the
        # artifact endpoint via the same `_entitlements.has_feature`
        # check).
        NoopBillingProvider().record_local_subscription(
            user_id=self.user_id, plan_slug="pro", store=self.store,
        )
        self.scaffold_adapter = MagicMock()
        self.client.app.state.code_scaffold_adapter = self.scaffold_adapter
        # Default: no Claude adapter wired (BASE/PRO path or
        # FRONTIER/Enterprise fallback). Tests that exercise the
        # Claude path inject a separate mock.
        self.client.app.state.claude_code_scaffold_adapter = None

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _make_approved_project(self, project_id: str = "proj-art") -> str:
        _seed_software_project(self.client, self.adapter, project_id)
        _set_state(self.store, project_id, "approved")
        return project_id


class GenerateStreamGatesTests(_BaseArtifactTest):
    """Pre-flight gates fire before the LLM is touched."""

    def test_unapproved_project_can_generate(self) -> None:
        """Product decision: the artifact (code) IS the
        thing that gets approved — not the canvas. The viewer must be
        generatable at any project_state. Pre-reframe this returned
        409 project_not_approved; now it succeeds (assuming software
        domain + entitlements pass).
        """
        # Kickoff lands at 'pending_review' (the SQL default); skip the
        # _make_approved_project helper's flip-to-approved step.
        _seed_software_project(
            self.client, self.adapter, "proj-art-pending",
        )
        # Stub the scaffold adapter so the SSE stream returns quickly.
        self.scaffold_adapter.generate.return_value = iter([
            {"type": "complete", "files": []},
        ])
        response = self.client.post(
            "/api/v2/projects/proj-art-pending/artifact/generate/stream",
        )
        # The state gate is gone — pre-approval projects no longer 409.
        self.assertNotEqual(response.status_code, 409, response.text)

    def test_non_software_domain_can_generate(self) -> None:
        """Product decision: artifact (code) is the
        deliverable for every workspace project. The legacy
        software-domain gate is gone — autonomous-pipeline projects
        with no metadata.domain (and even kickoff-driven projects
        with non-software domains like 'event') now pass straight
        through to the entitlement check.
        """
        self.adapter.kickoff.return_value = fake_kickoff_response()
        self.client.post(
            "/api/v2/projects/proj-art-event/kickoff",
            json={"user_idea": "A small wine festival."},
        )
        self.scaffold_adapter.generate.return_value = iter([
            {"type": "complete", "files": []},
        ])
        response = self.client.post(
            "/api/v2/projects/proj-art-event/artifact/generate/stream",
        )
        # Domain gate is gone — non-software projects no longer 422.
        self.assertNotEqual(response.status_code, 422, response.text)

    def test_free_user_returns_402_upgrade_required(self) -> None:
        # Wipe the Pro subscription set up in _BaseArtifactTest.setUp
        # so the scaffold entitlement gate fires.
        with self.store._connect() as conn:
            conn.execute(
                "DELETE FROM subscriptions WHERE user_id = ?",
                (self.user_id,),
            )
            conn.commit()
        project_id = self._make_approved_project("proj-art-free")
        response = self.client.post(
            f"/api/v2/projects/{project_id}/artifact/generate/stream",
        )
        self.assertEqual(response.status_code, 402, response.text)
        detail = response.json()["detail"]
        self.assertEqual(detail["error"], "upgrade_required")
        self.assertEqual(detail["min_plan"], "pro")
        self.scaffold_adapter.generate.assert_not_called()


class GenerateStreamHappyPathTests(_BaseArtifactTest):
    """Happy path persists scaffold + artifact overlay + emits complete."""

    def test_emits_heartbeat_then_complete_and_persists(self) -> None:
        self.scaffold_adapter.generate.return_value = _ok_manifest()
        project_id = self._make_approved_project("proj-art-happy")

        with self.client.stream(
            "POST",
            f"/api/v2/projects/{project_id}/artifact/generate/stream",
        ) as response:
            self.assertEqual(response.status_code, 200)
            body = "".join(response.iter_text())

        frames = _parse_sse_frames(body)
        self.assertEqual(frames[0][0], "heartbeat")
        complete = next(f for f in frames if f[0] == "complete")
        envelope = complete[1]["artifact"]
        self.assertEqual(envelope["framework"], "react-vite")
        self.assertEqual(len(envelope["files"]), 2)
        self.assertGreaterEqual(len(envelope["messages"]), 1)
        self.assertEqual(envelope["messages"][0]["role"], "assistant")

        # Scaffold row landed.
        rows = self.store.list_scaffolds_for_project(
            project_id=project_id, user_id=self.user_id,
        )
        self.assertEqual(len(rows), 1)
        # Overlay landed.
        overlay = self.store.get_v2_project_artifact(project_id=project_id)
        self.assertIsNotNone(overlay)
        self.assertEqual(overlay["latest_scaffold_id"], rows[0]["scaffold_id"])
        self.assertEqual(len(overlay["messages"]), 1)


class GenerateStreamRepoContextTests(_BaseArtifactTest):
    """Wave F.1 / #147: route fetches repo_context and threads it to the adapter.

    Patch target: ``planning_studio_service.connectors.github.repo_context.fetch_repo_context``
    — the route uses a function-local import inside ``_generator()`` (mirrors
    the ``orchestrator_router.py:473-483`` pattern), so there is no
    ``planning_studio_service.api.fetch_repo_context`` symbol to patch.
    """

    _FAKE_REPO_CONTEXT = {
        "repo_full_name": "acme/widget-app",
        "default_branch": "main",
        "head_sha": "abc1234",
        "top_level_files": [{"path": "package.json", "type": "file"}],
        "readme_excerpt": "# Widget App",
        "manifest_kind": "package.json",
        "manifest_excerpt": '{"name": "widget-app"}',
        "fetched_at": "2026-05-13T00:00:00Z",
    }

    def _set_workspace_id(self, project_id: str, workspace_id: str) -> None:
        """Backfill workspace_id directly via SQL (kickoff path leaves it
        NULL; the route's repo_context fetch is gated on a non-empty
        workspace_id, so tests exercising that branch must seed it)."""
        with self.store._connect() as conn:
            conn.execute(
                "UPDATE v2_projects SET workspace_id = ? WHERE project_id = ?",
                (workspace_id, project_id),
            )
            conn.commit()

    def test_passes_repo_context_to_adapter_when_fetch_succeeds(self) -> None:
        self.scaffold_adapter.generate.return_value = _ok_manifest()
        project_id = self._make_approved_project("proj-art-rc-ok")
        self._set_workspace_id(project_id, "ws-rc-ok")

        with patch(
            "planning_studio_service.connectors.github.repo_context."
            "fetch_repo_context",
            new=AsyncMock(return_value=self._FAKE_REPO_CONTEXT),
        ) as mock_fetch:
            with self.client.stream(
                "POST",
                f"/api/v2/projects/{project_id}/artifact/generate/stream",
            ) as response:
                self.assertEqual(response.status_code, 200)
                "".join(response.iter_text())

        mock_fetch.assert_awaited_once()
        # Workspace-id keyword forwarded; timeout is the contract default.
        await_kwargs = mock_fetch.await_args.kwargs
        self.assertIn("workspace_id", await_kwargs)
        self.assertEqual(await_kwargs.get("timeout_s"), 12.0)

        # Adapter received the same repo_context dict.
        gen_kwargs = self.scaffold_adapter.generate.call_args.kwargs
        self.assertEqual(gen_kwargs.get("repo_context"), self._FAKE_REPO_CONTEXT)

    def test_passes_none_when_fetch_returns_none(self) -> None:
        """No GitHub connector wired → fetch returns None → adapter gets None."""
        self.scaffold_adapter.generate.return_value = _ok_manifest()
        project_id = self._make_approved_project("proj-art-rc-none")
        self._set_workspace_id(project_id, "ws-rc-none")

        with patch(
            "planning_studio_service.connectors.github.repo_context."
            "fetch_repo_context",
            new=AsyncMock(return_value=None),
        ):
            with self.client.stream(
                "POST",
                f"/api/v2/projects/{project_id}/artifact/generate/stream",
            ) as response:
                self.assertEqual(response.status_code, 200)
                "".join(response.iter_text())

        gen_kwargs = self.scaffold_adapter.generate.call_args.kwargs
        self.assertIsNone(gen_kwargs.get("repo_context"))

    def test_handles_repo_context_fetch_failure_gracefully(self) -> None:
        """If fetch_repo_context raises unexpectedly, the route logs and
        proceeds with repo_context=None — does NOT 5xx, does NOT abort
        the SSE stream. Defense-in-depth on top of fetch_repo_context's
        own try/except (mirrors orchestrator_router.py:475-483).
        """
        self.scaffold_adapter.generate.return_value = _ok_manifest()
        project_id = self._make_approved_project("proj-art-rc-fail")
        self._set_workspace_id(project_id, "ws-rc-fail")

        with patch(
            "planning_studio_service.connectors.github.repo_context."
            "fetch_repo_context",
            new=AsyncMock(side_effect=RuntimeError("upstream broken")),
        ):
            with self.client.stream(
                "POST",
                f"/api/v2/projects/{project_id}/artifact/generate/stream",
            ) as response:
                self.assertEqual(response.status_code, 200)
                body = "".join(response.iter_text())

        # Stream still emits a complete frame — degrade-gracefully contract.
        frames = _parse_sse_frames(body)
        self.assertTrue(
            any(f[0] == "complete" for f in frames),
            f"expected complete frame, got events: {[f[0] for f in frames]}",
        )

        # Adapter still called, with repo_context=None.
        gen_kwargs = self.scaffold_adapter.generate.call_args.kwargs
        self.assertIsNone(gen_kwargs.get("repo_context"))


class GenerateStreamTierDispatchTests(_BaseArtifactTest):
    """Tier dispatch routes FRONTIER/ENTERPRISE to Claude (closes #118 path)."""

    def _claude_mock(self) -> MagicMock:
        mock = MagicMock()
        mock.generate.return_value = _ok_manifest()
        mock.edit.return_value = _ok_edit_manifest()
        return mock

    def test_pro_tier_uses_openai_with_pro_model(self) -> None:
        self.scaffold_adapter.generate.return_value = _ok_manifest()
        # PRO is the default for "pro" plan — explicit set is defensive
        # if DEFAULT_TIER_BY_PLAN ever shifts.
        self.store.set_preferred_model_tier(self.user_id, "pro")
        project_id = self._make_approved_project("proj-art-pro")
        with self.client.stream(
            "POST",
            f"/api/v2/projects/{project_id}/artifact/generate/stream",
        ) as response:
            "".join(response.iter_text())
        kwargs = self.scaffold_adapter.generate.call_args.kwargs
        self.assertEqual(kwargs["model_override"], "gpt-5")

    def test_frontier_routes_to_claude_with_codegen_model(self) -> None:
        # Bump plan to team (Frontier-allowed) and persist a Frontier
        # preference so resolve_tier_for_user lands on FRONTIER.
        NoopBillingProvider().record_local_subscription(
            user_id=self.user_id, plan_slug="team", store=self.store,
        )
        self.store.set_preferred_model_tier(self.user_id, "frontier")

        claude_mock = self._claude_mock()
        self.client.app.state.claude_code_scaffold_adapter = claude_mock

        project_id = self._make_approved_project("proj-art-frontier")
        with self.client.stream(
            "POST",
            f"/api/v2/projects/{project_id}/artifact/generate/stream",
        ) as response:
            "".join(response.iter_text())

        # OpenAI adapter must NOT have been touched on the Claude path.
        self.scaffold_adapter.generate.assert_not_called()
        # Claude adapter received the call with Opus 4.7 pinned.
        self.assertTrue(claude_mock.generate.called)
        kwargs = claude_mock.generate.call_args.kwargs
        self.assertEqual(kwargs["model_override"], CLAUDE_CODEGEN_MODEL)

    # NOTE: an end-to-end "ENTERPRISE plan + ENTERPRISE tier → Claude
    # with CLAUDE_CODEGEN_MODEL" assertion requires an ``enterprise``
    # plan slug in ``billing/plans.py`` (a separate product change).
    # The visible #118 closure for ENTERPRISE routing lives in
    # ``test_model_tiers.test_tier_to_adapter_enterprise_routes_to_claude``;
    # the FRONTIER → Claude assertion above exercises the same dispatch
    # branch (``tier in (FRONTIER, ENTERPRISE)`` in ``tier_to_adapter``)
    # via the production-purchasable plan path.

    def test_frontier_falls_back_to_openai_when_claude_not_wired(self) -> None:
        # Claude adapter explicitly unwired (None) — tier dispatch must
        # not 500. Falls through to OpenAI with FRONTIER's gpt-5.5
        # fallback model.
        NoopBillingProvider().record_local_subscription(
            user_id=self.user_id, plan_slug="team", store=self.store,
        )
        self.store.set_preferred_model_tier(self.user_id, "frontier")
        self.client.app.state.claude_code_scaffold_adapter = None

        self.scaffold_adapter.generate.return_value = _ok_manifest()
        project_id = self._make_approved_project("proj-art-fallback")
        with self.client.stream(
            "POST",
            f"/api/v2/projects/{project_id}/artifact/generate/stream",
        ) as response:
            "".join(response.iter_text())

        self.assertTrue(self.scaffold_adapter.generate.called)
        kwargs = self.scaffold_adapter.generate.call_args.kwargs
        self.assertEqual(kwargs["model_override"], "gpt-5.5")


class GetArtifactTests(_BaseArtifactTest):
    """``GET /api/v2/projects/{id}/artifact`` returns hydrated files."""

    def test_returns_404_when_no_artifact_yet(self) -> None:
        project_id = self._make_approved_project("proj-art-empty")
        response = self.client.get(
            f"/api/v2/projects/{project_id}/artifact",
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json()["detail"]["error"], "artifact_not_generated",
        )

    def test_hydrates_files_from_latest_scaffold(self) -> None:
        # Generate first to populate.
        self.scaffold_adapter.generate.return_value = _ok_manifest()
        project_id = self._make_approved_project("proj-art-hydrate")
        with self.client.stream(
            "POST",
            f"/api/v2/projects/{project_id}/artifact/generate/stream",
        ) as response:
            "".join(response.iter_text())

        response = self.client.get(
            f"/api/v2/projects/{project_id}/artifact",
        )
        self.assertEqual(response.status_code, 200)
        artifact = response.json()["artifact"]
        self.assertEqual(artifact["framework"], "react-vite")
        self.assertEqual(len(artifact["files"]), 2)
        paths = [f["path"] for f in artifact["files"]]
        self.assertIn("README.md", paths)

    def test_cross_user_request_returns_404_not_403(self) -> None:
        # Owner generates an artifact.
        self.scaffold_adapter.generate.return_value = _ok_manifest()
        project_id = self._make_approved_project("proj-art-cross")
        with self.client.stream(
            "POST",
            f"/api/v2/projects/{project_id}/artifact/generate/stream",
        ) as response:
            "".join(response.iter_text())

        # Sign in as a second user and try to read the same project.
        # Don't leak existence — must be 404, not 403.
        self.client.post("/api/auth/logout")
        signup_and_login(self.client, email="other@example.com")
        response = self.client.get(
            f"/api/v2/projects/{project_id}/artifact",
        )
        self.assertEqual(response.status_code, 404)


class EditStreamTests(_BaseArtifactTest):
    """Chat-driven edit appends turns + bumps latest_scaffold_id."""

    def test_edit_without_artifact_returns_409(self) -> None:
        project_id = self._make_approved_project("proj-art-edit-empty")
        response = self.client.post(
            f"/api/v2/projects/{project_id}/artifact/edit/stream",
            json={"message": "Add a debounce."},
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.json()["detail"]["error"], "artifact_not_generated",
        )

    def test_edit_appends_chat_turns_and_persists_new_scaffold(self) -> None:
        # Seed an artifact first.
        self.scaffold_adapter.generate.return_value = _ok_manifest()
        project_id = self._make_approved_project("proj-art-edit")
        with self.client.stream(
            "POST",
            f"/api/v2/projects/{project_id}/artifact/generate/stream",
        ) as response:
            "".join(response.iter_text())
        first_overlay = self.store.get_v2_project_artifact(project_id=project_id)
        first_scaffold_id = first_overlay["latest_scaffold_id"]

        # Now edit.
        self.scaffold_adapter.edit.return_value = _ok_edit_manifest()
        with self.client.stream(
            "POST",
            f"/api/v2/projects/{project_id}/artifact/edit/stream",
            json={"message": "Add a 100ms debounce."},
        ) as response:
            self.assertEqual(response.status_code, 200)
            body = "".join(response.iter_text())

        frames = _parse_sse_frames(body)
        complete = next(f for f in frames if f[0] == "complete")
        envelope = complete[1]["artifact"]
        # Two new chat messages should be present (user + assistant).
        roles = [m["role"] for m in envelope["messages"]]
        self.assertIn("user", roles)
        self.assertIn("assistant", roles)
        # Assistant message body comes from manifest.explanation.
        assistant_msg = next(
            m for m in envelope["messages"] if m["role"] == "assistant"
            and "100ms debounce" in m["body"]
        )
        self.assertIn("100ms debounce", assistant_msg["body"])

        # latest_scaffold_id must advance — edit creates a new row.
        new_overlay = self.store.get_v2_project_artifact(project_id=project_id)
        self.assertNotEqual(
            new_overlay["latest_scaffold_id"], first_scaffold_id,
        )

    def test_edit_validates_message_min_length(self) -> None:
        # Pydantic body validator rejects empty strings BEFORE the
        # endpoint sees the request — short-circuits with 422.
        self.scaffold_adapter.generate.return_value = _ok_manifest()
        project_id = self._make_approved_project("proj-art-edit-empty-msg")
        with self.client.stream(
            "POST",
            f"/api/v2/projects/{project_id}/artifact/generate/stream",
        ) as response:
            "".join(response.iter_text())

        response = self.client.post(
            f"/api/v2/projects/{project_id}/artifact/edit/stream",
            json={"message": ""},
        )
        self.assertEqual(response.status_code, 422)


class PatchFileAutosaveTests(_BaseArtifactTest):
    """``PATCH /api/v2/projects/{id}/artifact/files`` autosaves one file."""

    def _generate(self, project_id: str) -> None:
        self.scaffold_adapter.generate.return_value = _ok_manifest()
        with self.client.stream(
            "POST",
            f"/api/v2/projects/{project_id}/artifact/generate/stream",
        ) as response:
            "".join(response.iter_text())

    def test_autosave_persists_edit(self) -> None:
        # State is approved (default seed flips it), but autosave only
        # allows pending_review/rejected/summary_ready — flip back.
        project_id = self._make_approved_project("proj-art-save")
        self._generate(project_id)
        _set_state(self.store, project_id, "pending_review")

        response = self.client.patch(
            f"/api/v2/projects/{project_id}/artifact/files",
            json={"path": "README.md", "content": "# Edited\n"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["ok"])

        # Re-fetch and confirm the edit landed.
        get_resp = self.client.get(
            f"/api/v2/projects/{project_id}/artifact",
        )
        files = get_resp.json()["artifact"]["files"]
        readme = next(f for f in files if f["path"] == "README.md")
        self.assertEqual(readme["content"], "# Edited\n")

    def test_autosave_locked_when_in_review(self) -> None:
        project_id = self._make_approved_project("proj-art-locked")
        self._generate(project_id)
        # Project is approved (helper); shift to in_review explicitly.
        _set_state(self.store, project_id, "in_review")

        response = self.client.patch(
            f"/api/v2/projects/{project_id}/artifact/files",
            json={"path": "README.md", "content": "# Should not save\n"},
        )
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(
            response.json()["detail"]["error"], "project_locked",
        )

    def test_autosave_404_when_artifact_missing(self) -> None:
        # Project exists but no artifact has been generated yet.
        project_id = self._make_approved_project("proj-art-noart")
        _set_state(self.store, project_id, "pending_review")

        response = self.client.patch(
            f"/api/v2/projects/{project_id}/artifact/files",
            json={"path": "README.md", "content": "x"},
        )
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(
            response.json()["detail"]["error"], "artifact_not_generated",
        )

    def test_autosave_400_on_missing_path(self) -> None:
        project_id = self._make_approved_project("proj-art-badpath")
        self._generate(project_id)
        _set_state(self.store, project_id, "pending_review")

        response = self.client.patch(
            f"/api/v2/projects/{project_id}/artifact/files",
            json={"content": "x"},
        )
        self.assertEqual(response.status_code, 400, response.text)


class IdempotencyTests(_BaseArtifactTest):
    """``POST /artifact/generate/stream`` should not re-run the LLM when a
    manifest is already persisted and the caller didn't ask for a forced
    regen. Pins the impatient-race window described in issues-log #187."""

    def _first_generate(self, project_id: str) -> int:
        self.scaffold_adapter.generate.return_value = _ok_manifest()
        with self.client.stream(
            "POST",
            f"/api/v2/projects/{project_id}/artifact/generate/stream",
        ) as response:
            "".join(response.iter_text())
        return self.scaffold_adapter.generate.call_count

    def test_returns_cached_complete_event_when_not_force(self) -> None:
        project_id = self._make_approved_project("proj-art-cached")
        first_count = self._first_generate(project_id)
        self.assertEqual(first_count, 1)

        # Second POST without a body — should replay the cached
        # manifest as a single complete event and NOT re-fire adapter.
        with self.client.stream(
            "POST",
            f"/api/v2/projects/{project_id}/artifact/generate/stream",
        ) as response:
            self.assertEqual(response.status_code, 200)
            body = "".join(response.iter_text())
            cached_header = response.headers.get("x-llm-mode")

        frames = _parse_sse_frames(body)
        complete_frames = [f for f in frames if f[0] == "complete"]
        self.assertEqual(
            len(complete_frames), 1,
            "Cached replay must emit exactly one complete event",
        )
        envelope = complete_frames[0][1]["artifact"]
        # Cached envelope mirrors the success-path shape so the FE
        # ssePost reader resolves identically.
        self.assertEqual(envelope["framework"], "react-vite")
        self.assertEqual(len(envelope["files"]), 2)
        # adapter.generate must NOT have fired again.
        self.assertEqual(
            self.scaffold_adapter.generate.call_count, first_count,
        )
        # Telemetry: cached path advertises itself via x-llm-mode.
        self.assertEqual(cached_header, "cached")

    def test_force_true_triggers_fresh_regen(self) -> None:
        project_id = self._make_approved_project("proj-art-force")
        first_count = self._first_generate(project_id)
        self.assertEqual(first_count, 1)

        # Second POST WITH force=true — must re-fire the adapter.
        with self.client.stream(
            "POST",
            f"/api/v2/projects/{project_id}/artifact/generate/stream",
            json={"force": True},
        ) as response:
            self.assertEqual(response.status_code, 200)
            "".join(response.iter_text())

        self.assertEqual(
            self.scaffold_adapter.generate.call_count, first_count + 1,
            "force=true must bypass the cached-manifest early-return",
        )

    def test_explicit_force_false_returns_cached(self) -> None:
        # Explicit ``force=false`` should behave the same as omitting
        # the body — the FE's auto-fire path sends this shape.
        project_id = self._make_approved_project("proj-art-explicit")
        first_count = self._first_generate(project_id)

        with self.client.stream(
            "POST",
            f"/api/v2/projects/{project_id}/artifact/generate/stream",
            json={"force": False},
        ) as response:
            "".join(response.iter_text())

        self.assertEqual(
            self.scaffold_adapter.generate.call_count, first_count,
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
