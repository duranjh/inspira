"""Embedding clustering tests (W2 F5+ embeddings slice).

Pure-function cosine math + cluster assignment behaviour with
mocked embeddings (no real OpenAI calls). The threshold + running-
average centroid behaviour are tested separately so future
threshold tuning doesn't accidentally break the centroid math.
"""
from __future__ import annotations

import json
import os
import unittest
from unittest.mock import MagicMock

from planning_studio_service.feedback_items import (
    cluster as fc,
    embedding as fe,
    store as fi_store,
)
from planning_studio_service.feedback_items.embedding import EMBEDDING_DIMS

try:
    from ._helpers import make_test_app
except ImportError:
    from _helpers import make_test_app  # type: ignore[no-redef]


def _vec_dim(value: float) -> list[float]:
    """Build a length-EMBEDDING_DIMS vector full of `value`."""
    return [value] * EMBEDDING_DIMS


def _vec_first(value_first: float, rest: float = 0.0) -> list[float]:
    """Build a vector where the first element is `value_first` and
    all others are `rest`. Useful for axis-aligned similarity tests."""
    out = [rest] * EMBEDDING_DIMS
    out[0] = value_first
    return out


class CosineSimilarityTests(unittest.TestCase):

    def test_identical_vectors_score_1(self) -> None:
        v = _vec_dim(0.5)
        self.assertAlmostEqual(fc.cosine_similarity(v, v), 1.0, places=6)

    def test_orthogonal_vectors_score_0(self) -> None:
        a = _vec_first(1.0)
        b = _vec_dim(0.0)
        b[1] = 1.0  # Different axis from a's first.
        self.assertAlmostEqual(fc.cosine_similarity(a, b), 0.0, places=6)

    def test_anti_parallel_vectors_score_minus_1(self) -> None:
        a = _vec_first(1.0)
        b = _vec_first(-1.0)
        self.assertAlmostEqual(fc.cosine_similarity(a, b), -1.0, places=6)

    def test_zero_norm_returns_zero(self) -> None:
        a = _vec_dim(0.0)
        b = _vec_first(1.0)
        self.assertEqual(fc.cosine_similarity(a, b), 0.0)

    def test_length_mismatch_returns_zero(self) -> None:
        self.assertEqual(fc.cosine_similarity([1.0, 0.0], [1.0, 0.0, 0.0]), 0.0)


class IsEmbeddingsEnabledTests(unittest.TestCase):

    def setUp(self) -> None:
        self._old_flag = os.environ.pop("INSPIRA_EMBEDDINGS", None)
        self._old_key = os.environ.pop("OPENAI_API_KEY", None)

    def tearDown(self) -> None:
        if self._old_flag is not None:
            os.environ["INSPIRA_EMBEDDINGS"] = self._old_flag
        else:
            os.environ.pop("INSPIRA_EMBEDDINGS", None)
        if self._old_key is not None:
            os.environ["OPENAI_API_KEY"] = self._old_key
        else:
            os.environ.pop("OPENAI_API_KEY", None)

    def test_off_when_flag_unset(self) -> None:
        self.assertFalse(fe.is_embeddings_enabled())

    def test_off_when_key_missing(self) -> None:
        os.environ["INSPIRA_EMBEDDINGS"] = "1"
        self.assertFalse(fe.is_embeddings_enabled())

    def test_on_when_both_set(self) -> None:
        os.environ["INSPIRA_EMBEDDINGS"] = "1"
        os.environ["OPENAI_API_KEY"] = "sk-test"
        self.assertTrue(fe.is_embeddings_enabled())


class EmbedTextTests(unittest.TestCase):

    def test_returns_none_on_empty(self) -> None:
        self.assertIsNone(fe.embed_text(""))
        self.assertIsNone(fe.embed_text("   "))

    def test_returns_vector_from_mock(self) -> None:
        client = MagicMock()
        # OpenAI SDK returns objects with .data[].embedding
        from types import SimpleNamespace

        client.embeddings.create.return_value = SimpleNamespace(
            data=[SimpleNamespace(embedding=_vec_dim(0.1))]
        )
        out = fe.embed_text("hello", client=client)
        self.assertIsNotNone(out)
        self.assertEqual(len(out), EMBEDDING_DIMS)

    def test_returns_none_on_api_failure(self) -> None:
        client = MagicMock()
        client.embeddings.create.side_effect = RuntimeError("boom")
        self.assertIsNone(fe.embed_text("hello", client=client))

    def test_returns_none_on_wrong_dim(self) -> None:
        client = MagicMock()
        from types import SimpleNamespace

        client.embeddings.create.return_value = SimpleNamespace(
            data=[SimpleNamespace(embedding=[0.1, 0.2, 0.3])]  # wrong dim
        )
        self.assertIsNone(fe.embed_text("hello", client=client))


class EmbedTextsBatchTests(unittest.TestCase):

    def test_batch_returns_list_in_order(self) -> None:
        client = MagicMock()
        from types import SimpleNamespace

        client.embeddings.create.return_value = SimpleNamespace(
            data=[
                SimpleNamespace(embedding=_vec_dim(0.1)),
                SimpleNamespace(embedding=_vec_dim(0.2)),
            ]
        )
        out = fe.embed_texts_batch(["a", "b"], client=client)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0][0], 0.1)
        self.assertEqual(out[1][0], 0.2)

    def test_batch_skips_empty_inputs(self) -> None:
        client = MagicMock()
        from types import SimpleNamespace

        # Only one non-empty entry → API called with 1 item.
        client.embeddings.create.return_value = SimpleNamespace(
            data=[SimpleNamespace(embedding=_vec_dim(0.5))]
        )
        out = fe.embed_texts_batch(["", "hello", "  "], client=client)
        self.assertEqual(out[0], None)
        self.assertEqual(out[2], None)
        self.assertEqual(out[1][0], 0.5)


class ClusterAssignmentTests(unittest.TestCase):

    def setUp(self) -> None:
        self.client, self.store, _, self.temp_dir = make_test_app()
        self.workspace_id = "ws-cluster-test"

    def tearDown(self) -> None:
        del self.temp_dir

    def _seed_item(self, title: str, suffix: str = "") -> str:
        item_id, _ = fi_store.upsert_item(
            self.store,
            workspace_id=self.workspace_id,
            source="csv-import",
            external_id=None,
            title=title + suffix,
            body="",
        )
        return item_id

    def test_first_item_creates_new_cluster(self) -> None:
        item_id = self._seed_item("Login crashes")
        cluster_id, was_new = fc.assign_or_create_cluster(
            self.store,
            workspace_id=self.workspace_id,
            item_id=item_id,
            embedding=_vec_first(1.0),
        )
        self.assertTrue(was_new)
        self.assertTrue(cluster_id.startswith("cl-"))

    def test_similar_item_joins_existing_cluster(self) -> None:
        a_id = self._seed_item("First login crash")
        a_cluster, _ = fc.assign_or_create_cluster(
            self.store,
            workspace_id=self.workspace_id,
            item_id=a_id,
            embedding=_vec_first(1.0),
        )
        # Second item with very similar embedding (same axis,
        # slightly different magnitude → cosine is still 1.0).
        b_id = self._seed_item("Second login crash")
        b_cluster, was_new = fc.assign_or_create_cluster(
            self.store,
            workspace_id=self.workspace_id,
            item_id=b_id,
            embedding=_vec_first(0.9, rest=0.0),
        )
        self.assertFalse(was_new)
        self.assertEqual(a_cluster, b_cluster)

    def test_dissimilar_item_creates_new_cluster(self) -> None:
        a_id = self._seed_item("Login crashes")
        a_cluster, _ = fc.assign_or_create_cluster(
            self.store,
            workspace_id=self.workspace_id,
            item_id=a_id,
            embedding=_vec_first(1.0),
        )
        # Different axis → cosine 0.
        b_id = self._seed_item("Loving the new dashboard")
        b_vec = _vec_dim(0.0)
        b_vec[1] = 1.0
        b_cluster, was_new = fc.assign_or_create_cluster(
            self.store,
            workspace_id=self.workspace_id,
            item_id=b_id,
            embedding=b_vec,
        )
        self.assertTrue(was_new)
        self.assertNotEqual(a_cluster, b_cluster)

    def test_item_count_increments_on_join(self) -> None:
        a_id = self._seed_item("First")
        cluster_id, _ = fc.assign_or_create_cluster(
            self.store,
            workspace_id=self.workspace_id,
            item_id=a_id,
            embedding=_vec_first(1.0),
        )
        b_id = self._seed_item("Second")
        fc.assign_or_create_cluster(
            self.store,
            workspace_id=self.workspace_id,
            item_id=b_id,
            embedding=_vec_first(1.0),
        )
        clusters = fc.list_clusters_for_inbox(
            self.store, workspace_id=self.workspace_id
        )
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0]["item_count"], 2)

    def test_cluster_id_persisted_on_item(self) -> None:
        item_id = self._seed_item("test")
        cluster_id, _ = fc.assign_or_create_cluster(
            self.store,
            workspace_id=self.workspace_id,
            item_id=item_id,
            embedding=_vec_first(1.0),
        )
        item = fi_store.get_item(
            self.store,
            item_id=item_id,
            workspace_id=self.workspace_id,
        )
        assert item is not None
        self.assertEqual(item.cluster_id, cluster_id)

    def test_workspace_isolation(self) -> None:
        # Same embedding, two workspaces → two distinct clusters.
        ws_a = "ws-iso-a"
        ws_b = "ws-iso-b"
        a_id, _ = fi_store.upsert_item(
            self.store, workspace_id=ws_a, source="csv-import",
            external_id=None, title="iso test",
        )
        b_id, _ = fi_store.upsert_item(
            self.store, workspace_id=ws_b, source="csv-import",
            external_id=None, title="iso test",
        )
        a_cluster, _ = fc.assign_or_create_cluster(
            self.store, workspace_id=ws_a, item_id=a_id,
            embedding=_vec_first(1.0),
        )
        b_cluster, _ = fc.assign_or_create_cluster(
            self.store, workspace_id=ws_b, item_id=b_id,
            embedding=_vec_first(1.0),
        )
        self.assertNotEqual(a_cluster, b_cluster)


class ClustersEndpointTests(unittest.TestCase):

    def test_list_clusters_returns_empty_when_none(self) -> None:
        client, _, _, temp_dir = make_test_app()
        try:
            from ._helpers import signup_and_login
        except ImportError:
            from _helpers import signup_and_login  # type: ignore[no-redef]
        signup_and_login(
            client, email="member@acme.com", password="password123",
            display_name="Member",
        )
        client.post(
            "/api/v2/workspaces",
            json={"slug": "acme-corp", "name": "Acme Corp"},
        )
        try:
            resp = client.get("/api/v2/connectors/feedback/clusters")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json(), {"clusters": []})
        finally:
            del temp_dir


if __name__ == "__main__":
    unittest.main()
