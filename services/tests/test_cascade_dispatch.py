"""W2 cascade dispatch tests.

Stubs the OpenAI call (via ``openai_client`` parameter) and asserts:
- new ``decision_versions`` rows persisted with correct version_int
- ``decisions.current_version_int`` advanced
- ``change_note`` populated
- per-decision failure isolation (one regen errors → siblings still complete)
- missing OPENAI_API_KEY → cascade fails fast with structured error
- gpt-5-mini path invoked (NOT Anthropic)
"""
from __future__ import annotations

import asyncio
import json
import os
import unittest
from typing import Any
from unittest.mock import patch

from planning_studio_service import cascade_store
from planning_studio_service.agents import cascade

try:
    from ._helpers import make_test_app
except ImportError:
    from _helpers import make_test_app  # type: ignore[no-redef]


class _StubChoice:
    def __init__(self, content: str) -> None:
        self.message = type("M", (), {"content": content})


class _StubCompletion:
    def __init__(self, content: str) -> None:
        self.choices = [_StubChoice(content)]


class _StubChatCompletions:
    def __init__(
        self,
        *,
        responses: dict[str, str] | None = None,
        raise_for: set[str] | None = None,
    ) -> None:
        self.responses = responses or {}
        self.raise_for = raise_for or set()
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        # Look up response by the decision statement embedded in the prompt.
        user_msg = kwargs["messages"][-1]["content"]
        for marker, content in self.responses.items():
            if marker in user_msg:
                if marker in self.raise_for:
                    raise RuntimeError(f"stub_failure: {marker}")
                return _StubCompletion(content)
        # Default canned response.
        if "_default" in self.raise_for:
            raise RuntimeError("stub_default_failure")
        return _StubCompletion(
            json.dumps({
                "statement": "Rewritten decision.",
                "rationale": "Updated rationale.",
                "change_note": "Refined per user comment.",
            })
        )


class _StubClient:
    def __init__(self, **kwargs: Any) -> None:
        self.chat = type("C", (), {"completions": _StubChatCompletions(**kwargs)})()


class CascadeDispatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client, self.store, _, self.temp_dir = make_test_app()
        self.workspace_id = "ws-cascade-disp"
        self.project_id = "proj-cascade-disp"
        self.user_id = "user-cascade-disp"
        # Seed OPENAI_API_KEY for is_openai_available (stub doesn't need real key)
        self._old_key = os.environ.get("OPENAI_API_KEY")
        os.environ["OPENAI_API_KEY"] = "sk-test-stub"

    def tearDown(self) -> None:
        if self._old_key is None:
            os.environ.pop("OPENAI_API_KEY", None)
        else:
            os.environ["OPENAI_API_KEY"] = self._old_key
        self.temp_dir.cleanup()

    def _seed_decision(
        self, *, statement: str, topic_id: str | None = None,
    ) -> str:
        if topic_id is None:
            topic_id = self.store.create_topic(
                project_id=self.project_id, title="T", icon="flag",
            )["topic_id"]
        return self.store.create_decision(
            topic_id=topic_id, project_id=self.project_id,
            statement=statement, proposed_by="orchestrator",
        )["decision_id"]

    def _make_cascade(
        self, *, scope_mode: str, commented: list[dict[str, Any]],
    ) -> str:
        return cascade_store.create_cascade_run(
            self.store,
            workspace_id=self.workspace_id,
            project_id=self.project_id,
            triggered_by=self.user_id,
            scope_mode=scope_mode,
            commented_decisions=commented,
        )

    # ----- happy path ----------------------------------------------

    def test_local_mode_persists_v2_for_each_commented(self) -> None:
        d1 = self._seed_decision(statement="Use Postgres")
        cascade_id = self._make_cascade(
            scope_mode="local",
            commented=[{"decision_id": d1, "comment_text": "make it cheaper"}],
        )
        client = _StubClient(responses={
            "Use Postgres": json.dumps({
                "statement": "Use SQLite for v0.",
                "rationale": "Cheaper to operate.",
                "change_note": "Switched to SQLite per cost concern.",
            }),
        })
        asyncio.run(cascade.run_cascade(
            self.store,
            workspace_id=self.workspace_id,
            project_id=self.project_id,
            cascade_id=cascade_id,
            user_id=self.user_id,
            scope_mode="local",
            commented_decisions=[{"decision_id": d1, "comment_text": "make it cheaper"}],
            openai_client=client,
        ))
        run = cascade_store.get_cascade_run(
            self.store, workspace_id=self.workspace_id, cascade_id=cascade_id,
        )
        assert run is not None
        self.assertEqual(run["status"], "complete")
        self.assertEqual(run["diff_summary"]["updated_count"], 1)
        self.assertEqual(run["diff_summary"]["failed_count"], 0)
        # v2 row written
        v2 = cascade_store.get_decision_version(
            self.store, decision_id=d1, version_int=2,
        )
        assert v2 is not None
        self.assertEqual(v2["statement"], "Use SQLite for v0.")
        self.assertEqual(v2["cascade_id"], cascade_id)
        # current_version_int bumped
        self.assertEqual(
            cascade_store.get_latest_version_int(self.store, decision_id=d1),
            2,
        )
        # decisions.statement updated
        decision = self.store.get_decision(d1)
        assert decision is not None
        self.assertEqual(decision["statement"], "Use SQLite for v0.")
        # change_note populated
        self.assertEqual(v2["change_note"], "Switched to SQLite per cost concern.")
        # cascaded_from_decision_ids points to commented set
        self.assertEqual(v2["cascaded_from_decision_ids"], [d1])
        # v1 lazy-snapshot exists
        v1 = cascade_store.get_decision_version(
            self.store, decision_id=d1, version_int=1,
        )
        assert v1 is not None
        self.assertEqual(v1["statement"], "Use Postgres")

    def test_second_cascade_on_already_cascaded_decision(self) -> None:
        """C1/H1 regression: cascade #2 on a decision whose v2 is already
        in decision_versions correctly computes new_v=3 (not v=2 again,
        which would UNIQUE-violate). Exercises the self-healing
        get_latest_version_int path that now reads MAX(version_int)
        from decision_versions instead of decisions.current_version_int.
        """
        d1 = self._seed_decision(statement="Use Postgres")
        # Cascade #1
        cascade_id_1 = self._make_cascade(
            scope_mode="local",
            commented=[{"decision_id": d1, "comment_text": "first edit"}],
        )
        client_1 = _StubClient(responses={
            "Use Postgres": json.dumps({
                "statement": "Use SQLite for v0.",
                "rationale": "Cheaper.",
                "change_note": "first.",
            }),
        })
        asyncio.run(cascade.run_cascade(
            self.store,
            workspace_id=self.workspace_id,
            project_id=self.project_id,
            cascade_id=cascade_id_1,
            user_id=self.user_id,
            scope_mode="local",
            commented_decisions=[{"decision_id": d1, "comment_text": "first edit"}],
            openai_client=client_1,
        ))
        self.assertEqual(
            cascade_store.get_latest_version_int(self.store, decision_id=d1),
            2,
        )
        # Cascade #2 — the LLM sees the v2 statement now.
        cascade_id_2 = self._make_cascade(
            scope_mode="local",
            commented=[{"decision_id": d1, "comment_text": "second edit"}],
        )
        client_2 = _StubClient(responses={
            "Use SQLite for v0.": json.dumps({
                "statement": "Use SQLite with WAL mode for v0.",
                "rationale": "Better concurrency.",
                "change_note": "second.",
            }),
        })
        asyncio.run(cascade.run_cascade(
            self.store,
            workspace_id=self.workspace_id,
            project_id=self.project_id,
            cascade_id=cascade_id_2,
            user_id=self.user_id,
            scope_mode="local",
            commented_decisions=[{"decision_id": d1, "comment_text": "second edit"}],
            openai_client=client_2,
        ))
        run_2 = cascade_store.get_cascade_run(
            self.store, workspace_id=self.workspace_id, cascade_id=cascade_id_2,
        )
        assert run_2 is not None
        self.assertEqual(run_2["status"], "complete")
        self.assertEqual(run_2["diff_summary"]["updated_count"], 1)
        # v3 row exists with prior_version_id pointing at v2.
        self.assertEqual(
            cascade_store.get_latest_version_int(self.store, decision_id=d1),
            3,
        )
        v3 = cascade_store.get_decision_version(
            self.store, decision_id=d1, version_int=3,
        )
        assert v3 is not None
        self.assertEqual(v3["statement"], "Use SQLite with WAL mode for v0.")
        v2 = cascade_store.get_decision_version(
            self.store, decision_id=d1, version_int=2,
        )
        assert v2 is not None
        self.assertEqual(v3["prior_version_id"], v2["version_id"])

    def test_cascade_mode_rewrites_siblings_too(self) -> None:
        topic = self.store.create_topic(
            project_id=self.project_id, title="Auth", icon="flag",
        )["topic_id"]
        d_main = self.store.create_decision(
            topic_id=topic, project_id=self.project_id,
            statement="Use JWT", proposed_by="orchestrator",
        )["decision_id"]
        d_sib = self.store.create_decision(
            topic_id=topic, project_id=self.project_id,
            statement="Refresh tokens every 15min", proposed_by="orchestrator",
        )["decision_id"]
        cascade_id = self._make_cascade(
            scope_mode="cascade",
            commented=[{"decision_id": d_main, "comment_text": "switch to opaque tokens"}],
        )
        client = _StubClient(responses={
            "Use JWT": json.dumps({
                "statement": "Use opaque session tokens.",
                "rationale": "Session-server pattern.",
                "change_note": "Per user comment, swap to opaque.",
            }),
            "Refresh tokens every 15min": json.dumps({
                "statement": "Server-side session expiry every 30min.",
                "rationale": "Adapted to opaque tokens.",
                "change_note": "Cascade-aligned with main change.",
            }),
        })
        asyncio.run(cascade.run_cascade(
            self.store,
            workspace_id=self.workspace_id, project_id=self.project_id,
            cascade_id=cascade_id, user_id=self.user_id,
            scope_mode="cascade",
            commented_decisions=[{"decision_id": d_main, "comment_text": "switch to opaque tokens"}],
            openai_client=client,
        ))
        run = cascade_store.get_cascade_run(
            self.store, workspace_id=self.workspace_id, cascade_id=cascade_id,
        )
        assert run is not None
        self.assertEqual(run["status"], "complete")
        self.assertEqual(run["diff_summary"]["updated_count"], 2)
        self.assertEqual(run["affected_scope"]["count"], 1)  # 1 sibling affected

    # ----- failure isolation ---------------------------------------

    def test_per_decision_failure_isolation(self) -> None:
        topic = self.store.create_topic(
            project_id=self.project_id, title="X", icon="flag",
        )["topic_id"]
        d_a = self.store.create_decision(
            topic_id=topic, project_id=self.project_id,
            statement="Decision A original", proposed_by="orchestrator",
        )["decision_id"]
        d_b = self.store.create_decision(
            topic_id=topic, project_id=self.project_id,
            statement="Decision B original", proposed_by="orchestrator",
        )["decision_id"]
        cascade_id = self._make_cascade(
            scope_mode="local",
            commented=[
                {"decision_id": d_a, "comment_text": "comment A"},
                {"decision_id": d_b, "comment_text": "comment B"},
            ],
        )
        client = _StubClient(
            responses={
                "Decision A original": json.dumps({
                    "statement": "A rewritten.",
                    "rationale": "ok",
                    "change_note": "ok",
                }),
                "Decision B original": "INVALID-NOT-JSON",
            },
        )
        asyncio.run(cascade.run_cascade(
            self.store,
            workspace_id=self.workspace_id, project_id=self.project_id,
            cascade_id=cascade_id, user_id=self.user_id,
            scope_mode="local",
            commented_decisions=[
                {"decision_id": d_a, "comment_text": "comment A"},
                {"decision_id": d_b, "comment_text": "comment B"},
            ],
            openai_client=client,
        ))
        run = cascade_store.get_cascade_run(
            self.store, workspace_id=self.workspace_id, cascade_id=cascade_id,
        )
        assert run is not None
        self.assertEqual(run["status"], "complete")
        self.assertEqual(run["diff_summary"]["updated_count"], 1)
        self.assertEqual(run["diff_summary"]["failed_count"], 1)
        # A succeeded
        self.assertEqual(
            cascade_store.get_latest_version_int(self.store, decision_id=d_a), 2,
        )
        # B did not advance
        self.assertEqual(
            cascade_store.get_latest_version_int(self.store, decision_id=d_b), 1,
        )

    # ----- guardrails -----------------------------------------------

    def test_missing_openai_key_fails_fast(self) -> None:
        d1 = self._seed_decision(statement="Anything")
        cascade_id = self._make_cascade(
            scope_mode="local",
            commented=[{"decision_id": d1, "comment_text": "x"}],
        )
        os.environ.pop("OPENAI_API_KEY", None)
        asyncio.run(cascade.run_cascade(
            self.store,
            workspace_id=self.workspace_id, project_id=self.project_id,
            cascade_id=cascade_id, user_id=self.user_id,
            scope_mode="local",
            commented_decisions=[{"decision_id": d1, "comment_text": "x"}],
        ))
        run = cascade_store.get_cascade_run(
            self.store, workspace_id=self.workspace_id, cascade_id=cascade_id,
        )
        assert run is not None
        self.assertEqual(run["status"], "failed")
        self.assertIn("OPENAI_API_KEY", run["error"])
        # No version row created
        self.assertEqual(
            cascade_store.get_latest_version_int(self.store, decision_id=d1), 1,
        )

    def test_gpt_path_invoked_not_anthropic(self) -> None:
        d1 = self._seed_decision(statement="orig")
        cascade_id = self._make_cascade(
            scope_mode="local",
            commented=[{"decision_id": d1, "comment_text": "x"}],
        )
        client = _StubClient()
        # Provider-swap PR (#117) removed the Anthropic path from sub_agent
        # entirely — `_call_anthropic` no longer exists, so the original
        # patch on it AttributeError'd. The invariant we want to assert
        # ("cascade calls OpenAI, not Anthropic") is now structural: the
        # only LLM call site is `_call_openai`. The stub client's call
        # count below confirms the OpenAI dispatch fired exactly once.
        asyncio.run(cascade.run_cascade(
            self.store,
            workspace_id=self.workspace_id, project_id=self.project_id,
            cascade_id=cascade_id, user_id=self.user_id,
            scope_mode="local",
            commented_decisions=[{"decision_id": d1, "comment_text": "x"}],
            openai_client=client,
        ))
        # Confirm the gpt-5-mini stub was hit
        self.assertEqual(len(client.chat.completions.calls), 1)
        call = client.chat.completions.calls[0]
        self.assertEqual(call["model"], "gpt-5-mini")
        self.assertEqual(call["reasoning_effort"], "low")
        self.assertEqual(call["max_completion_tokens"], 4096)
        self.assertEqual(call["response_format"], {"type": "json_object"})


if __name__ == "__main__":
    unittest.main()
