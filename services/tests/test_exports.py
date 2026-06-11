"""HTTP-level + builder tests for the W2 κ exports surface.

Covers:
- GET / PUT /api/v2/connectors/{provider}/destination
- POST /api/v2/projects/{id}/export/linear
- POST /api/v2/projects/{id}/export/github
- builders.build_issue_body shape

External APIs mocked via ``httpx.MockTransport``; no network IO.
"""
from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import httpx

from planning_studio_service.byok import encrypt_api_key
from planning_studio_service.connectors import store as connectors_store
from planning_studio_service.exports import builders

try:
    from ._helpers import make_test_app, signup_and_login
except ImportError:
    from _helpers import make_test_app, signup_and_login  # type: ignore[no-redef]


def _setup_workspace_and_project(test_case):
    """Boilerplate: signup, create workspace, create v2 project + topics +
    decisions. Returns (workspace_id, project_id)."""
    signup_and_login(
        test_case.client,
        email="admin@acme.com",
        password="password123",
        display_name="Admin",
    )
    ws = test_case.client.post(
        "/api/v2/workspaces",
        json={"slug": "acme-corp", "name": "Acme Corp"},
    )
    workspace_id = ws.json()["workspace"]["workspace_id"]
    user_id = test_case.client.get("/api/auth/me").json()["user_id"]

    project_id = "proj-test-1"
    test_case.store.ensure_project(
        project_id=project_id, user_id=user_id, title="Mobile login broken on iOS Safari",
    )
    # Seed metadata so the issue body has a description + tradeoffs.
    with test_case.store._connect() as connection:
        connection.execute(
            "UPDATE v2_projects SET metadata_json = ? WHERE project_id = ?",
            (
                json.dumps(
                    {
                        "description": (
                            "A regression in iOS Safari 17.4 broke login."
                        ),
                        "tradeoffs": [
                            "Universal fix vs iOS-only fix.",
                            "Hot patch vs scheduled release.",
                        ],
                    }
                ),
                project_id,
            ),
        )
        connection.commit()

    topics = []
    for i, title in enumerate(
        ["Reproduce the bug", "Identify root cause", "Ship to production"]
    ):
        t = test_case.store.create_topic(
            project_id=project_id, title=title, icon="flag", order_index=i,
        )
        topics.append(t)
    for topic in topics:
        test_case.store.create_decision(
            topic_id=topic["topic_id"],
            project_id=project_id,
            statement=f"Decision for {topic['title']}",
            proposed_by="planner",
            status="confirmed",
        )
    return workspace_id, project_id, user_id


def _seed_credential(
    store,
    *,
    workspace_id: str,
    provider: str,
    api_key: str = "lin_api_thisisalongtestkey1234567890",
    installation_id: str | None = None,
) -> None:
    connectors_store.upsert_credential(
        store,
        workspace_id=workspace_id,
        provider=provider,
        encrypted_token=encrypt_api_key(api_key),
        installation_id=installation_id,
        account_login="acme",
        scopes=[],
    )


def _set_destination(
    store,
    *,
    workspace_id: str,
    provider: str,
    metadata: dict,
) -> None:
    connectors_store.set_credential_metadata(
        store,
        workspace_id=workspace_id,
        provider=provider,
        metadata=metadata,
    )


def _patch_async_client(transport: httpx.MockTransport):
    """Inject a MockTransport into every httpx.AsyncClient created
    inside the route handlers (linear_client + github client)."""
    original = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = transport
        return original(*args, **kwargs)

    return patch("httpx.AsyncClient", side_effect=factory)


# =====================================================================
# Destination routes
# =====================================================================


class DestinationRoutesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client, self.store, _, self.temp_dir = make_test_app()
        self.workspace_id, _, _ = _setup_workspace_and_project(self)

    def tearDown(self) -> None:
        del self.temp_dir

    def test_get_destination_unconnected(self) -> None:
        resp = self.client.get("/api/v2/connectors/linear/destination")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertFalse(body["configured"])
        self.assertIsNone(body["display"])
        self.assertIn("Connect linear", body["hint"])

    def test_get_destination_connected_but_unset(self) -> None:
        _seed_credential(
            self.store, workspace_id=self.workspace_id, provider="linear"
        )
        resp = self.client.get("/api/v2/connectors/linear/destination")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertFalse(body["configured"])
        self.assertIn("Pick a Linear team to send issues to", body["hint"])

    def test_put_then_get_linear_destination(self) -> None:
        _seed_credential(
            self.store, workspace_id=self.workspace_id, provider="linear"
        )
        put_resp = self.client.put(
            "/api/v2/connectors/linear/destination",
            json={
                "team_id": "team-uuid",
                "team_name": "Engineering",
                "project_name": "Q2 cycle",
            },
        )
        self.assertEqual(put_resp.status_code, 200, put_resp.text)
        get_resp = self.client.get("/api/v2/connectors/linear/destination")
        body = get_resp.json()
        self.assertTrue(body["configured"])
        self.assertEqual(body["display"], "Engineering · Q2 cycle")

    def test_put_then_get_github_destination(self) -> None:
        _seed_credential(
            self.store,
            workspace_id=self.workspace_id,
            provider="github",
            installation_id="inst-1",
        )
        put_resp = self.client.put(
            "/api/v2/connectors/github/destination",
            json={"owner": "acme-corp", "repo": "acme-platform"},
        )
        self.assertEqual(put_resp.status_code, 200, put_resp.text)
        get_resp = self.client.get("/api/v2/connectors/github/destination")
        body = get_resp.json()
        self.assertTrue(body["configured"])
        self.assertEqual(body["display"], "acme-corp/acme-platform")

    def test_put_destination_missing_required_fields(self) -> None:
        _seed_credential(
            self.store, workspace_id=self.workspace_id, provider="linear"
        )
        resp = self.client.put(
            "/api/v2/connectors/linear/destination",
            json={"team_id": "team-uuid"},  # team_name missing
        )
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.json()["detail"]["error"], "missing_fields")

    def test_put_destination_unknown_provider(self) -> None:
        resp = self.client.put(
            "/api/v2/connectors/asana/destination",
            json={"team_id": "x", "team_name": "y"},
        )
        self.assertEqual(resp.status_code, 404)

    def test_put_destination_when_not_connected(self) -> None:
        # No credential row at all → 409.
        resp = self.client.put(
            "/api/v2/connectors/linear/destination",
            json={"team_id": "team-uuid", "team_name": "Engineering"},
        )
        self.assertEqual(resp.status_code, 409)


# =====================================================================
# Builders (pure Python — no HTTP)
# =====================================================================


class BuildersTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client, self.store, _, self.temp_dir = make_test_app()
        _, self.project_id, _ = _setup_workspace_and_project(self)

    def tearDown(self) -> None:
        del self.temp_dir

    def test_build_full_body(self) -> None:
        body = builders.build_issue_body(
            self.store,
            project_id=self.project_id,
            include_canvas_link=True,
            include_source_feedback=True,
            apply_priority_label=True,
            priority_label="P1",
            canvas_url="http://localhost:5173/p/proj-test-1",
        )
        self.assertEqual(body.title, "Mobile login broken on iOS Safari")
        self.assertIn("## What this addresses", body.body_markdown)
        self.assertIn("## Decisions (3)", body.body_markdown)
        self.assertIn("## Trade-offs considered", body.body_markdown)
        self.assertIn(
            "Linked from Inspira project →", body.body_markdown
        )
        self.assertEqual(len(body.topic_titles), 3)
        self.assertEqual(body.priority_label, "P1")

    def test_optional_sections_off(self) -> None:
        body = builders.build_issue_body(
            self.store,
            project_id=self.project_id,
            include_canvas_link=False,
            include_source_feedback=False,
            apply_priority_label=False,
            priority_label="P1",
            canvas_url="http://localhost:5173/p/proj-test-1",
        )
        self.assertNotIn("Source data", body.body_markdown)
        self.assertNotIn(
            "Linked from Inspira project", body.body_markdown
        )
        self.assertIsNone(body.priority_label)

    def test_github_body_appends_tasks(self) -> None:
        gh_body = builders.github_body_with_tasks(
            "## Decisions\n\n- one", topic_titles=["A", "B"]
        )
        self.assertIn("## Tasks", gh_body)
        self.assertIn("- [ ] A", gh_body)
        self.assertIn("- [ ] B", gh_body)

    def test_missing_project_raises(self) -> None:
        with self.assertRaises(LookupError):
            builders.build_issue_body(
                self.store,
                project_id="nope",
                include_canvas_link=True,
                include_source_feedback=True,
                apply_priority_label=True,
                priority_label="P1",
                canvas_url="x",
            )


# =====================================================================
# POST /export/linear
# =====================================================================


class _LinearMock:
    """Stateful mock of Linear's GraphQL endpoint.

    Tracks issueCreate mutations so tests can assert on parent vs.
    sub-issue payloads.
    """

    def __init__(
        self,
        *,
        labels: list[dict] | None = None,
        fail_status: int | None = None,
    ) -> None:
        self.labels = labels or []
        self.fail_status = fail_status
        self.issue_creates: list[dict] = []
        self._counter = 0

    def transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            if self.fail_status is not None:
                return httpx.Response(self.fail_status)
            payload = json.loads(request.content.decode("utf-8"))
            query = payload.get("query", "")
            if "TeamLabels" in query:
                return httpx.Response(
                    200,
                    json={
                        "data": {
                            "team": {
                                "labels": {"nodes": self.labels}
                            }
                        }
                    },
                )
            if "IssueCreate" in query:
                self._counter += 1
                input_ = payload.get("variables", {}).get("input", {})
                self.issue_creates.append(input_)
                identifier = f"ACM-{249 + self._counter}"
                return httpx.Response(
                    200,
                    json={
                        "data": {
                            "issueCreate": {
                                "success": True,
                                "issue": {
                                    "id": f"issue-{self._counter}",
                                    "identifier": identifier,
                                    "url": (
                                        "https://linear.app/acme/issue/"
                                        f"{identifier}"
                                    ),
                                    "title": input_.get("title"),
                                },
                            }
                        }
                    },
                )
            return httpx.Response(200, json={"data": {}})

        return httpx.MockTransport(handler)


class ExportToLinearTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client, self.store, _, self.temp_dir = make_test_app()
        self.workspace_id, self.project_id, _ = _setup_workspace_and_project(
            self
        )

    def tearDown(self) -> None:
        del self.temp_dir

    def _seed(self) -> None:
        _seed_credential(
            self.store, workspace_id=self.workspace_id, provider="linear"
        )
        _set_destination(
            self.store,
            workspace_id=self.workspace_id,
            provider="linear",
            metadata={
                "default_team_id": "team-uuid",
                "default_team_name": "Engineering",
            },
        )

    def test_happy_path(self) -> None:
        self._seed()
        mock = _LinearMock(
            labels=[{"id": "label-p1", "name": "P1", "color": "#c89d3b"}]
        )
        with _patch_async_client(mock.transport()):
            resp = self.client.post(
                f"/api/v2/projects/{self.project_id}/export/linear",
                json={},  # all defaults: P1, all toggles on
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["sub_issue_count"], 3)
        self.assertTrue(body["identifier"].startswith("ACM-"))
        # 1 parent + 3 sub-issues
        self.assertEqual(len(mock.issue_creates), 4)
        # Parent has labelIds resolved.
        self.assertEqual(mock.issue_creates[0].get("labelIds"), ["label-p1"])
        # Sub-issues carry parentId.
        for sub in mock.issue_creates[1:]:
            self.assertEqual(sub.get("parentId"), "issue-1")

    def test_priority_off_skips_label_resolution(self) -> None:
        self._seed()
        mock = _LinearMock(labels=[])
        with _patch_async_client(mock.transport()):
            resp = self.client.post(
                f"/api/v2/projects/{self.project_id}/export/linear",
                json={"apply_priority_label": False},
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        # No labelIds on the parent
        self.assertNotIn("labelIds", mock.issue_creates[0])

    def test_missing_credential(self) -> None:
        # No _seed() — connector not configured.
        resp = self.client.post(
            f"/api/v2/projects/{self.project_id}/export/linear",
            json={},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(
            resp.json()["detail"]["code"], "connector_not_configured"
        )

    def test_missing_destination(self) -> None:
        _seed_credential(
            self.store, workspace_id=self.workspace_id, provider="linear"
        )
        # Note: no _set_destination
        resp = self.client.post(
            f"/api/v2/projects/{self.project_id}/export/linear",
            json={},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(
            resp.json()["detail"]["code"], "destination_not_configured"
        )

    def test_upstream_unauthorized(self) -> None:
        self._seed()
        mock = _LinearMock(fail_status=401)
        with _patch_async_client(mock.transport()):
            resp = self.client.post(
                f"/api/v2/projects/{self.project_id}/export/linear",
                json={},
            )
        self.assertEqual(resp.status_code, 502)
        self.assertEqual(
            resp.json()["detail"]["code"], "upstream_unauthorized"
        )

    def test_cross_user_project(self) -> None:
        self._seed()
        # Create project owned by a different user.
        other_pid = "proj-other"
        self.store.ensure_project(
            project_id=other_pid, user_id="user-someone-else", title="other"
        )
        resp = self.client.post(
            f"/api/v2/projects/{other_pid}/export/linear",
            json={},
        )
        self.assertEqual(resp.status_code, 404)


# =====================================================================
# POST /export/github
# =====================================================================


class _GitHubMock:
    """Stateful mock of GitHub REST endpoints used by export."""

    def __init__(
        self,
        *,
        label_exists: bool = True,
        unauthorized: bool = False,
        repo_missing: bool = False,
    ) -> None:
        self.label_exists = label_exists
        self.unauthorized = unauthorized
        self.repo_missing = repo_missing
        self.requests: list[tuple[str, str, dict | None]] = []
        self.created_label_payload: dict | None = None
        self.created_issue_payload: dict | None = None

    def transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            method = request.method
            body = (
                json.loads(request.content.decode("utf-8"))
                if request.content
                else None
            )
            self.requests.append((method, url, body))
            # Installation access token endpoint
            if "/access_tokens" in url:
                return httpx.Response(
                    201,
                    json={
                        "token": "ghs_fake_installation_token",
                        "expires_at": "2099-01-01T00:00:00Z",
                    },
                )
            if self.unauthorized:
                return httpx.Response(401, json={"message": "Bad credentials"})
            if self.repo_missing:
                return httpx.Response(404, json={"message": "Not Found"})
            if "/labels/" in url and method == "GET":
                if self.label_exists:
                    return httpx.Response(
                        200, json={"name": "P1", "color": "c89d3b"}
                    )
                return httpx.Response(404, json={"message": "Not Found"})
            if "/labels" in url and method == "POST":
                self.created_label_payload = body
                return httpx.Response(
                    201,
                    json={"name": body.get("name"), "color": body.get("color")},
                )
            if "/issues" in url and method == "POST":
                self.created_issue_payload = body
                return httpx.Response(
                    201,
                    json={
                        "id": 99,
                        "number": 1247,
                        "html_url": (
                            "https://github.com/acme-corp/acme-platform/"
                            "issues/1247"
                        ),
                    },
                )
            return httpx.Response(404, json={"message": "Unmatched"})

        return httpx.MockTransport(handler)


def _patch_github_app_config(present: bool = True):
    """Patch load_app_config_from_env in github_send to return a fake
    config tuple (or None when ``present=False``)."""
    if present:
        from planning_studio_service.connectors.github.app_jwt import (
            GitHubAppConfig,
        )
        from planning_studio_service.connectors.github.oauth import (
            GitHubOAuthConfig,
        )

        # PEM is unused in the test path because installation_access_token
        # is itself patched, but we construct a non-empty config so the
        # ``configs is None`` branch is unambiguously not taken.
        cfg = (
            GitHubAppConfig(
                app_id="1",
                app_slug="inspira-test",
                private_key_pem="-----BEGIN-----\nx\n-----END-----",
            ),
            GitHubOAuthConfig(
                client_id="cid",
                client_secret="csecret",
                session_secret="sessionsecret-32-bytes-min-len-padding",
            ),
        )
        return patch(
            (
                "planning_studio_service.exports.github_send."
                "load_app_config_from_env"
            ),
            return_value=cfg,
        )
    return patch(
        (
            "planning_studio_service.exports.github_send."
            "load_app_config_from_env"
        ),
        return_value=None,
    )


def _patch_github_token():
    """Bypass real JWT minting + token exchange — return a synthetic
    ``(token, expires_at)`` tuple."""

    async def _fake_token(*_, **__):
        return ("ghs_fake_installation_token", datetime(2099, 1, 1, tzinfo=timezone.utc))

    return patch(
        (
            "planning_studio_service.exports.github_send."
            "installation_access_token"
        ),
        side_effect=_fake_token,
    )


class ExportToGitHubTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client, self.store, _, self.temp_dir = make_test_app()
        self.workspace_id, self.project_id, _ = _setup_workspace_and_project(
            self
        )

    def tearDown(self) -> None:
        del self.temp_dir

    def _seed(self) -> None:
        _seed_credential(
            self.store,
            workspace_id=self.workspace_id,
            provider="github",
            installation_id="inst-42",
        )
        _set_destination(
            self.store,
            workspace_id=self.workspace_id,
            provider="github",
            metadata={
                "default_owner": "acme-corp",
                "default_repo": "acme-platform",
            },
        )

    def test_happy_path(self) -> None:
        self._seed()
        mock = _GitHubMock(label_exists=True)
        with _patch_github_app_config(), _patch_github_token(), \
                _patch_async_client(mock.transport()):
            resp = self.client.post(
                f"/api/v2/projects/{self.project_id}/export/github",
                json={},
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["issue_number"], 1247)
        # Body has tasks-as-checkboxes
        issue_body = (mock.created_issue_payload or {}).get("body", "")
        self.assertIn("- [ ] Reproduce the bug", issue_body)
        self.assertEqual(
            (mock.created_issue_payload or {}).get("labels"), ["P1"]
        )

    def test_label_create_if_missing(self) -> None:
        self._seed()
        mock = _GitHubMock(label_exists=False)
        with _patch_github_app_config(), _patch_github_token(), \
                _patch_async_client(mock.transport()):
            resp = self.client.post(
                f"/api/v2/projects/{self.project_id}/export/github",
                json={"priority_label": "P0"},
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        # POST /labels was hit with the rust color.
        self.assertIsNotNone(mock.created_label_payload)
        self.assertEqual(
            (mock.created_label_payload or {}).get("name"), "P0"
        )
        self.assertEqual(
            (mock.created_label_payload or {}).get("color"), "b3471d"
        )

    def test_missing_credential(self) -> None:
        resp = self.client.post(
            f"/api/v2/projects/{self.project_id}/export/github",
            json={},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(
            resp.json()["detail"]["code"], "connector_not_configured"
        )

    def test_missing_destination(self) -> None:
        _seed_credential(
            self.store,
            workspace_id=self.workspace_id,
            provider="github",
            installation_id="inst-42",
        )
        resp = self.client.post(
            f"/api/v2/projects/{self.project_id}/export/github",
            json={},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(
            resp.json()["detail"]["code"], "destination_not_configured"
        )

    def test_app_not_configured(self) -> None:
        self._seed()
        with _patch_github_app_config(present=False):
            resp = self.client.post(
                f"/api/v2/projects/{self.project_id}/export/github",
                json={},
            )
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(
            resp.json()["detail"]["code"], "github_app_not_configured"
        )

    def test_upstream_unauthorized(self) -> None:
        self._seed()
        mock = _GitHubMock(unauthorized=True)
        with _patch_github_app_config(), _patch_github_token(), \
                _patch_async_client(mock.transport()):
            resp = self.client.post(
                f"/api/v2/projects/{self.project_id}/export/github",
                json={},
            )
        self.assertEqual(resp.status_code, 502)
        self.assertEqual(
            resp.json()["detail"]["code"], "upstream_unauthorized"
        )

    def test_repo_missing_surfaces_destination_error(self) -> None:
        self._seed()
        mock = _GitHubMock(repo_missing=True)
        with _patch_github_app_config(), _patch_github_token(), \
                _patch_async_client(mock.transport()):
            resp = self.client.post(
                f"/api/v2/projects/{self.project_id}/export/github",
                json={},
            )
        # 404 from GitHub on the configured repo translates into
        # destination_not_configured so the modal can ask the partner
        # to fix the repo path.
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(
            resp.json()["detail"]["code"], "destination_not_configured"
        )


if __name__ == "__main__":
    unittest.main()
