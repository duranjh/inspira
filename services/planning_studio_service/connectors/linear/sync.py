"""Linear sync — pull issues + write to feedback_items.

Mirrors the GitHub sync.py shape: open a connector_sync_run row,
fetch from the connector, write rows, close the run. Workspace-
scoped throughout — the credential lookup is via
``(workspace_id, 'linear')`` composite PK.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import httpx

from ...byok import decrypt_api_key
from ...feedback_items import cluster as feedback_cluster
from ...feedback_items import store as feedback_store
from ...feedback_items.embedding import (
    embed_texts_batch,
    is_embeddings_enabled,
)
from ...feedback_items.llm_classify import (
    ItemForClassify,
    classify_items_with_fallback,
    is_llm_enabled,
)
from .. import store as connectors_store
from . import client as linear_client

if TYPE_CHECKING:
    from ...store import PlanningStudioStore


logger = logging.getLogger(__name__)


def _issue_body(node: dict[str, Any]) -> str:
    """Compose the body string Inspira stores for a Linear issue.

    Linear's `description` is markdown; we pass it through
    unchanged. Empty descriptions get the URL appended so the
    feedback row at least carries the link back to the source.
    """
    body = (node.get("description") or "").strip()
    url = node.get("url") or ""
    if not body and url:
        return url
    if body and url:
        return f"{body}\n\n{url}"
    return body


async def sync_workspace(
    *,
    store: "PlanningStudioStore",
    workspace_id: str,
    trigger: str = "manual",
) -> dict[str, Any]:
    """Run a Linear sync for ``workspace_id``.

    Returns a small summary dict drained back to the caller (used
    for tests + logs). On auth errors we mark the credential as
    ``needs_reauth`` so the FE flips the tile to the error state.
    """
    cred = connectors_store.get_credential(
        store, workspace_id=workspace_id, provider="linear"
    )
    if cred is None:
        return {"status": "no_credential", "items_ingested": 0}

    run_id = connectors_store.start_sync_run(
        store,
        workspace_id=workspace_id,
        provider="linear",
        trigger=trigger,
    )

    try:
        api_key = decrypt_api_key(cred["encrypted_token"])
    except Exception as exc:  # noqa: BLE001
        connectors_store.finish_sync_run(
            store,
            run_id=run_id,
            status="error",
            repos_synced=0,
            error=f"decrypt: {exc!s}",
        )
        return {"status": "error", "items_ingested": 0}

    inserted = 0
    skipped = 0
    try:
        async with httpx.AsyncClient(timeout=15.0) as http:
            issues = await linear_client.list_issues(api_key, http=http)

        # F5+ batched LLM classify: when enabled, classify all
        # issues in batched LLM calls before iterating into
        # upsert_item. With the flag off, the per-item fallback
        # in upsert_item handles classification.
        prepared: list[tuple[dict[str, Any], str, str]] = []
        for node in issues:
            title = (node.get("title") or "").strip()
            if not title:
                continue
            prepared.append((node, title, _issue_body(node)))

        llm_categories: list[str] = []
        if is_llm_enabled() and prepared:
            llm_categories = classify_items_with_fallback(
                [ItemForClassify(title=title, body=body) for _, title, body in prepared]
            )

        new_item_payload: list[tuple[str, str, str]] = []
        for idx, (node, title, body) in enumerate(prepared):
            try:
                external_id = node.get("id") or node.get("identifier")
                hint: str | None = None
                if idx < len(llm_categories):
                    hint = llm_categories[idx]
                item_id, was_new = feedback_store.upsert_item(
                    store,
                    workspace_id=workspace_id,
                    source="linear",
                    external_id=external_id,
                    title=title,
                    body=body,
                    author=(node.get("creator") or {}).get("name"),
                    author_email=(node.get("creator") or {}).get("email"),
                    received_at=node.get("createdAt"),
                    type_hint=hint,
                    raw_payload=node,
                )
                if was_new:
                    inserted += 1
                    new_item_payload.append((item_id, title, body))
                else:
                    skipped += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "linear sync: skipping malformed issue %s: %s",
                    node.get("id"),
                    exc,
                )

        # Embedding-based clustering for new Linear issues.
        if new_item_payload and is_embeddings_enabled():
            texts = [f"{t}\n{b}".strip() for _, t, b in new_item_payload]
            vectors = embed_texts_batch(texts)
            for (item_id, _, _), vec in zip(new_item_payload, vectors):
                if vec is None:
                    continue
                try:
                    feedback_cluster.assign_or_create_cluster(
                        store,
                        workspace_id=workspace_id,
                        item_id=item_id,
                        embedding=vec,
                    )
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "linear sync: cluster assign failed for %s", item_id
                    )
    except linear_client.LinearAuthError as exc:
        connectors_store.mark_credential_status(
            store,
            workspace_id=workspace_id,
            provider="linear",
            status="needs_reauth",
        )
        connectors_store.finish_sync_run(
            store,
            run_id=run_id,
            status="needs_reauth",
            repos_synced=inserted,
            error=str(exc),
        )
        return {
            "status": "needs_reauth",
            "items_ingested": inserted,
        }
    except linear_client.LinearRateLimited as exc:
        connectors_store.finish_sync_run(
            store,
            run_id=run_id,
            status="rate_limited",
            repos_synced=inserted,
            error=str(exc),
        )
        return {"status": "rate_limited", "items_ingested": inserted}
    except linear_client.LinearTransient as exc:
        connectors_store.finish_sync_run(
            store,
            run_id=run_id,
            status="error",
            repos_synced=inserted,
            error=str(exc),
        )
        return {"status": "error", "items_ingested": inserted}

    # Mirror GitHub sync semantics: account_login is the partner's
    # workspace-side identity. We refresh it on every successful
    # run so renames don't go stale.
    try:
        viewer = await linear_client.validate_key(api_key)
        connectors_store.upsert_credential(
            store,
            workspace_id=workspace_id,
            provider="linear",
            encrypted_token=cred["encrypted_token"],
            installation_id=None,
            account_login=viewer.get("name"),
            scopes=[],
        )
    except linear_client.LinearAuthError:
        # If validate fails after a successful issue pull, leave
        # the credential as-is — the sync itself worked.
        pass

    connectors_store.finish_sync_run(
        store,
        run_id=run_id,
        status="ok",
        repos_synced=inserted,
        error=None,
    )
    return {
        "status": "ok",
        "items_ingested": inserted,
        "items_skipped": skipped,
    }
