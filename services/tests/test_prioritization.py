"""F6 ROI prioritization agent tests (W3).

Pure-function ranker behaviour + end-to-end ``run`` that hits the
heuristic fallback path (no OpenAI key in test env). The LLM
path is exercised by mocking the openai SDK module-level.
"""
from __future__ import annotations

import json
import os
import unittest
from typing import Any
from unittest.mock import MagicMock, patch

from planning_studio_service.agents import prioritization as f6
from planning_studio_service.feedback_items import (
    cluster as fc,
    store as fi_store,
)
from planning_studio_service.feedback_items.embedding import EMBEDDING_DIMS

try:
    from ._helpers import make_test_app
except ImportError:
    from _helpers import make_test_app  # type: ignore[no-redef]


def _make_cluster_dict(
    cluster_id: str,
    item_count: int,
    *,
    bug: int = 0,
    feature: int = 0,
    complaint: int = 0,
    praise: int = 0,
    question: int = 0,
    noise: int = 0,
    theme: str | None = None,
) -> dict[str, Any]:
    """Convenience builder for the dict shape ``rank_clusters`` consumes."""
    return {
        "cluster_id": cluster_id,
        "theme": theme,
        "item_count": item_count,
        "category_counts": {
            "bug": bug, "feature": feature, "complaint": complaint,
            "praise": praise, "question": question, "noise": noise,
        },
        "most_recent_ingested_at": "2026-05-03T10:00:00Z",
        "sample_item_ids": [],
    }


class HeuristicRankTests(unittest.TestCase):
    """Pure-function tests on the deterministic fallback ranker."""

    def test_empty_input_returns_empty(self) -> None:
        self.assertEqual(f6._heuristic_rank([]), [])

    def test_bug_heavy_cluster_outranks_question_heavy(self) -> None:
        clusters = [
            _make_cluster_dict("cl-q", 5, question=5),
            _make_cluster_dict("cl-b", 5, bug=5),
        ]
        ranked = f6._heuristic_rank(clusters)
        self.assertEqual(ranked[0]["cluster_id"], "cl-b")
        self.assertEqual(ranked[1]["cluster_id"], "cl-q")
        self.assertEqual(ranked[0]["rank"], 1)
        self.assertEqual(ranked[1]["rank"], 2)

    def test_ranks_are_distinct(self) -> None:
        clusters = [
            _make_cluster_dict("a", 1, bug=1),
            _make_cluster_dict("b", 1, bug=1),
            _make_cluster_dict("c", 1, bug=1),
        ]
        ranked = f6._heuristic_rank(clusters)
        ranks = [r["rank"] for r in ranked]
        self.assertEqual(sorted(ranks), [1, 2, 3])

    def test_score_caps_at_95(self) -> None:
        # 100 bugs would score >> 95; cap should hold.
        clusters = [_make_cluster_dict("huge", 100, bug=100)]
        ranked = f6._heuristic_rank(clusters)
        self.assertLessEqual(ranked[0]["score"], 95.0)
        self.assertGreater(ranked[0]["score"], 5.0)

    def test_praise_only_ranks_low(self) -> None:
        clusters = [
            _make_cluster_dict("praise-only", 10, praise=10),
            _make_cluster_dict("one-bug", 1, bug=1),
        ]
        ranked = f6._heuristic_rank(clusters)
        # The single bug should beat 10 praise items (praise weight = 0).
        self.assertEqual(ranked[0]["cluster_id"], "one-bug")

    def test_includes_suggested_theme_label(self) -> None:
        clusters = [_make_cluster_dict("a", 1, bug=1, theme="Login bugs")]
        ranked = f6._heuristic_rank(clusters)
        # When theme is present we reuse it as the label.
        self.assertEqual(ranked[0]["suggested_theme_label"], "Login bugs")

    def test_no_theme_falls_back_to_generic_label(self) -> None:
        clusters = [_make_cluster_dict("a", 1, bug=1, theme=None)]
        ranked = f6._heuristic_rank(clusters)
        self.assertTrue(ranked[0]["suggested_theme_label"].startswith("Cluster"))


class IsOpenAIAvailableTests(unittest.TestCase):

    def setUp(self) -> None:
        self._old = os.environ.pop("OPENAI_API_KEY", None)

    def tearDown(self) -> None:
        if self._old is not None:
            os.environ["OPENAI_API_KEY"] = self._old
        else:
            os.environ.pop("OPENAI_API_KEY", None)

    def test_off_when_key_missing(self) -> None:
        self.assertFalse(f6._is_openai_available())

    def test_off_when_key_blank(self) -> None:
        os.environ["OPENAI_API_KEY"] = "   "
        self.assertFalse(f6._is_openai_available())

    def test_on_when_key_present(self) -> None:
        os.environ["OPENAI_API_KEY"] = "sk-test"
        self.assertTrue(f6._is_openai_available())


class EndToEndRunTests(unittest.TestCase):
    """``run()`` against a real store with seeded clusters."""

    def setUp(self) -> None:
        self.client, self.store, _, self.temp_dir = make_test_app()
        # Force the heuristic path — no LLM in test env.
        self._old_key = os.environ.pop("OPENAI_API_KEY", None)
        self.workspace_id = "ws-f6-test"

    def tearDown(self) -> None:
        if self._old_key is not None:
            os.environ["OPENAI_API_KEY"] = self._old_key
        del self.temp_dir

    def _seed_cluster(
        self,
        *,
        cluster_id_seed: str,
        items: list[tuple[str, str]],  # (title, type_hint)
        theme: str | None = None,
        embedding_axis: int = 0,
    ) -> str:
        """Seed a cluster + N items with typed hints. Returns cluster_id."""
        embedding = [0.0] * EMBEDDING_DIMS
        embedding[embedding_axis] = 1.0
        first_item_id = None
        for title, type_hint in items:
            item_id, _ = fi_store.upsert_item(
                self.store,
                workspace_id=self.workspace_id,
                source="csv-import",
                external_id=None,
                title=title,
                body="",
                type_hint=type_hint,
            )
            if first_item_id is None:
                first_item_id = item_id
        # Run cluster assignment for the first item; subsequent items get
        # joined under the same cluster because their embeddings match.
        cluster_id = None
        for title, _ in items:
            # Re-fetch item by title and assign — simplest path.
            with self.store._connect() as connection:
                row = connection.execute(
                    "SELECT item_id FROM feedback_items "
                    "WHERE workspace_id = ? AND title = ?",
                    (self.workspace_id, title),
                ).fetchone()
            if row is None:
                continue
            cid, _ = fc.assign_or_create_cluster(
                self.store,
                workspace_id=self.workspace_id,
                item_id=row[0],
                embedding=embedding,
            )
            cluster_id = cid
        if theme is not None and cluster_id is not None:
            with self.store._connect() as connection:
                connection.execute(
                    "UPDATE feedback_clusters SET theme = ? WHERE cluster_id = ?",
                    (theme, cluster_id),
                )
                connection.commit()
        return cluster_id or ""

    def test_empty_workspace_completes_with_zero_themes(self) -> None:
        run_id = f6.run(
            self.store,
            workspace_id=self.workspace_id,
            triggered_by="user-test",
        )
        from planning_studio_service.orchestrator_store import (
            get_prioritization_run,
        )
        prio = get_prioritization_run(
            self.store,
            workspace_id=self.workspace_id,
            run_id=run_id,
        )
        self.assertIsNotNone(prio)
        self.assertEqual(prio["status"], "completed")
        self.assertEqual(prio["output"]["themes"], [])
        self.assertEqual(prio["output"]["input_cluster_count"], 0)

    def test_run_persists_ranked_themes(self) -> None:
        self._seed_cluster(
            cluster_id_seed="bug",
            items=[
                ("Login crashes", "bug"),
                ("Login fails", "bug"),
                ("Login broken", "bug"),
            ],
            embedding_axis=0,
        )
        self._seed_cluster(
            cluster_id_seed="praise",
            items=[("Love the new UI", "praise")],
            embedding_axis=1,
        )
        run_id = f6.run(
            self.store,
            workspace_id=self.workspace_id,
            triggered_by="user-test",
        )
        from planning_studio_service.orchestrator_store import (
            get_prioritization_run,
        )
        prio = get_prioritization_run(
            self.store,
            workspace_id=self.workspace_id,
            run_id=run_id,
        )
        self.assertEqual(prio["status"], "completed")
        themes = prio["output"]["themes"]
        self.assertEqual(len(themes), 2)
        # Bug cluster should rank first (heuristic fallback).
        first = themes[0]
        self.assertEqual(first["rank"], 1)
        self.assertGreater(first["score"], themes[1]["score"])
        # Provenance roll-up is attached.
        self.assertIn("provenance", first)
        self.assertEqual(
            first["provenance"]["item_count"],
            first["provenance"]["category_counts"].get("bug", 0)
            + first["provenance"]["category_counts"].get("praise", 0),
        )

    def test_theme_backfilled_when_missing(self) -> None:
        cluster_id = self._seed_cluster(
            cluster_id_seed="nofill",
            items=[("Crash A", "bug"), ("Crash B", "bug")],
            theme=None,
            embedding_axis=2,
        )
        f6.run(
            self.store,
            workspace_id=self.workspace_id,
            triggered_by="user-test",
        )
        with self.store._connect() as connection:
            row = connection.execute(
                "SELECT theme FROM feedback_clusters WHERE cluster_id = ?",
                (cluster_id,),
            ).fetchone()
        # Heuristic path produces "Cluster N" when no theme exists.
        self.assertIsNotNone(row[0])
        self.assertNotEqual(row[0], "")

    def test_existing_theme_preserved(self) -> None:
        cluster_id = self._seed_cluster(
            cluster_id_seed="hastheme",
            items=[("Bug X", "bug")],
            theme="Original theme",
            embedding_axis=3,
        )
        f6.run(
            self.store,
            workspace_id=self.workspace_id,
            triggered_by="user-test",
        )
        with self.store._connect() as connection:
            row = connection.execute(
                "SELECT theme FROM feedback_clusters WHERE cluster_id = ?",
                (cluster_id,),
            ).fetchone()
        self.assertEqual(row[0], "Original theme")

    def test_workspace_isolation(self) -> None:
        self._seed_cluster(
            cluster_id_seed="ws-a",
            items=[("WS A bug", "bug")],
            embedding_axis=0,
        )
        # Switch workspace, seed something else.
        original_ws = self.workspace_id
        self.workspace_id = "ws-b-isolation"
        self._seed_cluster(
            cluster_id_seed="ws-b",
            items=[("WS B feature", "feature")],
            embedding_axis=1,
        )
        # Run F6 on workspace B; assert WS A's cluster is invisible.
        run_id_b = f6.run(
            self.store,
            workspace_id=self.workspace_id,
            triggered_by="user-test",
        )
        from planning_studio_service.orchestrator_store import (
            get_prioritization_run,
        )
        prio_b = get_prioritization_run(
            self.store,
            workspace_id=self.workspace_id,
            run_id=run_id_b,
        )
        cluster_ids_b = [t["cluster_id"] for t in prio_b["output"]["themes"]]
        self.assertEqual(len(cluster_ids_b), 1)
        # Cross-workspace read returns None.
        prio_a_cross = get_prioritization_run(
            self.store,
            workspace_id=original_ws,
            run_id=run_id_b,
        )
        self.assertIsNone(prio_a_cross)


def _openai_response(text: str) -> Any:
    """Build a minimal OpenAI ChatCompletion response shape."""
    from types import SimpleNamespace

    message = SimpleNamespace(content=text)
    choice = SimpleNamespace(message=message)
    return SimpleNamespace(choices=[choice])


class LLMRankTests(unittest.TestCase):
    """LLM path with the openai SDK mocked at module level."""

    def setUp(self) -> None:
        self._old = os.environ.get("OPENAI_API_KEY")
        os.environ["OPENAI_API_KEY"] = "sk-test"

    def tearDown(self) -> None:
        if self._old is not None:
            os.environ["OPENAI_API_KEY"] = self._old
        else:
            os.environ.pop("OPENAI_API_KEY", None)

    def test_returns_none_when_openai_import_fails(self) -> None:
        clusters = [_make_cluster_dict("a", 1, bug=1)]
        # Patch builtins.__import__ to fail openai load.
        import builtins

        real_import = builtins.__import__

        def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "openai":
                raise ImportError("no openai in test")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=fake_import):
            self.assertIsNone(f6._llm_rank(clusters))

    def test_parses_valid_llm_response(self) -> None:
        clusters = [
            _make_cluster_dict("cl-a", 5, bug=5),
            _make_cluster_dict("cl-b", 3, feature=3),
        ]
        fake_response = _openai_response(
            json.dumps(
                {
                    "themes": [
                        {
                            "cluster_id": "cl-a", "rank": 1,
                            "score": 88.5,
                            "rationale": "Active bug burst.",
                            "suggested_theme_label": "Login bugs",
                        },
                        {
                            "cluster_id": "cl-b", "rank": 2,
                            "score": 65.0,
                            "rationale": "Feature ask.",
                            "suggested_theme_label": "Bulk export",
                        },
                    ]
                }
            )
        )
        fake_client = MagicMock()
        fake_client.chat.completions.create.return_value = fake_response
        # Inject the patched OpenAI class so the function's
        # `from openai import OpenAI` resolves to our mock.
        fake_module = MagicMock()
        fake_module.OpenAI.return_value = fake_client
        with patch.dict("sys.modules", {"openai": fake_module}):
            ranked = f6._llm_rank(clusters)
        self.assertIsNotNone(ranked)
        self.assertEqual(len(ranked), 2)
        self.assertEqual(ranked[0]["cluster_id"], "cl-a")
        self.assertEqual(ranked[0]["rank"], 1)
        self.assertEqual(ranked[0]["score"], 88.5)

    def test_strips_markdown_fences(self) -> None:
        clusters = [_make_cluster_dict("cl-a", 5, bug=5)]
        fenced = "```json\n" + json.dumps(
            {
                "themes": [
                    {
                        "cluster_id": "cl-a", "rank": 1, "score": 90,
                        "rationale": "x", "suggested_theme_label": "y",
                    }
                ]
            }
        ) + "\n```"
        fake_response = _openai_response(fenced)
        fake_client = MagicMock()
        fake_client.chat.completions.create.return_value = fake_response
        fake_module = MagicMock()
        fake_module.OpenAI.return_value = fake_client
        with patch.dict("sys.modules", {"openai": fake_module}):
            ranked = f6._llm_rank(clusters)
        self.assertIsNotNone(ranked)
        self.assertEqual(ranked[0]["cluster_id"], "cl-a")

    def test_returns_none_on_invalid_json(self) -> None:
        clusters = [_make_cluster_dict("cl-a", 5, bug=5)]
        fake_response = _openai_response("not even close to JSON")
        fake_client = MagicMock()
        fake_client.chat.completions.create.return_value = fake_response
        fake_module = MagicMock()
        fake_module.OpenAI.return_value = fake_client
        with patch.dict("sys.modules", {"openai": fake_module}):
            ranked = f6._llm_rank(clusters)
        self.assertIsNone(ranked)

    def test_patches_missing_clusters_with_heuristic(self) -> None:
        """LLM only ranks one of two clusters → the missing one gets
        appended via heuristic so output matches input length."""
        clusters = [
            _make_cluster_dict("cl-a", 5, bug=5),
            _make_cluster_dict("cl-b", 3, feature=3),
        ]
        fake_response = _openai_response(
            json.dumps(
                {
                    "themes": [
                        {
                            "cluster_id": "cl-a", "rank": 1,
                            "score": 88,
                            "rationale": "x",
                            "suggested_theme_label": "y",
                        }
                    ]
                }
            )
        )
        fake_client = MagicMock()
        fake_client.chat.completions.create.return_value = fake_response
        fake_module = MagicMock()
        fake_module.OpenAI.return_value = fake_client
        with patch.dict("sys.modules", {"openai": fake_module}):
            ranked = f6._llm_rank(clusters)
        # rank_clusters checks len matches, but _llm_rank itself returns
        # the patched list (full length when patching extras).
        self.assertEqual(len(ranked), 2)
        cluster_ids = {r["cluster_id"] for r in ranked}
        self.assertEqual(cluster_ids, {"cl-a", "cl-b"})

    def test_55_cluster_ranking_no_truncation(self) -> None:
        """Pinned regression for issue #117.

        With Claude Sonnet at ``max_tokens=2048``, ranking 50+ clusters
        produced output that truncated mid-JSON, fell back to the
        heuristic ranker, and surfaced ``"Cluster N"`` placeholder
        labels in the demo. The swap to GPT-5-mini with
        ``max_completion_tokens=16384`` + ``reasoning_effort="low"``
        and ``response_format={"type": "json_object"}`` makes the
        failure mode unreachable.

        This test seeds 55 distinct clusters, returns a complete
        55-entry JSON ranking from the mocked OpenAI client, and
        asserts the output has 55 entries with the LLM rationales
        intact (not heuristic placeholders).
        """
        clusters = [
            _make_cluster_dict(f"cl-{i:02d}", item_count=2, bug=2)
            for i in range(55)
        ]
        themes_payload = [
            {
                "cluster_id": f"cl-{i:02d}",
                "rank": i + 1,
                "score": float(95 - i),
                "rationale": f"LLM rationale for cl-{i:02d}",
                "suggested_theme_label": f"Theme {i:02d}",
            }
            for i in range(55)
        ]
        fake_response = _openai_response(
            json.dumps({"themes": themes_payload})
        )
        fake_client = MagicMock()
        fake_client.chat.completions.create.return_value = fake_response
        fake_module = MagicMock()
        fake_module.OpenAI.return_value = fake_client
        with patch.dict("sys.modules", {"openai": fake_module}):
            ranked = f6._llm_rank(clusters)
        self.assertIsNotNone(ranked)
        self.assertEqual(len(ranked), 55)
        # Every entry carries the LLM rationale, not a heuristic
        # fallback string. This is the bit that broke under truncation.
        for entry in ranked:
            self.assertTrue(
                entry["rationale"].startswith("LLM rationale for "),
                f"unexpected rationale: {entry['rationale']!r}",
            )
        # And rank_clusters should accept the full ranking and report
        # the LLM model, not the heuristic fallback string.
        with patch.dict("sys.modules", {"openai": fake_module}):
            full_ranked, model_used = f6.rank_clusters(clusters)
        self.assertEqual(len(full_ranked), 55)
        self.assertEqual(model_used, f6.PRIORITIZATION_MODEL)


if __name__ == "__main__":
    unittest.main()
