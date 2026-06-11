"""Tests for the code-scaffold feature.

Covers the eight cases called out in the feature spec plus a handful of
adapter-level sanity checks on the sanitize pass.

Tests never touch OpenAI — the scaffold adapter is injected via
``app.state.code_scaffold_adapter`` as a ``MagicMock`` that returns a
pre-shaped manifest.
"""
from __future__ import annotations

import io
import unittest
import zipfile
from unittest.mock import MagicMock

try:
    from ._helpers import (
        fake_kickoff_response,
        make_test_app,
        signup_and_login,
    )
except ImportError:
    from _helpers import (  # type: ignore[no-redef]
        fake_kickoff_response,
        make_test_app,
        signup_and_login,
    )

import os
from types import SimpleNamespace
from typing import Any

from planning_studio_service.agents.code_scaffold import (
    ClaudeCodeScaffoldAdapter,
    ClaudeCodeScaffoldConfig,
    CodeScaffoldAdapter,
    CodeScaffoldConfig,
    _format_scaffold_edit_user_message,
    _is_safe_path,
    sanitize_scaffold_manifest,
)
from planning_studio_service.agents.prompts_scaffold import (
    repo_context_section,
)
from planning_studio_service.agents.schemas_scaffold import (
    MAX_EDIT_EXPLANATION_CHARS,
)


def _fake_scaffold_tool_use_response(
    *,
    tool_name: str,
    args: dict[str, Any],
) -> Any:
    """Build a minimal Anthropic-shaped Message for the scaffold adapter tests.

    Mirrors the helper in test_anthropic_adapter.py — exposes a single
    tool_use block carrying the args, plus stop_reason + usage so
    ``_extract_tool_use_args``'s diagnostic path doesn't crash on a
    happy-path test.
    """
    block = SimpleNamespace(type="tool_use", name=tool_name, input=args)
    return SimpleNamespace(
        content=[block],
        stop_reason="tool_use",
        usage=SimpleNamespace(input_tokens=10, output_tokens=20),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_software_kickoff_response() -> dict:
    """Like fake_kickoff_response() but with a software domain.

    The shared helper returns domain='event'; scaffold tests that need an
    approved project must use a software-adjacent domain so the backend
    domain guard passes.
    """
    response = fake_kickoff_response()
    response["domain"] = "software"
    return response


def _seed_software_project(client, adapter, project_id: str) -> None:
    """Seed a project + topics + a decision + a summary, via the HTTP routes."""
    adapter.kickoff.return_value = _fake_software_kickoff_response()
    response = client.post(
        f"/api/v2/projects/{project_id}/kickoff",
        json={"user_idea": "A note-taking web app with tagged entries."},
    )
    response.raise_for_status()


def _ok_manifest() -> dict:
    """A well-formed scaffold manifest the adapter can return."""
    return {
        "framework": "react-vite",
        "language": "typescript",
        "files": [
            {"path": "README.md", "content": "# Notes\n\nA small note-taking app.\n"},
            {
                "path": "package.json",
                "content": '{"name":"notes","version":"0.1.0"}\n',
            },
            {"path": "src/main.tsx", "content": "console.log('hi')\n"},
            {"path": "tests/main.test.ts", "content": "test('ok', () => {})\n"},
            {"path": ".gitignore", "content": "node_modules\ndist\n"},
        ],
        "readme_preview": "A small note-taking app.",
        "post_install_steps": ["pnpm install", "pnpm dev"],
        "truncation_note": "",
    }


# ---------------------------------------------------------------------------
# Sanitize-pass unit tests
# ---------------------------------------------------------------------------


class PathSafetyTests(unittest.TestCase):
    def test_accepts_project_relative_posix_paths(self) -> None:
        for path in [
            "README.md",
            "src/App.tsx",
            "tests/test_main.py",
            "a/b/c/d.ts",
        ]:
            with self.subTest(path=path):
                self.assertTrue(_is_safe_path(path))

    def test_rejects_dotdot_segments(self) -> None:
        for path in ["../etc/passwd", "src/../../oops.md", "a/b/../c.py"]:
            with self.subTest(path=path):
                self.assertFalse(_is_safe_path(path))

    def test_rejects_absolute_and_windows_paths(self) -> None:
        for path in ["/etc/passwd", "C:/Users/x", "src\\main.ts"]:
            with self.subTest(path=path):
                self.assertFalse(_is_safe_path(path))


class SanitizeManifestTests(unittest.TestCase):
    def test_drops_unsafe_paths(self) -> None:
        parsed = {
            "framework": "react-vite",
            "language": "typescript",
            "files": [
                {"path": "README.md", "content": "hi"},
                {"path": "../escape.sh", "content": "rm -rf /"},
                {"path": "/abs/path.txt", "content": "nope"},
            ],
            "readme_preview": "",
            "post_install_steps": [],
            "truncation_note": "",
        }
        sanitize_scaffold_manifest(parsed)
        paths = [f["path"] for f in parsed["files"]]
        self.assertEqual(paths, ["README.md"])
        self.assertEqual(len(parsed["_sanitize"]["dropped_unsafe_paths"]), 2)

    def test_deduplicates_paths_keeping_first(self) -> None:
        parsed = {
            "framework": "react-vite",
            "language": "typescript",
            "files": [
                {"path": "README.md", "content": "first"},
                {"path": "README.md", "content": "second"},
            ],
            "readme_preview": "",
            "post_install_steps": [],
            "truncation_note": "",
        }
        sanitize_scaffold_manifest(parsed)
        self.assertEqual(len(parsed["files"]), 1)
        # First wins.
        self.assertTrue(parsed["files"][0]["content"].startswith("first"))

    def test_empty_files_raises_runtime_error(self) -> None:
        parsed = {
            "framework": "react-vite",
            "language": "typescript",
            "files": [],
            "readme_preview": "",
            "post_install_steps": [],
            "truncation_note": "",
        }
        with self.assertRaises(RuntimeError):
            sanitize_scaffold_manifest(parsed)

    def test_ensures_trailing_newline(self) -> None:
        parsed = {
            "framework": "react-vite",
            "language": "typescript",
            "files": [
                {"path": "a.txt", "content": "no newline"},
                {"path": "b.txt", "content": "has newline\n"},
                {"path": "c.txt", "content": "many newlines\n\n\n"},
            ],
            "readme_preview": "",
            "post_install_steps": [],
            "truncation_note": "",
        }
        sanitize_scaffold_manifest(parsed)
        for f in parsed["files"]:
            with self.subTest(path=f["path"]):
                self.assertTrue(f["content"].endswith("\n"))
                self.assertFalse(f["content"].endswith("\n\n"))


# ---------------------------------------------------------------------------
# HTTP endpoint tests
# ---------------------------------------------------------------------------


class ScaffoldEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        signup_and_login(self.client, email="scaffold@example.com")
        self.scaffold_adapter = MagicMock()
        self.client.app.state.code_scaffold_adapter = self.scaffold_adapter

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_free_user_gets_upgrade_required_402(self) -> None:
        """A Free-tier user POSTing to /scaffold gets the structured
        upgrade-required 402 (PR 2 replaced the credit-balance gate
        with a plan-tier gate)."""
        _seed_software_project(self.client, self.adapter, "proj-broke")
        response = self.client.post("/api/v2/projects/proj-broke/scaffold")
        self.assertEqual(response.status_code, 402)
        detail = response.json()["detail"]
        self.assertEqual(detail["error"], "upgrade_required")
        self.assertEqual(detail["min_plan"], "pro")
        # Adapter should NOT have been called — plan check is first.
        self.scaffold_adapter.generate.assert_not_called()

    def test_successful_scaffold_returns_manifest(
        self,
    ) -> None:
        # Bump the user to Pro so they have 500 credits (enough for 5 runs).
        from planning_studio_service.billing import NoopBillingProvider

        me = self.client.get("/api/auth/me").json()
        NoopBillingProvider().record_local_subscription(
            user_id=me["user_id"], plan_slug="pro", store=self.store,
        )

        _seed_software_project(self.client, self.adapter, "proj-happy")
        self.scaffold_adapter.generate.return_value = _ok_manifest()

        response = self.client.post("/api/v2/projects/proj-happy/scaffold")
        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()
        self.assertIn("scaffold", body)
        self.assertEqual(body["scaffold"]["framework"], "react-vite")
        self.assertEqual(body["scaffold"]["language"], "typescript")
        self.assertEqual(body["scaffold"]["file_count"], 5)

        # Scaffold row persisted.
        rows = self.store.list_scaffolds_for_project(
            project_id="proj-happy", user_id=me["user_id"],
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["framework"], "react-vite")

    def test_zip_download_returns_valid_zip(self) -> None:
        from planning_studio_service.billing import NoopBillingProvider

        me = self.client.get("/api/auth/me").json()
        NoopBillingProvider().record_local_subscription(
            user_id=me["user_id"], plan_slug="pro", store=self.store,
        )

        _seed_software_project(self.client, self.adapter, "proj-zip")
        self.scaffold_adapter.generate.return_value = _ok_manifest()
        gen = self.client.post("/api/v2/projects/proj-zip/scaffold")
        scaffold_id = gen.json()["scaffold"]["scaffold_id"]

        download = self.client.get(f"/api/v2/scaffolds/{scaffold_id}/download")
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download.headers["content-type"], "application/zip")
        self.assertIn(
            "attachment", download.headers.get("content-disposition", ""),
        )

        # Validate the zip itself: read it back and check the file list.
        buf = io.BytesIO(download.content)
        with zipfile.ZipFile(buf, "r") as zf:
            names = set(zf.namelist())
            self.assertIn("README.md", names)
            self.assertIn("package.json", names)
            self.assertIn("src/main.tsx", names)
            self.assertIn("tests/main.test.ts", names)
            self.assertIn(".gitignore", names)
            # Content of README matches the manifest (trailing newline
            # normalized by sanitize pass).
            readme = zf.read("README.md").decode("utf-8")
            self.assertIn("A small note-taking app", readme)

    def test_cross_user_idor_returns_404(self) -> None:
        """User B cannot download user A's scaffold."""
        from planning_studio_service.billing import NoopBillingProvider

        # User A creates a scaffold.
        me_a = self.client.get("/api/auth/me").json()
        NoopBillingProvider().record_local_subscription(
            user_id=me_a["user_id"], plan_slug="pro", store=self.store,
        )
        _seed_software_project(self.client, self.adapter, "proj-a")
        self.scaffold_adapter.generate.return_value = _ok_manifest()
        gen = self.client.post("/api/v2/projects/proj-a/scaffold")
        scaffold_id = gen.json()["scaffold"]["scaffold_id"]

        # User B signs in (replaces the session cookie on the client).
        self.client.post("/api/auth/logout")
        self.client.cookies.clear()
        signup_and_login(self.client, email="userb@example.com")

        download = self.client.get(f"/api/v2/scaffolds/{scaffold_id}/download")
        self.assertEqual(download.status_code, 404)
        self.assertEqual(
            download.json()["detail"]["error"], "scaffold_not_found",
        )

    def test_regenerate_creates_new_scaffold_row(self) -> None:
        from planning_studio_service.billing import NoopBillingProvider

        me = self.client.get("/api/auth/me").json()
        NoopBillingProvider().record_local_subscription(
            user_id=me["user_id"], plan_slug="pro", store=self.store,
        )
        _seed_software_project(self.client, self.adapter, "proj-regen")
        self.scaffold_adapter.generate.return_value = _ok_manifest()

        first = self.client.post("/api/v2/projects/proj-regen/scaffold")
        second = self.client.post("/api/v2/projects/proj-regen/scaffold")
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)
        self.assertNotEqual(
            first.json()["scaffold"]["scaffold_id"],
            second.json()["scaffold"]["scaffold_id"],
        )

        rows = self.store.list_scaffolds_for_project(
            project_id="proj-regen", user_id=me["user_id"],
        )
        self.assertEqual(len(rows), 2)

    def test_list_scaffolds_endpoint_returns_rows(self) -> None:
        from planning_studio_service.billing import NoopBillingProvider

        me = self.client.get("/api/auth/me").json()
        NoopBillingProvider().record_local_subscription(
            user_id=me["user_id"], plan_slug="pro", store=self.store,
        )
        _seed_software_project(self.client, self.adapter, "proj-list")
        self.scaffold_adapter.generate.return_value = _ok_manifest()
        self.client.post("/api/v2/projects/proj-list/scaffold")

        response = self.client.get("/api/v2/projects/proj-list/scaffolds")
        self.assertEqual(response.status_code, 200)
        rows = response.json()["scaffolds"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["framework"], "react-vite")

    def test_llm_failure_returns_planner_error(self) -> None:
        """LLM failure surfaces the generic planner_call_failed envelope.
        PR 2 removed credit accounting so there's no balance to inspect;
        the contract is just "user sees an error, no scaffold persisted"."""
        from planning_studio_service.billing import NoopBillingProvider

        me = self.client.get("/api/auth/me").json()
        NoopBillingProvider().record_local_subscription(
            user_id=me["user_id"], plan_slug="pro", store=self.store,
        )
        _seed_software_project(self.client, self.adapter, "proj-failure")
        self.scaffold_adapter.generate.side_effect = RuntimeError("openai blew up")

        response = self.client.post("/api/v2/projects/proj-failure/scaffold")
        self.assertEqual(response.status_code, 500)

        # No scaffold row was persisted.
        rows = self.store.list_scaffolds_for_project(
            project_id="proj-failure", user_id=me["user_id"],
        )
        self.assertEqual(len(rows), 0)


class SanitizeManifestEdgeCasesTests(unittest.TestCase):
    """Additional sanitize-pass coverage: truncation and file-count cap."""

    def test_content_exceeding_max_chars_is_truncated_to_cap(self) -> None:
        from planning_studio_service.agents.schemas_scaffold import MAX_FILE_CONTENT_CHARS

        long_content = "x" * (MAX_FILE_CONTENT_CHARS + 500)
        parsed = {
            "framework": "fastapi",
            "language": "python",
            "files": [{"path": "main.py", "content": long_content}],
            "readme_preview": "",
            "post_install_steps": [],
            "truncation_note": "",
        }
        sanitize_scaffold_manifest(parsed)
        content_out = parsed["files"][0]["content"]
        # Must be exactly MAX_FILE_CONTENT_CHARS chars + 1 trailing newline.
        self.assertLessEqual(len(content_out), MAX_FILE_CONTENT_CHARS + 1)
        self.assertTrue(content_out.endswith("\n"))
        # Truncation is noted in the _sanitize dict.
        self.assertIn("main.py", parsed["_sanitize"]["truncated_paths"])

    def test_files_beyond_max_count_are_capped(self) -> None:
        from planning_studio_service.agents.schemas_scaffold import MAX_FILES_PER_SCAFFOLD

        many_files = [
            {"path": f"file{i}.txt", "content": "hi"} for i in range(MAX_FILES_PER_SCAFFOLD + 5)
        ]
        parsed = {
            "framework": "other",
            "language": "python",
            "files": many_files,
            "readme_preview": "",
            "post_install_steps": [],
            "truncation_note": "",
        }
        sanitize_scaffold_manifest(parsed)
        self.assertEqual(len(parsed["files"]), MAX_FILES_PER_SCAFFOLD)
        # Overflow recorded.
        self.assertEqual(
            len(parsed["_sanitize"]["capped_overflow"]), 5,
        )

    def test_non_string_content_is_coerced_to_string(self) -> None:
        parsed = {
            "framework": "other",
            "language": "python",
            "files": [{"path": "data.py", "content": 42}],
            "readme_preview": "",
            "post_install_steps": [],
            "truncation_note": "",
        }
        sanitize_scaffold_manifest(parsed)
        self.assertIsInstance(parsed["files"][0]["content"], str)

    def test_non_string_post_install_steps_are_filtered(self) -> None:
        parsed = {
            "framework": "other",
            "language": "python",
            "files": [{"path": "a.py", "content": "hi"}],
            "readme_preview": "",
            "post_install_steps": ["pip install", 42, None, "python main.py"],
            "truncation_note": "",
        }
        sanitize_scaffold_manifest(parsed)
        # Non-string entries dropped.
        self.assertEqual(parsed["post_install_steps"], ["pip install", "python main.py"])


class ScaffoldNonSoftwareDomainTest(unittest.TestCase):
    """Scaffold endpoint behavior for non-software projects."""

    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        signup_and_login(self.client, email="domain-check@example.com")
        self.scaffold_adapter = MagicMock()
        self.client.app.state.code_scaffold_adapter = self.scaffold_adapter

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _seed_event_project(self, project_id: str) -> None:
        """Kickoff with an event-domain response (domain='event')."""
        self.adapter.kickoff.return_value = fake_kickoff_response()  # domain=event
        resp = self.client.post(
            f"/api/v2/projects/{project_id}/kickoff",
            json={"user_idea": "A corporate team retreat for 200 people."},
        )
        resp.raise_for_status()

    def test_non_software_domain_no_longer_blocked(self) -> None:
        """Founder reframe 2026-05-04: code-gen runs for any owned
        project — autonomous-pipeline projects carry no
        metadata.domain and the legacy software-domain 422 gate
        would block every one of them. The gate is gone; this test
        pins that contract."""
        from planning_studio_service.billing import NoopBillingProvider

        me = self.client.get("/api/auth/me").json()
        NoopBillingProvider().record_local_subscription(
            user_id=me["user_id"], plan_slug="pro", store=self.store,
        )
        self._seed_event_project("proj-event")
        self.scaffold_adapter.generate.return_value = _ok_manifest()

        response = self.client.post("/api/v2/projects/proj-event/scaffold")
        # Domain gate is gone — non-software projects no longer 422.
        self.assertNotEqual(response.status_code, 422, response.text)
        # Adapter IS called now (was previously short-circuited by 422).
        self.scaffold_adapter.generate.assert_called()


# ---------------------------------------------------------------------------
# Edit-mode sanitize + validation tests
# ---------------------------------------------------------------------------


class SanitizeEditShapeTests(unittest.TestCase):
    """Sanitize preserves the ``explanation`` field on edit responses."""

    def _edit_manifest(self, explanation: str) -> dict:
        return {
            "framework": "react-vite",
            "language": "typescript",
            "files": [
                {"path": "README.md", "content": "# Notes\n"},
                {"path": "src/main.tsx", "content": "console.log('hi')\n"},
            ],
            "readme_preview": "",
            "post_install_steps": [],
            "truncation_note": "",
            "explanation": explanation,
        }

    def test_preserves_explanation_when_present(self) -> None:
        parsed = self._edit_manifest("I added the 100ms debounce on the storage event.")
        sanitize_scaffold_manifest(parsed)
        self.assertEqual(
            parsed["explanation"],
            "I added the 100ms debounce on the storage event.",
        )

    def test_clamps_oversized_explanation(self) -> None:
        # Schema cap is enforced model-side via maxLength; sanitize is the
        # belt-and-braces guard for a stale schema deploy.
        oversized = "x" * (MAX_EDIT_EXPLANATION_CHARS + 200)
        parsed = self._edit_manifest(oversized)
        sanitize_scaffold_manifest(parsed)
        self.assertEqual(len(parsed["explanation"]), MAX_EDIT_EXPLANATION_CHARS)

    def test_normalizes_non_string_explanation_to_empty(self) -> None:
        parsed = self._edit_manifest("ok")
        # The schema enforces string, but be defensive against a bad call.
        parsed["explanation"] = {"unexpected": "shape"}
        sanitize_scaffold_manifest(parsed)
        self.assertEqual(parsed["explanation"], "")

    def test_generate_shape_does_not_grow_explanation_field(self) -> None:
        # If the model returns the generate-shape (no explanation key),
        # sanitize must NOT inject one — otherwise the schema validator
        # downstream would fail an additionalProperties=False check on
        # SCAFFOLD_SCHEMA.
        parsed = {
            "framework": "react-vite",
            "language": "typescript",
            "files": [{"path": "README.md", "content": "# Hi\n"}],
            "readme_preview": "",
            "post_install_steps": [],
            "truncation_note": "",
        }
        sanitize_scaffold_manifest(parsed)
        self.assertNotIn("explanation", parsed)


class EditUserMessageFormatterTests(unittest.TestCase):
    """``_format_scaffold_edit_user_message`` renders project + scaffold + request."""

    def test_includes_project_title_and_user_request(self) -> None:
        msg = _format_scaffold_edit_user_message(
            project_title="Notes",
            current_files=[
                {"path": "README.md", "content": "# Notes\n"},
                {"path": "src/main.tsx", "content": "console.log('hi')\n"},
            ],
            user_message="Add a debounce of 100ms to the storage event listener.",
        )
        self.assertIn("PROJECT: Notes", msg)
        self.assertIn("CURRENT SCAFFOLD:", msg)
        self.assertIn("--- README.md ---", msg)
        self.assertIn("--- src/main.tsx ---", msg)
        self.assertIn(
            "Add a debounce of 100ms to the storage event listener.", msg,
        )
        self.assertIn("USER REQUEST:", msg)

    def test_handles_empty_current_files_gracefully(self) -> None:
        msg = _format_scaffold_edit_user_message(
            project_title="Empty",
            current_files=[],
            user_message="Start over.",
        )
        self.assertIn("(empty — no scaffold has been generated yet)", msg)


class CodeScaffoldEditValidationTests(unittest.TestCase):
    """``CodeScaffoldAdapter.edit()`` rejects empty inputs before any LLM call."""

    def setUp(self) -> None:
        # MagicMock client means the OpenAI SDK is never called — but
        # validation happens before that anyway, so the mock is never
        # exercised in these tests.
        self.adapter = CodeScaffoldAdapter(
            config=CodeScaffoldConfig(api_key="test"),
            client=MagicMock(),
        )

    def test_empty_project_title_raises(self) -> None:
        with self.assertRaises(ValueError):
            self.adapter.edit(
                project_title="",
                current_files=[{"path": "x", "content": "y"}],
                user_message="add a thing",
            )

    def test_whitespace_project_title_raises(self) -> None:
        with self.assertRaises(ValueError):
            self.adapter.edit(
                project_title="   ",
                current_files=[{"path": "x", "content": "y"}],
                user_message="add a thing",
            )

    def test_empty_user_message_raises(self) -> None:
        with self.assertRaises(ValueError):
            self.adapter.edit(
                project_title="My project",
                current_files=[{"path": "x", "content": "y"}],
                user_message="",
            )

    def test_whitespace_user_message_raises(self) -> None:
        with self.assertRaises(ValueError):
            self.adapter.edit(
                project_title="My project",
                current_files=[{"path": "x", "content": "y"}],
                user_message="   ",
            )


# ---------------------------------------------------------------------------
# Claude code-scaffold adapter tests
# ---------------------------------------------------------------------------


class ClaudeCodeScaffoldConstructionTests(unittest.TestCase):
    """Construction surfaces a clear error when ANTHROPIC_API_KEY is missing."""

    def test_missing_anthropic_key_raises_at_construction(self) -> None:
        # Wipe env so the constructor can't fall through to a real key.
        original = os.environ.pop("ANTHROPIC_API_KEY", None)
        try:
            with self.assertRaises(RuntimeError) as ctx:
                ClaudeCodeScaffoldAdapter()
            self.assertIn("ANTHROPIC_API_KEY", str(ctx.exception))
        finally:
            if original is not None:
                os.environ["ANTHROPIC_API_KEY"] = original

    def test_explicit_config_key_bypasses_env(self) -> None:
        adapter = ClaudeCodeScaffoldAdapter(
            config=ClaudeCodeScaffoldConfig(api_key="sk-ant-test"),
            client=MagicMock(),
        )
        # Default model resolves from env or DEFAULT_CLAUDE_MODEL.
        self.assertIsNotNone(adapter.config.model)
        self.assertTrue(adapter.config.model.startswith("claude-"))


class ClaudeCodeScaffoldGenerateTests(unittest.TestCase):
    """``generate()`` returns the same shape as the OpenAI sibling."""

    def _adapter(self, client: Any, *, model: str | None = None) -> ClaudeCodeScaffoldAdapter:
        config = ClaudeCodeScaffoldConfig(api_key="sk-ant-test")
        if model is not None:
            config.model = model
        return ClaudeCodeScaffoldAdapter(config=config, client=client)

    def test_generate_returns_sanitized_manifest(self) -> None:
        fake_client = MagicMock()
        fake_client.messages.create.return_value = _fake_scaffold_tool_use_response(
            tool_name="generate_scaffold_manifest",
            args=_ok_manifest(),
        )
        adapter = self._adapter(fake_client)

        result = adapter.generate(
            project_title="Notes",
            summary_markdown="A small note-taking app.",
            topics=[],
            decisions=[],
        )

        self.assertEqual(result["framework"], "react-vite")
        self.assertEqual(result["language"], "typescript")
        self.assertEqual(len(result["files"]), 5)
        # Sanitizer bookkeeping (parity with OpenAI adapter path).
        self.assertIn("_sanitize", result)

    def test_generate_wires_anthropic_envelope(self) -> None:
        fake_client = MagicMock()
        fake_client.messages.create.return_value = _fake_scaffold_tool_use_response(
            tool_name="generate_scaffold_manifest",
            args=_ok_manifest(),
        )
        adapter = self._adapter(fake_client)
        adapter.generate(
            project_title="Notes",
            summary_markdown="A small note-taking app.",
            topics=[],
            decisions=[],
        )

        call_kwargs = fake_client.messages.create.call_args.kwargs
        # Flat tool_choice shape — no ``function`` wrapper like OpenAI.
        self.assertEqual(
            call_kwargs["tool_choice"],
            {"type": "tool", "name": "generate_scaffold_manifest"},
        )
        # System prompt passes via the top-level kwarg, not messages.
        self.assertIn("first-draft code architect", call_kwargs["system"])
        # Tool spec uses Anthropic's flat envelope (name + input_schema).
        tool_spec = call_kwargs["tools"][0]
        self.assertEqual(tool_spec["name"], "generate_scaffold_manifest")
        self.assertIn("input_schema", tool_spec)
        self.assertNotIn("function", tool_spec)
        # Anthropic requires max_tokens on every call.
        self.assertIn("max_tokens", call_kwargs)

    def test_generate_model_override_pins_per_call_model(self) -> None:
        """The artifact endpoint passes CLAUDE_CODEGEN_MODEL — it must win."""
        fake_client = MagicMock()
        fake_client.messages.create.return_value = _fake_scaffold_tool_use_response(
            tool_name="generate_scaffold_manifest",
            args=_ok_manifest(),
        )
        adapter = self._adapter(fake_client, model="claude-sonnet-4-5-20250929")
        adapter.generate(
            project_title="Notes",
            summary_markdown="",
            topics=[],
            decisions=[],
            model_override="claude-opus-4-7",
        )
        call_kwargs = fake_client.messages.create.call_args.kwargs
        self.assertEqual(call_kwargs["model"], "claude-opus-4-7")
        # Config wasn't mutated — tier dispatcher reuses the adapter
        # across calls and per-call overrides must NOT leak.
        self.assertEqual(adapter.config.model, "claude-sonnet-4-5-20250929")

    def test_generate_rejects_empty_project_title(self) -> None:
        adapter = self._adapter(MagicMock())
        with self.assertRaises(ValueError):
            adapter.generate(
                project_title="",
                summary_markdown="",
                topics=[],
                decisions=[],
            )


class ClaudeCodeScaffoldEditTests(unittest.TestCase):
    """``edit()`` returns the same shape + persists the explanation field."""

    def _adapter(self, client: Any) -> ClaudeCodeScaffoldAdapter:
        return ClaudeCodeScaffoldAdapter(
            config=ClaudeCodeScaffoldConfig(api_key="sk-ant-test"),
            client=client,
        )

    def _edit_args(self) -> dict:
        manifest = _ok_manifest()
        manifest["explanation"] = (
            "I added the 100ms debounce on the storage event listener."
        )
        return manifest

    def test_edit_returns_explanation_and_files(self) -> None:
        fake_client = MagicMock()
        fake_client.messages.create.return_value = _fake_scaffold_tool_use_response(
            tool_name="edit_scaffold_manifest",
            args=self._edit_args(),
        )
        adapter = self._adapter(fake_client)

        result = adapter.edit(
            project_title="Notes",
            current_files=[{"path": "src/main.tsx", "content": "console.log('hi')\n"}],
            user_message="Add a 100ms debounce.",
        )

        self.assertEqual(
            result["explanation"],
            "I added the 100ms debounce on the storage event listener.",
        )
        self.assertEqual(len(result["files"]), 5)

    def test_edit_pins_edit_tool_name_in_tool_choice(self) -> None:
        fake_client = MagicMock()
        fake_client.messages.create.return_value = _fake_scaffold_tool_use_response(
            tool_name="edit_scaffold_manifest",
            args=self._edit_args(),
        )
        adapter = self._adapter(fake_client)
        adapter.edit(
            project_title="Notes",
            current_files=[{"path": "x", "content": "y"}],
            user_message="Tweak it.",
        )
        call_kwargs = fake_client.messages.create.call_args.kwargs
        self.assertEqual(
            call_kwargs["tool_choice"],
            {"type": "tool", "name": "edit_scaffold_manifest"},
        )
        # System prompt is the EDIT prompt, not the generate prompt.
        self.assertIn("edit mode", call_kwargs["system"])

    def test_edit_rejects_empty_user_message(self) -> None:
        adapter = self._adapter(MagicMock())
        with self.assertRaises(ValueError):
            adapter.edit(
                project_title="Notes",
                current_files=[{"path": "x", "content": "y"}],
                user_message="",
            )


# ---------------------------------------------------------------------------
# repo_context grounding (Wave F.1, #147)
# ---------------------------------------------------------------------------


def _ok_repo_context() -> dict[str, Any]:
    """Canonical fixture mirroring fetch_repo_context()'s return shape."""
    return {
        "repo_full_name": "acme/widget-app",
        "default_branch": "main",
        "head_sha": "abc1234",
        "top_level_files": [
            {"path": "package.json", "type": "file"},
            {"path": "README.md", "type": "file"},
            {"path": "src", "type": "dir"},
        ],
        "readme_excerpt": "# Widget App\n\nA tool for managing widgets.",
        "manifest_kind": "package.json",
        "manifest_excerpt": (
            '{"name": "widget-app", "version": "1.0.0", '
            '"dependencies": {"react": "^18.0.0"}}'
        ),
        "fetched_at": "2026-05-13T00:00:00Z",
    }


class RepoContextSectionTests(unittest.TestCase):
    """Pure-function tests on the prompt renderer (no LLM call)."""

    def test_returns_empty_string_when_none(self) -> None:
        self.assertEqual(repo_context_section(None), "")

    def test_returns_empty_string_when_empty_dict(self) -> None:
        self.assertEqual(repo_context_section({}), "")

    def test_renders_repo_metadata_top_level_readme_and_manifest(self) -> None:
        out = repo_context_section(_ok_repo_context())
        # Header + repo metadata
        self.assertIn("## Repository context", out)
        self.assertIn("acme/widget-app", out)
        self.assertIn("branch: main", out)
        # Top-level files
        self.assertIn("**Top-level files:**", out)
        self.assertIn("`package.json` (file)", out)
        self.assertIn("`src` (dir)", out)
        # README
        self.assertIn("**README excerpt:**", out)
        self.assertIn("Widget App", out)
        # Manifest
        self.assertIn("**Manifest (package.json):**", out)
        self.assertIn("widget-app", out)
        # Closing instruction
        self.assertIn("Do not invent module names", out)

    def test_handles_missing_optional_fields_gracefully(self) -> None:
        """Empty repos surface no readme / manifest — must not crash."""
        out = repo_context_section({
            "repo_full_name": "acme/empty",
            "default_branch": "main",
            "head_sha": "deadbeef",
            "top_level_files": [],
            "readme_excerpt": None,
            "manifest_kind": None,
            "manifest_excerpt": None,
            "fetched_at": "2026-05-13T00:00:00Z",
        })
        self.assertIn("acme/empty", out)
        # Optional sections absent
        self.assertNotIn("**Top-level files:**", out)
        self.assertNotIn("**README excerpt:**", out)
        self.assertNotIn("**Manifest", out)


class CodeScaffoldGenerateRepoContextTests(unittest.TestCase):
    """OpenAI ``CodeScaffoldAdapter.generate()`` threads repo_context.

    The OpenAI sibling's ``_call_with_toolcall_retry`` path is harder
    to stub end-to-end (custom toolcall envelope), so these tests
    exercise the prompt-assembly indirectly: they call ``generate()``
    expecting it to fail downstream, then inspect the system prompt
    string captured on the OpenAI client mock's ``create.call_args``.
    """

    def _adapter(self) -> CodeScaffoldAdapter:
        return CodeScaffoldAdapter(
            config=CodeScaffoldConfig(api_key="test"),
            client=MagicMock(),
        )

    def _captured_system_prompt(self, adapter: CodeScaffoldAdapter) -> str:
        """Pull the system prompt out of the OpenAI mock's first call."""
        call_kwargs = adapter.client.chat.completions.create.call_args.kwargs
        messages = call_kwargs["messages"]
        return next(
            m["content"] for m in messages if m["role"] == "system"
        )

    def test_generate_appends_repo_context_section_when_provided(self) -> None:
        adapter = self._adapter()
        # The mocked OpenAI client returns a non-tool-call response, so
        # _call_with_toolcall_retry will eventually raise — but
        # ``create`` is invoked first, so call_args is populated.
        try:
            adapter.generate(
                project_title="Notes",
                summary_markdown="A small note-taking app.",
                topics=[],
                decisions=[],
                repo_context=_ok_repo_context(),
            )
        except Exception:  # noqa: BLE001 — expected; we only need call_args
            pass
        system_prompt = self._captured_system_prompt(adapter)
        self.assertIn("first-draft code architect", system_prompt)
        self.assertIn("## Repository context", system_prompt)
        self.assertIn("acme/widget-app", system_prompt)
        self.assertIn("package.json", system_prompt)

    def test_generate_omits_repo_context_section_when_none(self) -> None:
        adapter = self._adapter()
        try:
            adapter.generate(
                project_title="Notes",
                summary_markdown="A small note-taking app.",
                topics=[],
                decisions=[],
                # repo_context defaults to None
            )
        except Exception:  # noqa: BLE001
            pass
        system_prompt = self._captured_system_prompt(adapter)
        self.assertIn("first-draft code architect", system_prompt)
        self.assertNotIn("## Repository context", system_prompt)
        self.assertNotIn("Repository:", system_prompt)


class ClaudeCodeScaffoldGenerateRepoContextTests(unittest.TestCase):
    """Claude ``ClaudeCodeScaffoldAdapter.generate()`` threads repo_context."""

    def _adapter(self, client: Any) -> ClaudeCodeScaffoldAdapter:
        return ClaudeCodeScaffoldAdapter(
            config=ClaudeCodeScaffoldConfig(api_key="sk-ant-test"),
            client=client,
        )

    def test_generate_appends_repo_context_section_when_provided(self) -> None:
        fake_client = MagicMock()
        fake_client.messages.create.return_value = (
            _fake_scaffold_tool_use_response(
                tool_name="generate_scaffold_manifest",
                args=_ok_manifest(),
            )
        )
        adapter = self._adapter(fake_client)
        adapter.generate(
            project_title="Notes",
            summary_markdown="A small note-taking app.",
            topics=[],
            decisions=[],
            repo_context=_ok_repo_context(),
        )
        call_kwargs = fake_client.messages.create.call_args.kwargs
        system_prompt = call_kwargs["system"]
        # Existing scaffold prompt content still present (regression).
        self.assertIn("first-draft code architect", system_prompt)
        # New repo-context block is appended.
        self.assertIn("## Repository context", system_prompt)
        self.assertIn("acme/widget-app", system_prompt)
        self.assertIn("package.json", system_prompt)
        self.assertIn("Widget App", system_prompt)

    def test_generate_omits_repo_context_section_when_none(self) -> None:
        fake_client = MagicMock()
        fake_client.messages.create.return_value = (
            _fake_scaffold_tool_use_response(
                tool_name="generate_scaffold_manifest",
                args=_ok_manifest(),
            )
        )
        adapter = self._adapter(fake_client)
        adapter.generate(
            project_title="Notes",
            summary_markdown="A small note-taking app.",
            topics=[],
            decisions=[],
            # repo_context defaults to None
        )
        call_kwargs = fake_client.messages.create.call_args.kwargs
        system_prompt = call_kwargs["system"]
        self.assertIn("first-draft code architect", system_prompt)
        self.assertNotIn("## Repository context", system_prompt)
        self.assertNotIn("Repository:", system_prompt)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
