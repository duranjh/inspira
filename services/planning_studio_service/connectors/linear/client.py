"""Linear GraphQL client (W2 F4).

Linear exposes a single GraphQL endpoint at
``https://api.linear.app/graphql``. All requests authenticate
via ``Authorization: <api_key>`` (no Bearer prefix per the
Linear docs). The API key shape matches
``lin_(api|oauth)_[A-Za-z0-9_-]{20,}``.

Surface kept narrow: validate-key (used by the Connect dialog)
and list-issues (used by the sync job). Webhook + write paths
are out of scope for F4.
"""
from __future__ import annotations

from typing import Any

import httpx


LINEAR_API_URL = "https://api.linear.app/graphql"

VIEWER_QUERY = "query { viewer { id name email } }"

ISSUES_QUERY = """
query Issues($first: Int!, $after: String) {
  issues(
    first: $first,
    after: $after,
    orderBy: updatedAt
  ) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id
      identifier
      title
      description
      url
      createdAt
      updatedAt
      priority
      state { name type }
      creator { name email }
    }
  }
}
"""


class LinearAuthError(Exception):
    """API key was rejected or revoked."""


class LinearRateLimited(Exception):
    """API quota exhausted; retry later."""


class LinearTransient(Exception):
    """5xx / network blip — caller may retry."""


async def validate_key(
    api_key: str,
    *,
    http: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Round-trip a `viewer` query to confirm the key works.

    Returns the viewer object on success — caller persists the
    ``id`` and ``name`` as the workspace's account_login. Raises
    ``LinearAuthError`` on 401 / 403, ``LinearTransient`` on
    network or 5xx blips.
    """
    owns_client = http is None
    if http is None:
        http = httpx.AsyncClient(timeout=10.0)
    try:
        resp = await http.post(
            LINEAR_API_URL,
            headers={
                "Authorization": api_key,
                "Content-Type": "application/json",
            },
            json={"query": VIEWER_QUERY},
        )
    except httpx.HTTPError as exc:
        raise LinearTransient(str(exc)) from exc
    finally:
        if owns_client:
            await http.aclose()

    if resp.status_code in (401, 403):
        raise LinearAuthError(
            f"Linear rejected the API key (status {resp.status_code})"
        )
    if resp.status_code == 429:
        raise LinearRateLimited("Linear rate limit hit")
    if resp.status_code >= 500:
        raise LinearTransient(f"Linear 5xx: {resp.status_code}")

    body = resp.json()
    if body.get("errors"):
        # Linear surfaces auth errors via 200 + errors[] when the
        # key is malformed but still parseable; treat as auth.
        raise LinearAuthError(
            f"Linear errors: {body['errors']}"
        )
    viewer = body.get("data", {}).get("viewer")
    if not viewer:
        raise LinearAuthError("Linear viewer response empty")
    return viewer


TEAM_LABELS_QUERY = """
query TeamLabels($teamId: String!, $first: Int!) {
  team(id: $teamId) {
    labels(first: $first) {
      nodes { id name color }
    }
  }
}
"""

ISSUE_CREATE_MUTATION = """
mutation IssueCreate($input: IssueCreateInput!) {
  issueCreate(input: $input) {
    success
    issue { id identifier url title }
  }
}
"""


def _raise_for_response(resp: httpx.Response) -> None:
    """Translate a Linear HTTP response into typed exceptions.

    Used by all GraphQL helpers below. Centralizes the 401/403 →
    auth, 429 → rate, 5xx → transient mapping so each call site
    doesn't reimplement it.
    """
    if resp.status_code in (401, 403):
        raise LinearAuthError(
            f"Linear rejected the request (status {resp.status_code})"
        )
    if resp.status_code == 429:
        raise LinearRateLimited("Linear rate limit hit")
    if resp.status_code >= 500:
        raise LinearTransient(f"Linear 5xx: {resp.status_code}")
    if resp.status_code >= 400:
        # Other 4xx (400 malformed query, 404 unknown team, etc.) — surface
        # as transient so the router maps them to a 502 with a clean
        # ``upstream_transient`` code rather than crashing on the
        # subsequent ``resp.json()`` call.
        raise LinearTransient(
            f"Linear {resp.status_code}: {resp.text[:200]}"
        )


async def list_team_labels(
    api_key: str,
    *,
    team_id: str,
    http: httpx.AsyncClient | None = None,
) -> list[dict[str, Any]]:
    """Return the team's issue-label list (id + name + color).

    Used by the export flow to resolve a priority-label name (e.g.
    ``"P1"``) to the UUID that ``issueCreate.labelIds`` requires.
    Linear does not support label-by-name on issue creation; you
    must resolve to id first.
    """
    owns_client = http is None
    if http is None:
        http = httpx.AsyncClient(timeout=10.0)
    try:
        resp = await http.post(
            LINEAR_API_URL,
            headers={
                "Authorization": api_key,
                "Content-Type": "application/json",
            },
            json={
                "query": TEAM_LABELS_QUERY,
                "variables": {"teamId": team_id, "first": 100},
            },
        )
    except httpx.HTTPError as exc:
        raise LinearTransient(str(exc)) from exc
    finally:
        if owns_client:
            await http.aclose()
    _raise_for_response(resp)
    body = resp.json()
    if body.get("errors"):
        raise LinearAuthError(f"Linear errors: {body['errors']}")
    team = (body.get("data") or {}).get("team") or {}
    return ((team.get("labels") or {}).get("nodes")) or []


async def create_issue(
    api_key: str,
    *,
    team_id: str,
    title: str,
    description: str = "",
    label_ids: list[str] | None = None,
    parent_id: str | None = None,
    project_id: str | None = None,
    http: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Create a single Linear issue.

    Returns ``{id, identifier, url, title}`` on success. Raises
    ``LinearAuthError`` if the mutation succeeds at the HTTP layer
    but Linear rejects the input (e.g. unknown ``teamId`` —
    Linear surfaces this as ``errors[]`` not 4xx).
    """
    input_payload: dict[str, Any] = {"teamId": team_id, "title": title}
    if description:
        input_payload["description"] = description
    if label_ids:
        input_payload["labelIds"] = list(label_ids)
    if parent_id:
        input_payload["parentId"] = parent_id
    if project_id:
        input_payload["projectId"] = project_id

    owns_client = http is None
    if http is None:
        http = httpx.AsyncClient(timeout=15.0)
    try:
        resp = await http.post(
            LINEAR_API_URL,
            headers={
                "Authorization": api_key,
                "Content-Type": "application/json",
            },
            json={
                "query": ISSUE_CREATE_MUTATION,
                "variables": {"input": input_payload},
            },
        )
    except httpx.HTTPError as exc:
        raise LinearTransient(str(exc)) from exc
    finally:
        if owns_client:
            await http.aclose()
    _raise_for_response(resp)
    body = resp.json()
    if body.get("errors"):
        raise LinearAuthError(f"Linear errors: {body['errors']}")
    payload = ((body.get("data") or {}).get("issueCreate") or {})
    if not payload.get("success") or not payload.get("issue"):
        raise LinearTransient(
            f"Linear issueCreate did not succeed: {payload}"
        )
    return payload["issue"]


async def list_issues(
    api_key: str,
    *,
    page_size: int = 50,
    max_pages: int = 6,
    http: httpx.AsyncClient | None = None,
) -> list[dict[str, Any]]:
    """Pull recent issues across the partner's Linear workspace.

    ``max_pages * page_size`` caps the fetch — F4 ships at 6 × 50
    = 300 issues; F5 will move this to incremental sync via
    updatedAt cursors. The current shape is the "first import"
    snapshot.
    """
    owns_client = http is None
    if http is None:
        http = httpx.AsyncClient(timeout=15.0)
    headers = {
        "Authorization": api_key,
        "Content-Type": "application/json",
    }
    out: list[dict[str, Any]] = []
    cursor: str | None = None
    try:
        for _ in range(max_pages):
            resp = await http.post(
                LINEAR_API_URL,
                headers=headers,
                json={
                    "query": ISSUES_QUERY,
                    "variables": {
                        "first": page_size,
                        "after": cursor,
                    },
                },
            )
            if resp.status_code == 401 or resp.status_code == 403:
                raise LinearAuthError(
                    f"Linear key revoked (status {resp.status_code})"
                )
            if resp.status_code == 429:
                raise LinearRateLimited("Linear rate limit hit")
            if resp.status_code >= 500:
                raise LinearTransient(f"Linear 5xx: {resp.status_code}")
            body = resp.json()
            if body.get("errors"):
                raise LinearAuthError(f"Linear errors: {body['errors']}")
            page = body.get("data", {}).get("issues", {})
            out.extend(page.get("nodes") or [])
            page_info = page.get("pageInfo") or {}
            if not page_info.get("hasNextPage"):
                break
            cursor = page_info.get("endCursor")
            if not cursor:
                break
    except httpx.HTTPError as exc:
        raise LinearTransient(str(exc)) from exc
    finally:
        if owns_client:
            await http.aclose()
    return out
