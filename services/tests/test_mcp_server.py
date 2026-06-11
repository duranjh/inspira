"""End-to-end tests for the MCP + OpenAPI Actions surface.

Layout:

- ``McpRoutesTests`` exercises the ``/api/v2/mcp/*`` HTTP surface (the
  OpenAI Actions path) via FastAPI's TestClient. That path shares the
  same handlers as the MCP server, so covering it here proves the
  end-to-end round trip from bearer PAT → handler → store → response
  for each of the 11 tools.
- ``McpAuthTests`` focuses on the auth edge cases: missing token,
  malformed prefix, unknown hash, revoked token.
- ``McpIdorTests`` signs two users up and proves that user B can never
  reach anything user A created — even when B knows the IDs.
- ``MarkdownExportTests`` verifies the ported ``project_to_markdown``
  against known fixtures without going through HTTP.

Each test creates an isolated store, seeds a ``user_access_tokens``
table by hand (the concurrent agent owns the canonical schema; we
create a compatible-shaped table locally so our tests run in isolation),
and mints PATs as needed. The handler code NEVER looks at any column
outside ``{token_hash, user_id, revoked_at}``, so this local seeding
stays forward-compatible.
"""
from __future__ import annotations

import hashlib
import unittest
from typing import Any

from fastapi.testclient import TestClient

try:
    from ._helpers import make_test_app, signup_and_login
except ImportError:
    from _helpers import make_test_app, signup_and_login  # type: ignore[no-redef]


def _ensure_pat_table(store: Any) -> None:
    """Create ``user_access_tokens`` locally if the concurrent agent's
    migration hasn't landed. Idempotent.

    Mirrors the shape documented in the brief:
    ``token_id, token_hash, user_id, name, created_at, last_used_at,
    revoked_at``. Only three columns are read by ``inspira_mcp.auth``:
    ``token_hash``, ``user_id``, ``revoked_at``. We add ``last_used_at``
    so the best-effort bump in ``resolve_bearer_token`` can write
    without erroring, and the other two are padding that matches the
    production schema.
    """
    with store._connect() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS user_access_tokens (
                token_id TEXT PRIMARY KEY,
                token_hash TEXT UNIQUE NOT NULL,
                user_id TEXT NOT NULL,
                name TEXT,
                created_at TEXT,
                last_used_at TEXT,
                revoked_at TEXT
            )
            """
        )
        connection.commit()


def _mint_pat(store: Any, user_id: str, *, revoked: bool = False, raw: str | None = None) -> str:
    """Insert a PAT row for ``user_id`` and return the raw token.

    The raw token matches the documented prefix (``inspira_pat_``) so
    the auth module's prefix guard accepts it. Tests that exercise the
    prefix-rejection path pass a custom ``raw`` that violates the format.
    """
    import secrets

    if raw is None:
        raw = "inspira_pat_" + secrets.token_hex(16)
    token_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    with store._connect() as connection:
        connection.execute(
            """
            INSERT INTO user_access_tokens
                (token_id, token_hash, user_id, name, created_at, last_used_at, revoked_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"pat-{secrets.token_hex(6)}",
                token_hash,
                user_id,
                "test PAT",
                "2026-04-22T00:00:00+00:00",
                None,
                "2026-04-22T00:00:00+00:00" if revoked else None,
            ),
        )
        connection.commit()
    return raw


class _BaseMcpTest(unittest.TestCase):
    """Common setUp/tearDown: one test app + one authenticated user + a PAT."""

    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        _ensure_pat_table(self.store)
        self.signup = signup_and_login(
            self.client, email="a@inspira.app", password="pw-abc-1234",
        )
        self.user_id = self.signup["user_id"]
        self.pat = _mint_pat(self.store, self.user_id)
        # Wipe the session cookie: API clients authenticate via Bearer
        # only. The signup above accidentally set a cookie; drop it so
        # every test request exercises the PAT path exclusively.
        self.client.cookies.clear()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _headers(self, pat: str | None = None) -> dict[str, str]:
        token = pat if pat is not None else self.pat
        return {"Authorization": f"Bearer {token}"}

    def _post(self, tool: str, body: dict[str, Any], *, pat: str | None = None):
        return self.client.post(
            f"/api/v2/mcp/{tool}",
            headers=self._headers(pat),
            json=body,
        )


class McpRoutesTests(_BaseMcpTest):
    """Happy-path round trip for every tool in TOOL_SPEC."""

    def test_create_canvas_returns_project_id_and_title(self) -> None:
        response = self._post("create_canvas", {"idea": "plan a summer workshop"})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["project_id"].startswith("project-"))
        self.assertEqual(payload["title"], "plan a summer workshop")
        self.assertEqual(payload["initial_topic_ids"], [])

    def test_list_projects_surfaces_created_canvas(self) -> None:
        create = self._post("create_canvas", {"idea": "a book club", "title": "Book Club"})
        project_id = create.json()["project_id"]

        response = self._post("list_projects", {})
        self.assertEqual(response.status_code, 200)
        projects = response.json()["projects"]
        matching = [p for p in projects if p["project_id"] == project_id]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]["title"], "Book Club")
        self.assertEqual(matching[0]["topic_count"], 0)

    def test_add_topic_and_list_topics(self) -> None:
        project_id = self._post("create_canvas", {"idea": "home kitchen remodel"}).json()[
            "project_id"
        ]

        add = self._post(
            "add_topic",
            {"project_id": project_id, "title": "Budget", "icon": "chart", "why": "cap"},
        )
        self.assertEqual(add.status_code, 200)
        topic = add.json()["topic"]
        self.assertEqual(topic["title"], "Budget")
        self.assertEqual(topic["icon"], "chart")
        self.assertEqual(topic["metadata"].get("why_this_topic"), "cap")

        listing = self._post("list_topics", {"project_id": project_id})
        self.assertEqual(listing.status_code, 200)
        ids = [t["topic_id"] for t in listing.json()["topics"]]
        self.assertIn(topic["topic_id"], ids)

    def test_update_topic_and_delete_topic(self) -> None:
        project_id = self._post("create_canvas", {"idea": "launch plan"}).json()[
            "project_id"
        ]
        topic_id = self._post(
            "add_topic", {"project_id": project_id, "title": "Old name"}
        ).json()["topic"]["topic_id"]

        renamed = self._post(
            "update_topic", {"topic_id": topic_id, "title": "New name"},
        )
        self.assertEqual(renamed.status_code, 200)
        self.assertEqual(renamed.json()["topic"]["title"], "New name")

        deleted = self._post("delete_topic", {"topic_id": topic_id})
        self.assertEqual(deleted.status_code, 200)
        self.assertTrue(deleted.json()["deleted"])

        listing = self._post("list_topics", {"project_id": project_id}).json()
        ids = [t["topic_id"] for t in listing["topics"]]
        self.assertNotIn(topic_id, ids)

    def test_add_relationship_between_topics(self) -> None:
        project_id = self._post("create_canvas", {"idea": "graph test"}).json()[
            "project_id"
        ]
        left = self._post(
            "add_topic", {"project_id": project_id, "title": "Left"}
        ).json()["topic"]["topic_id"]
        right = self._post(
            "add_topic", {"project_id": project_id, "title": "Right"}
        ).json()["topic"]["topic_id"]

        response = self._post(
            "add_relationship",
            {
                "project_id": project_id,
                "from_topic_id": left,
                "to_topic_id": right,
                "label": "depends on",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["relationship_id"].startswith("rel-"))

    def test_self_relationship_is_rejected(self) -> None:
        project_id = self._post("create_canvas", {"idea": "loop"}).json()["project_id"]
        topic_id = self._post(
            "add_topic", {"project_id": project_id, "title": "Only"}
        ).json()["topic"]["topic_id"]
        response = self._post(
            "add_relationship",
            {
                "project_id": project_id,
                "from_topic_id": topic_id,
                "to_topic_id": topic_id,
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            (response.json().get("detail") or {}).get("error"), "self_relationship",
        )

    def test_record_answer_persists_planner_and_user_turns(self) -> None:
        project_id = self._post("create_canvas", {"idea": "qna test"}).json()[
            "project_id"
        ]
        topic_id = self._post(
            "add_topic", {"project_id": project_id, "title": "Goals"}
        ).json()["topic"]["topic_id"]

        response = self._post(
            "record_answer",
            {
                "topic_id": topic_id,
                "question": "What does success look like?",
                "answer": "Three users per week talking to each other.",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["turn_id"].startswith("turn-"))

        turns = self.store.list_qna_turns(topic_id=topic_id, user_id=self.user_id)
        self.assertEqual(len(turns), 2)
        roles = [t["role"] for t in turns]
        self.assertEqual(roles, ["planner", "user"])
        bodies = [t["body"] for t in turns]
        self.assertIn("What does success look like?", bodies)
        self.assertIn("Three users per week talking to each other.", bodies)

        # Topic status should flip to in_progress.
        updated = self.store.get_topic(topic_id, user_id=self.user_id)
        assert updated is not None
        self.assertEqual(updated["status"], "in_progress")

    def test_add_decision_stores_statement_and_rationale(self) -> None:
        project_id = self._post("create_canvas", {"idea": "dec"}).json()["project_id"]
        topic_id = self._post(
            "add_topic", {"project_id": project_id, "title": "Venue"}
        ).json()["topic"]["topic_id"]

        response = self._post(
            "add_decision",
            {
                "topic_id": topic_id,
                "statement": "Use the community hall on Thursday.",
                "rationale": "It's free and has parking.",
            },
        )
        self.assertEqual(response.status_code, 200)
        decision_id = response.json()["decision_id"]
        self.assertTrue(decision_id.startswith("dec-"))

        decisions = self.store.list_decisions(project_id=project_id, user_id=self.user_id)
        matching = [d for d in decisions if d["decision_id"] == decision_id]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]["statement"], "Use the community hall on Thursday.")
        self.assertEqual(matching[0]["rationale"], "It's free and has parking.")

    def test_get_summary_returns_counts_and_latest_timestamp(self) -> None:
        project_id = self._post("create_canvas", {"idea": "stats"}).json()["project_id"]
        topic_id = self._post(
            "add_topic", {"project_id": project_id, "title": "One"}
        ).json()["topic"]["topic_id"]
        self._post(
            "add_topic", {"project_id": project_id, "title": "Two"},
        )
        self._post(
            "add_decision",
            {"topic_id": topic_id, "statement": "Ship it."},
        )

        response = self._post("get_summary", {"project_id": project_id})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["topic_count"], 2)
        self.assertEqual(body["decision_count"], 1)
        self.assertIsNotNone(body["last_updated"])

    def test_export_markdown_renders_structured_document(self) -> None:
        project_id = self._post(
            "create_canvas", {"idea": "weekend workshop", "title": "Workshop Plan"}
        ).json()["project_id"]
        topic_a = self._post(
            "add_topic",
            {"project_id": project_id, "title": "Venue", "icon": "map-pin"},
        ).json()["topic"]["topic_id"]
        topic_b = self._post(
            "add_topic",
            {"project_id": project_id, "title": "Budget", "icon": "chart"},
        ).json()["topic"]["topic_id"]
        self._post(
            "add_decision",
            {"topic_id": topic_a, "statement": "Community hall.", "rationale": "Free"},
        )
        self._post(
            "record_answer",
            {
                "topic_id": topic_b,
                "question": "Spending cap?",
                "answer": "Two hundred.",
            },
        )
        self._post(
            "add_relationship",
            {
                "project_id": project_id,
                "from_topic_id": topic_b,
                "to_topic_id": topic_a,
                "label": "bounds",
            },
        )

        response = self._post("export_markdown", {"project_id": project_id})
        self.assertEqual(response.status_code, 200)
        markdown = response.json()["markdown"]
        self.assertIn("# Workshop Plan", markdown)
        self.assertIn("## Venue", markdown)
        self.assertIn("## Budget", markdown)
        self.assertIn("- Community hall.", markdown)
        self.assertIn("**Planner:** Spending cap?", markdown)
        self.assertIn("**You:** Two hundred.", markdown)
        self.assertIn("## Relationships", markdown)
        self.assertIn("**Budget**", markdown)


class McpAuthTests(_BaseMcpTest):
    """Edge cases around bearer-PAT resolution."""

    def test_missing_authorization_header_returns_401(self) -> None:
        response = self.client.post(
            "/api/v2/mcp/list_projects", json={},
        )
        self.assertEqual(response.status_code, 401)

    def test_malformed_prefix_returns_401(self) -> None:
        response = self.client.post(
            "/api/v2/mcp/list_projects",
            headers={"Authorization": "Bearer not-a-real-pat"},
            json={},
        )
        self.assertEqual(response.status_code, 401)

    def test_unknown_token_returns_401(self) -> None:
        # Valid prefix but never written to the table.
        response = self.client.post(
            "/api/v2/mcp/list_projects",
            headers={
                "Authorization": "Bearer inspira_pat_0000000000000000000000000000000000000000"
            },
            json={},
        )
        self.assertEqual(response.status_code, 401)

    def test_revoked_token_returns_401(self) -> None:
        revoked = _mint_pat(self.store, self.user_id, revoked=True)
        response = self._post("list_projects", {}, pat=revoked)
        self.assertEqual(response.status_code, 401)

    def test_unknown_tool_returns_404(self) -> None:
        # FastAPI returns 404 for an unregistered route — sanity-check that
        # the bearer auth doesn't accidentally intercept before routing.
        response = self.client.post(
            "/api/v2/mcp/nonexistent_tool",
            headers=self._headers(),
            json={},
        )
        self.assertEqual(response.status_code, 404)


class McpIdorTests(_BaseMcpTest):
    """User B must never reach user A's data via the MCP routes."""

    def setUp(self) -> None:
        super().setUp()
        # Second client against the same app; its own cookie jar so signup
        # on B does NOT authenticate A.
        self.client_b = TestClient(self.client.app)
        signup_b = signup_and_login(
            self.client_b, email="b@inspira.app", password="pw-xyz-7890",
        )
        self.user_id_b = signup_b["user_id"]
        self.pat_b = _mint_pat(self.store, self.user_id_b)
        self.client_b.cookies.clear()

        # Seed an A-owned project + topic + decision that we'll probe.
        self.project_id = self._post(
            "create_canvas", {"idea": "a's private canvas"}
        ).json()["project_id"]
        self.topic_id = self._post(
            "add_topic", {"project_id": self.project_id, "title": "Private Topic"},
        ).json()["topic"]["topic_id"]

    def _post_b(self, tool: str, body: dict[str, Any]):
        return self.client_b.post(
            f"/api/v2/mcp/{tool}",
            headers={"Authorization": f"Bearer {self.pat_b}"},
            json=body,
        )

    def test_user_b_cannot_list_topics_on_a_project(self) -> None:
        response = self._post_b("list_topics", {"project_id": self.project_id})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            (response.json().get("detail") or {}).get("error"), "project_not_found",
        )

    def test_user_b_cannot_add_topic_to_a_project(self) -> None:
        response = self._post_b(
            "add_topic",
            {"project_id": self.project_id, "title": "pwnd"},
        )
        self.assertEqual(response.status_code, 404)
        # Sanity: no rogue topic landed on A's project.
        topics = self.store.list_topics(
            project_id=self.project_id, user_id=self.user_id,
        )
        titles = [t["title"] for t in topics]
        self.assertNotIn("pwnd", titles)

    def test_user_b_cannot_update_a_topic(self) -> None:
        response = self._post_b(
            "update_topic", {"topic_id": self.topic_id, "title": "pwnd"},
        )
        self.assertEqual(response.status_code, 404)
        fresh = self.store.get_topic(self.topic_id, user_id=self.user_id)
        assert fresh is not None
        self.assertNotEqual(fresh["title"], "pwnd")

    def test_user_b_cannot_record_answer_on_a_topic(self) -> None:
        response = self._post_b(
            "record_answer",
            {
                "topic_id": self.topic_id,
                "question": "snoop?",
                "answer": "yes please",
            },
        )
        self.assertEqual(response.status_code, 404)

    def test_user_b_cannot_export_a_project(self) -> None:
        response = self._post_b(
            "export_markdown", {"project_id": self.project_id},
        )
        self.assertEqual(response.status_code, 404)

    def test_user_b_list_projects_sees_only_b(self) -> None:
        # Create a B-owned project so the list isn't empty.
        b_create = self.client_b.post(
            "/api/v2/mcp/create_canvas",
            headers={"Authorization": f"Bearer {self.pat_b}"},
            json={"idea": "b's own work"},
        ).json()
        response = self._post_b("list_projects", {})
        ids = [p["project_id"] for p in response.json()["projects"]]
        self.assertIn(b_create["project_id"], ids)
        self.assertNotIn(self.project_id, ids)


class McpInputValidationTests(_BaseMcpTest):
    """Bad payload shapes surface as 422 (FastAPI's Pydantic default)."""

    def test_missing_required_field_is_422(self) -> None:
        response = self._post("create_canvas", {})
        self.assertEqual(response.status_code, 422)

    def test_extra_field_is_forbidden(self) -> None:
        # model_config extra="forbid" — unknown key = 422.
        response = self._post(
            "create_canvas", {"idea": "fine", "rogue_key": "nope"},
        )
        self.assertEqual(response.status_code, 422)

    def test_empty_title_on_update_rejected(self) -> None:
        project_id = self._post("create_canvas", {"idea": "x"}).json()["project_id"]
        topic_id = self._post(
            "add_topic", {"project_id": project_id, "title": "Start"}
        ).json()["topic"]["topic_id"]
        response = self._post(
            "update_topic", {"topic_id": topic_id, "title": "   "},
        )
        self.assertEqual(response.status_code, 400)


class MarkdownExportTests(unittest.TestCase):
    """Unit tests for the Python port of projectToMarkdown."""

    def test_renders_minimal_project(self) -> None:
        from inspira_mcp.markdown_export import project_to_markdown

        output = project_to_markdown(
            project_title="Demo",
            topics=[
                {
                    "topic_id": "t1",
                    "title": "Intro",
                    "icon": "lightbulb",
                    "order_index": 0,
                    "metadata": {},
                    "created_at": "2026-04-22T00:00:00",
                }
            ],
            relationships=[],
            decisions_by_topic_id={},
            turns_by_topic_id={},
        )
        self.assertIn("# Demo", output)
        self.assertIn("## Intro", output)
        self.assertIn("### Decisions", output)
        self.assertIn("_No decisions captured yet._", output)

    def test_escapes_markdown_in_titles(self) -> None:
        from inspira_mcp.markdown_export import project_to_markdown

        output = project_to_markdown(
            project_title="Demo",
            topics=[
                {
                    "topic_id": "t1",
                    "title": "evil`backtick*asterisk",
                    "icon": "flag",
                    "order_index": 0,
                    "metadata": {},
                    "created_at": "2026-04-22T00:00:00",
                }
            ],
            relationships=[],
            decisions_by_topic_id={},
            turns_by_topic_id={},
        )
        # Backtick and asterisk both escaped.
        self.assertIn("\\`", output)
        self.assertIn("\\*", output)

    def test_relationships_rendered_with_titles(self) -> None:
        from inspira_mcp.markdown_export import project_to_markdown

        output = project_to_markdown(
            project_title="Graph",
            topics=[
                {
                    "topic_id": "a",
                    "title": "Alpha",
                    "icon": "flag",
                    "order_index": 0,
                    "metadata": {},
                    "created_at": "t",
                },
                {
                    "topic_id": "b",
                    "title": "Beta",
                    "icon": "flag",
                    "order_index": 1,
                    "metadata": {},
                    "created_at": "t",
                },
            ],
            relationships=[
                {
                    "relationship_id": "r1",
                    "source_topic_id": "a",
                    "target_topic_id": "b",
                    "label": "requires",
                }
            ],
            decisions_by_topic_id={},
            turns_by_topic_id={},
        )
        self.assertIn("## Relationships", output)
        self.assertIn("**Alpha**", output)
        self.assertIn("**Beta**", output)
        self.assertIn("requires", output)


class McpServerBuilderTests(unittest.TestCase):
    """Smoke: the MCP server registers every tool in TOOL_SPEC."""

    def test_every_tool_registered(self) -> None:
        import asyncio

        from inspira_mcp.schemas import tool_names
        from inspira_mcp.server import build_mcp_server

        # Build a server against an isolated store — no network, no PAT auth.
        client, store, _adapter, temp_dir = make_test_app()
        try:
            server = build_mcp_server(store)
            registered = asyncio.run(server.list_tools())
            registered_names = {t.name for t in registered}
            for expected in tool_names():
                self.assertIn(expected, registered_names)
            self.assertEqual(len(registered_names), 11)
        finally:
            temp_dir.cleanup()
            client.close()


if __name__ == "__main__":
    unittest.main()
