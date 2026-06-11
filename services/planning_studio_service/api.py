"""FastAPI HTTP layer for Inspira/planning-studio.

Replaces the legacy BaseHTTPServer-based ``app.py``. All routes preserve the
exact URL paths and JSON payload shapes the frontend already depends on —
this is a straight port, not a redesign.

Why FastAPI over BaseHTTPServer:
- Production-grade ASGI server (uvicorn) with proper graceful shutdown.
- Built-in OpenAPI docs at ``/docs`` and ``/redoc``.
- Cleaner middleware story for CORS, rate limits, sessions, Sentry.
- Dependency injection for auth / current user / rate limit tokens.

The store layer (``store.PlanningStudioStore``) and agent adapter
(``agents.OpenAIPlanningInterviewer``) are untouched — this module only
owns the HTTP dispatch.

Auth is added incrementally; see ``auth.py`` and the ``Depends(current_user)``
injections on protected routes. During the transition phase (no user signed
in, legacy seed project) requests fall through to a system user so the
existing frontend keeps working end-to-end.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Path, Request, WebSocket, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field, field_validator
from starlette.middleware.base import BaseHTTPMiddleware

from .config import load_config
from .validators import SanitizedStr
# MarkdownImportBody is used as a route annotation. Because api.py has
# ``from __future__ import annotations``, the annotation is stored as a
# string and resolved at runtime against the route function's __globals__
# (i.e. this module's top-level namespace). If the import were inside
# create_app(), the name would only exist in that function's locals, and
# FastAPI's Pydantic walker would see a ForwardRef('MarkdownImportBody')
# it can't resolve — breaking both /openapi.json (500) and every POST to
# /api/v2/projects/from-markdown (422 "body: field required"). Keeping
# the import here makes both endpoints usable.
from .markdown_import import MarkdownImportBody
# JsonImportBody has the same ForwardRef story — see MarkdownImportBody
# above. Importing at module scope (not inside create_app) is what lets
# POST /api/v2/projects/from-json resolve its body model correctly.
from .json_import import JsonImportBody
from .metrics import metrics_collector
# SSE helpers for /kickoff/stream and /turn/stream. These are routed to by
# inline references (``sse_stream(...)``, ``format_sse(...)``) inside the
# route coroutines defined in ``create_app`` — they must live in module
# globals so the route's __globals__ can resolve the bare names at call
# time. A missing import here surfaces as ``NameError: name 'sse_stream'
# is not defined`` the first time a user clicks "Map it" (observed
# in smoke testing).
from .sse import format_sse, sse_stream
from .thinking_messages import thinking_message
from .store import VALID_TOPIC_STATUSES, PlanningStudioStore, now_timestamp
from .dedupe_merge import merge_topics
from . import realtime


# Defined at module scope (not inside create_app) on purpose: the module
# uses ``from __future__ import annotations`` (line 22), so FastAPI
# resolves every handler annotation via typing.get_type_hints() against
# this module's globalns. A function-local class becomes an unresolvable
# ForwardRef and FastAPI silently misclassifies the param as a query
# parameter, surfacing as 422 {"loc":["query","body"]} on every POST.
# Sibling body models MarkdownImportBody (markdown_import.py:51) and
# JsonImportBody (json_import.py:61) follow this same pattern — see the
# import-site comments at lines 40-53 for the longer write-up.
class BulkDeleteV2ProjectsBody(BaseModel):
    """Body for POST /api/v2/projects/bulk-delete."""

    project_ids: list[str] = Field(min_length=1, max_length=500)


class ArtifactGenerateBody(BaseModel):
    """Body for POST /api/v2/projects/{id}/artifact/generate/stream.

    ``force=true`` bypasses the cached-manifest early-return so the
    Regenerate kebab can discard and re-draft. Default ``false`` makes
    the auto-fire-on-404 path safe against the impatient-race window
    where a partner clicks Code before the pre-warm BG task finishes
    persisting (#187) — pre-fix, that click cost 2× the gpt-5-mini
    spend; post-fix it replays the cached scaffold instead.
    """

    force: bool = False


# Wave F.6 — "Refresh PR with Inspira" body models. Module-scope per
# feedback_fastapi_future_annotations: with ``from __future__ import
# annotations`` active, FastAPI must be able to resolve the class at
# import time or it silently classifies the param as a query param
# (surfaced as HTTP 403 / WS 1008 on incident #183).

class RefreshResolveDecision(BaseModel):
    """One per-file decision in the refresh-resolve payload."""

    decision: str = Field(
        pattern=r"^(accept_redraft|keep_partner_edit|merged)$",
    )
    merged_content: str | None = None


class RefreshResolveBody(BaseModel):
    """Body for POST /api/v2/projects/{id}/refresh-resolve."""

    refresh_id: str = Field(min_length=1)
    decisions: dict[str, RefreshResolveDecision] = Field(default_factory=dict)


# Ensure root logger has a stderr handler so application logs surface in
# Fly. uvicorn's default LOGGING_CONFIG only configures handlers for the
# `uvicorn` and `uvicorn.access` loggers — the root logger stays
# handler-less, so logger.info()/warning()/exception() calls from app
# code are silently dropped. Surfaced by #096: even with LOG_LEVEL=info
# configured, none of the [document_bg] / [toolcall_retry] /
# [breakered_create] diagnostics reached the deploy host's logs.
# basicConfig is a no-op if root already has handlers (pytest etc), so
# this is safe in test contexts. Sentry's LoggingIntegration attaches
# its own handler at startup; both fire after this.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)

logger = logging.getLogger("planning_studio.api")

# Soft-admin gate for ``/api/admin/metrics``. Real RBAC (an ``is_admin``
# column on users, plus a role model) lands with audit P3 — until then,
# the admin surface is gated on a single canonical email address set via
# ``INSPIRA_ADMIN_EMAIL``. When unset, the admin surface is disabled
# outright. The endpoint still goes through the signed-session
# dependency, so an anonymous attacker can't reach it at all.
_ADMIN_EMAIL = os.environ.get("INSPIRA_ADMIN_EMAIL", "").strip().lower()


# Per-user daily token budget (audit M5). A single user shouldn't be able
# to burn our OpenAI spend — 200k combined prompt+completion tokens/day is
# generous for real use but bounded enough that one rogue session doesn't
# take the service down. Ratchet via INSPIRA_USER_DAILY_TOKEN_BUDGET;
# a non-positive value disables the gate entirely (dev/test escape hatch).
def _load_user_daily_token_budget() -> int:
    raw = os.environ.get("INSPIRA_USER_DAILY_TOKEN_BUDGET", "").strip()
    if not raw:
        return 200_000
    try:
        value = int(raw)
    except ValueError:
        logger.warning(
            "INSPIRA_USER_DAILY_TOKEN_BUDGET=%r is not an int; using default",
            raw,
        )
        return 200_000
    return value


# Rough chars-per-token used when OpenAI doesn't hand us usage stats.
# Conservative over-estimate beats under-counting because the budget is
# the user-visible cost cap.
_ESTIMATE_CHARS_PER_TOKEN = 4


# How long suggestion results stay cached per-user, in seconds. Product
# memo calls for hours, not minutes — regeneration is expensive and the
# signal set doesn't change minute to minute. Invalidated on new project
# creation (see store.create_v2_project).
SUGGESTIONS_CACHE_TTL_SECONDS = 4 * 3600


# Per-user daily URL-fetch cap. Separate from the LLM token budget —
# URL fetches are cheap on OpenAI spend (zero) but can become an
# outbound-bandwidth / reputation issue if abused (someone using
# Inspira as a free proxy to probe arbitrary hosts). Override via
# INSPIRA_USER_DAILY_URL_FETCH_CAP; non-positive disables the gate.
def _load_user_daily_url_fetch_cap() -> int:
    raw = os.environ.get("INSPIRA_USER_DAILY_URL_FETCH_CAP", "").strip()
    if not raw:
        return 200
    try:
        return int(raw)
    except ValueError:
        logger.warning(
            "INSPIRA_USER_DAILY_URL_FETCH_CAP=%r is not an int; using default",
            raw,
        )
        return 200


def _seconds_until_utc_midnight() -> int:
    """Rough seconds until the daily budget resets at next UTC 00:00."""
    now = datetime.now(timezone.utc)
    # ``today_midnight`` is the START of today (UTC). Adding 86400 lands
    # on the START of tomorrow (i.e. next midnight). The previous local
    # variable name "tomorrow" was misleading — it was today's midnight,
    # not tomorrow's. Same arithmetic, clearer name.
    today_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    next_midnight_epoch = today_midnight.timestamp() + 86400
    seconds = int(next_midnight_epoch - now.timestamp())
    return max(60, seconds)  # Avoid zero-or-negative Retry-After headers.


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        value = datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value


# ---------------------------------------------------------------------------
# Security-headers middleware
# ---------------------------------------------------------------------------
# Static suite of response headers applied to EVERY response (including
# error responses and streamed JSON). CSP is also emitted here so JSON
# 4xx/5xx error pages, the OpenAPI docs UI, and any server-rendered
# HTML inherit the same policy as the SPA shell that nginx serves.
# The two layers stay in sync — defence in depth.
#
# HSTS is gated on ENVIRONMENT=production so local HTTP smoke tests don't
# pin the browser to HTTPS.
#
# CSP ships in report-only mode first (INSPIRA_CSP_REPORT_ONLY=true,
# the default) so a missed inline-script case can't break the app on
# rollout. Browsers POST violation reports to /api/csp-report; once a
# few days of reports come back clean, flip the env var to ``false``
# to enforce.
#
# Tunables (read at app construction time):
#   INSPIRA_CSP_REPORT_ONLY   "true" (default) → Content-Security-Policy-Report-Only
#                             "false"          → Content-Security-Policy (enforce)
#   INSPIRA_CSP_DISABLE       "true"           → emit no CSP header at all
#                             (escape hatch for debugging false positives
#                              in production)
#   ENVIRONMENT=production    enables HSTS


# Allowed connect-src origins — only the hosts the SPA actually fetches
# from. Keep tight: any host listed here can receive XHR/fetch/SSE/WS
# from the page, so each addition widens the data-exfiltration surface
# a successful XSS would have. Add new third-party APIs deliberately.
_CSP_CONNECT_SRC = (
    "'self' "
    "https://api.openai.com "
    "https://api.anthropic.com"
)

# 'unsafe-inline' on script-src is intentional for now: Vite injects a
# tiny inline bootstrap script in index.html, and removing it requires a
# nonce-per-request setup we haven't built yet. Tracked through the CSP
# report log — once nonces are wired through nginx, drop it.
# style-src keeps 'unsafe-inline' because emotion/styled style tags are
# everywhere; the same nonce migration applies.
_CSP_DIRECTIVES: tuple[tuple[str, str], ...] = (
    ("default-src", "'self'"),
    ("script-src", "'self' 'unsafe-inline'"),
    ("style-src", "'self' 'unsafe-inline' https://fonts.googleapis.com"),
    ("font-src", "'self' https://fonts.gstatic.com"),
    ("img-src", "'self' data: https:"),
    ("connect-src", _CSP_CONNECT_SRC),
    ("frame-ancestors", "'none'"),
    # Where browsers POST violation reports. Modern browsers prefer the
    # report-to mechanism but most still honour report-uri, and emitting
    # both costs nothing.
    ("report-uri", "/api/csp-report"),
)


def _build_csp_header() -> str:
    """Render ``_CSP_DIRECTIVES`` into a single CSP header value."""
    return "; ".join(f"{name} {value}" for name, value in _CSP_DIRECTIVES)


_STATIC_SECURITY_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    # X-Frame-Options is the legacy clickjacking control; CSP
    # frame-ancestors 'none' supersedes it on modern browsers, but we
    # keep both because some embedded webviews and old corporate
    # browsers still only honour XFO.
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    # Permissions-Policy denies every powerful feature the app does
    # NOT use. The voice realtime feature was scrapped in PR 2 so
    # microphone goes from ``(self)`` back to deny — no UX surface
    # uses getUserMedia anymore.
    "Permissions-Policy": (
        "accelerometer=(), camera=(), geolocation=(), "
        "microphone=(), payment=(), usb=()"
    ),
}


def _csp_header_name(report_only: bool) -> str:
    return (
        "Content-Security-Policy-Report-Only"
        if report_only
        else "Content-Security-Policy"
    )


class _SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Inject the static security-header suite on every response.

    Registered AFTER CORS so CORS's preflight responses are generated
    first and still flow through this middleware; registered BEFORE
    slowapi so rate-limit 429 responses also carry the headers.

    Behavior:
      * Always emits the static suite (X-Content-Type-Options,
        X-Frame-Options, Referrer-Policy, Permissions-Policy).
      * Emits Content-Security-Policy or -Report-Only depending on the
        ``INSPIRA_CSP_REPORT_ONLY`` env var (default: report-only). The
        full policy comes from ``_CSP_DIRECTIVES`` above. Backend CSP
        complements the SPA-shell CSP that nginx serves — defence in
        depth so JSON 4xx/5xx error pages and the OpenAPI docs UI also
        carry a policy.
      * Skips CSP entirely when ``INSPIRA_CSP_DISABLE=true`` (escape
        hatch for debugging false positives in prod).
      * Emits Strict-Transport-Security only when ``is_production`` is
        true so dev HTTP smoke tests don't pin the browser to HTTPS.

    setdefault semantics throughout: a downstream handler that already
    set a header (e.g. a route serving an embeddable widget that needs
    a different X-Frame-Options) wins.
    """

    def __init__(self, app: Any, *, is_production: bool) -> None:
        super().__init__(app)
        self._is_production = is_production
        self._csp_disabled = (
            os.environ.get("INSPIRA_CSP_DISABLE", "").strip().lower() == "true"
        )
        # Default to report-only so the rollout can't break inline-script
        # paths we haven't migrated to nonces yet. Operators flip to
        # ``false`` once /api/csp-report logs are clean.
        report_only_raw = os.environ.get(
            "INSPIRA_CSP_REPORT_ONLY", "true",
        ).strip().lower()
        self._csp_report_only = report_only_raw != "false"
        self._csp_value = _build_csp_header()
        self._csp_header_name = _csp_header_name(self._csp_report_only)

    async def dispatch(self, request: Request, call_next: Any) -> Response:  # noqa: ARG002
        response = await call_next(request)
        for name, value in _STATIC_SECURITY_HEADERS.items():
            # setdefault semantics: don't clobber a header a downstream
            # handler already set (e.g. a custom X-Frame-Options on an
            # embeddable route we might add later).
            if name not in response.headers:
                response.headers[name] = value
        if not self._csp_disabled:
            # Don't double-emit if the route or a downstream piece (e.g.
            # nginx reverse-proxy) already attached a CSP. Either name
            # counts — keeping both could yield contradictory policies.
            if (
                "Content-Security-Policy" not in response.headers
                and "Content-Security-Policy-Report-Only" not in response.headers
            ):
                response.headers[self._csp_header_name] = self._csp_value
        # HSTS is only meaningful over HTTPS. Emit only in production so
        # dev http://localhost traffic doesn't pin HTTPS on the browser.
        #
        # 2026-04-25: deliberately set to max-age=0 to defer the HSTS
        # commitment until after a potential AWS/GCP infra move. The
        # max-age=0 directive actively CLEARS the cached HSTS state in
        # browsers that received the prior 1-year directive earlier
        # tonight — without this, those browsers would refuse HTTP for
        # a year regardless. Bump back up (e.g. 31536000) once the
        # infra plan is settled.
        if self._is_production and "Strict-Transport-Security" not in response.headers:
            response.headers["Strict-Transport-Security"] = "max-age=0"
        return response


class _RequestIdMiddleware(BaseHTTPMiddleware):
    """Attach a stable request id to every response.

    The id is picked up from ``Fly-Request-Id`` (the header Fly.io puts
    on every inbound request at the edge) or ``X-Request-ID`` when it
    already arrives from a reverse proxy; otherwise we mint a short uuid.
    The id is:
      - set on ``request.state.request_id`` so downstream handlers and
        the generic 500 handler can reference it without re-parsing;
      - echoed back on the response as ``X-Request-ID`` so the browser /
        curl / Sentry all see the same value that shows up in fly logs.
    """

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        import uuid as _uuid  # noqa: PLC0415

        incoming = (
            request.headers.get("fly-request-id")
            or request.headers.get("x-request-id")
            or ""
        ).strip()
        request_id = incoming or _uuid.uuid4().hex[:16]
        # Mutate the ASGI scope so ``Request`` instances built later in
        # the pipeline (e.g. inside exception handlers) see it via
        # ``request.state.request_id`` as well.
        request.state.request_id = request_id  # type: ignore[attr-defined]
        response = await call_next(request)
        if "X-Request-ID" not in response.headers:
            response.headers["X-Request-ID"] = request_id
        return response


# ---------------------------------------------------------------------------
# Pydantic models — request bodies only. Response bodies stay dicts for now so
# the frontend keeps seeing the same loose shapes the legacy handlers emit.
# ---------------------------------------------------------------------------


# Hard caps on free-text user input so a bad actor can't burn our OpenAI
# budget with a 10 MB `user_idea`. Tuned generously for real use.
_MAX_IDEA_CHARS = 8000
_MAX_TURN_ANSWER_CHARS = 8000
_MAX_TITLE_CHARS = 200
_MAX_EXCERPT_CHARS = 20000
_MAX_ATTACHMENTS_PER_REQUEST = 10


class CreateSessionBody(BaseModel):
    project_id: str = Field(max_length=200)
    title: SanitizedStr = Field(max_length=_MAX_TITLE_CHARS)
    objective: SanitizedStr = Field(max_length=2000)
    mode: str = Field(default="interview", max_length=40)


class AttachedSource(BaseModel):
    display_name: SanitizedStr = Field(default="", max_length=500)
    kind: str = Field(default="", max_length=60)
    excerpt: SanitizedStr = Field(default="", max_length=_MAX_EXCERPT_CHARS)


class KickoffBody(BaseModel):
    user_idea: SanitizedStr = Field(default="", max_length=_MAX_IDEA_CHARS)
    attached_sources: list[AttachedSource] = Field(
        default_factory=list, max_length=_MAX_ATTACHMENTS_PER_REQUEST,
    )
    locale: str | None = Field(default=None, max_length=10)
    # Per-turn LLM tier override. When set, wins over the user's persisted
    # default (but is still clamped to the user's plan allowance).
    # ``None`` means "use the persisted default or plan default".
    model_tier: str | None = Field(default=None, max_length=40)


class ExtractThemesBody(BaseModel):
    """v4 — pasted customer feedback items to cluster into themes.

    Each item is a single feedback entry (one line / CSV row / JSON
    array element). The endpoint runs a single LLM call (gpt-4o-mini)
    that returns 3-5 themes; the frontend then fires one kickoff per
    theme to auto-generate one project per theme on the workspace home.
    """

    items: list[str] = Field(
        default_factory=list,
        max_length=2000,
        description=(
            "Feedback items to cluster. Each ≤2000 chars. Total cap "
            "of 2000 items keeps the LLM input bounded."
        ),
    )
    locale: str | None = Field(default=None, max_length=10)


class TopicCreateBody(BaseModel):
    title: SanitizedStr = Field(default="", max_length=_MAX_TITLE_CHARS)
    icon: str = Field(default="flag", max_length=40)
    position_x: float = 0.0
    position_y: float = 0.0


class TopicUpdateBody(BaseModel):
    title: SanitizedStr | None = Field(default=None, max_length=_MAX_TITLE_CHARS)
    icon: str | None = Field(default=None, max_length=40)
    position_x: float | None = None
    position_y: float | None = None
    # Status is an enum with three valid values — see store.VALID_TOPIC_STATUSES
    # and the CREATE TABLE topics comment in store.py. Null means "no change".
    # Unknown strings are rejected by ``_validate_topic_status`` below and
    # surfaced to the client as a 400 with the shape
    # ``{"error": "invalid_status", "allowed": [...]}`` so the frontend can
    # render the exact whitelist without parsing prose. The pydantic-level
    # validator keeps a ``max_length`` sanity bound here; enum-whitelisting
    # is enforced in the route before the store ever sees the value.
    status: str | None = Field(default=None, max_length=40)

    @field_validator("status")
    @classmethod
    def _validate_status(cls, value: str | None) -> str | None:
        """Defense-in-depth whitelist check at the pydantic layer.

        Returns the value unchanged when it's None or a known status.
        Raises ``ValueError("invalid_status")`` otherwise — this surfaces
        as a pydantic ValidationError (HTTP 422) so direct callers of the
        model (tests, adjacent routes, future shared utilities) see a
        clear error. The HTTP edge in :func:`v2_update_topic` performs a
        second check and returns the richer ``400 invalid_status`` shape
        the frontend expects.
        """
        if value is None:
            return value
        if value not in VALID_TOPIC_STATUSES:
            raise ValueError("invalid_status")
        return value


class TopicPrivateNotesBody(BaseModel):
    """POST /api/v2/topics/{id}/private-notes.

    ``notes`` is the full replacement content. Null or empty string both
    clear the field. NEVER sent to the LLM — persisted verbatim and read
    back only by endpoints the note owner calls.
    """

    # Soft cap at 10k chars — roughly 2-3 pages of plain text. Longer than
    # that and the user almost certainly wants a decision or a topic, not
    # a private note.
    notes: SanitizedStr | None = Field(default=None, max_length=10_000)


class TopicColorBody(BaseModel):
    """POST /api/v2/topics/{id}/color.

    ``color`` is one of the five allowlisted slugs (``sage``, ``rust``,
    ``gold``, ``ink``, ``paper``) or ``None`` to clear the tag. Any other
    value is rejected at the store layer with a 400; the short max_length
    here is belt-and-suspenders against pathological input.
    """

    color: str | None = Field(default=None, max_length=16)


class TopicTurnBody(BaseModel):
    user_answer: SanitizedStr = Field(default="", max_length=_MAX_TURN_ANSWER_CHARS)
    attached_sources: list[AttachedSource] = Field(
        default_factory=list, max_length=_MAX_ATTACHMENTS_PER_REQUEST,
    )
    locale: str | None = Field(default=None, max_length=10)
    # Same per-turn override as ``KickoffBody.model_tier``. See that doc.
    model_tier: str | None = Field(default=None, max_length=40)


class PreferredModelTierBody(BaseModel):
    """PATCH /api/v2/auth/me/preferred-model-tier.

    ``tier`` may be a slug ("base" / "pro" / "frontier") or ``None`` to
    clear the override (falls back to the plan default).
    """

    tier: str | None = Field(default=None, max_length=40)


class MergeTopicsBody(BaseModel):
    """POST /api/v2/projects/{project_id}/topics/merge — merge two
    duplicate topics proposed by the Duplicates planner view."""

    keep_topic_id: str = Field(min_length=1, max_length=120)
    drop_topic_id: str = Field(min_length=1, max_length=120)


class ExportLoggedBody(BaseModel):
    """POST /api/v2/projects/{project_id}/activity/export-logged.

    Exports in Inspira are assembled client-side (html2pdf, markdown
    builder, JSON blob) so the backend sees no mutation. This ping
    endpoint lets the frontend log an audit row after a successful
    export so the Activity feed can show it alongside everything else.
    ``fmt`` is one of pdf / markdown / json / csv / html.
    """

    fmt: str = Field(min_length=1, max_length=16)


class AccessTokenCreateBody(BaseModel):
    """POST /api/v2/auth/tokens.

    ``name`` is the human label the user types ("Zapier", "My script").
    We cap at 80 chars because anything longer is almost certainly
    a paste-mistake from some other field; the label is purely for the
    user's own list view.
    """

    name: SanitizedStr = Field(min_length=1, max_length=80)


class ByokKeyBody(BaseModel):
    """POST /api/v2/auth/byok body.

    ``provider`` is one of ``openai`` / ``anthropic`` (enforced in the
    route handler, not here, so the error shape is a structured 400
    instead of FastAPI's default 422). ``api_key`` has a generous cap —
    real OpenAI / Anthropic keys are well under 200 chars; the 512-char
    budget covers any future format change.
    """

    provider: str = Field(default="", max_length=40)
    api_key: str = Field(default="", max_length=512)


class DecisionCreateBody(BaseModel):
    statement: SanitizedStr = Field(default="", max_length=2000)
    rationale: SanitizedStr | None = Field(default=None, max_length=4000)
    source_turn_id: str | None = Field(default=None, max_length=200)
    proposed_by: str = Field(default="planner", max_length=40)
    status: str = Field(default="confirmed", max_length=40)


class RelationshipCreateBody(BaseModel):
    source_topic_id: str = Field(default="", max_length=200)
    target_topic_id: str = Field(default="", max_length=200)
    label: SanitizedStr | None = Field(default=None, max_length=120)


class RelationshipPatchBody(BaseModel):
    """L5a — body for `PATCH /api/v2/relationships/{id}`.

    Only `label` is mutable for now. Pass an explicit `null` to
    clear the label (or an empty string — the route handler
    normalizes both to NULL in the DB). Adding more mutable fields
    later (style, weight, direction-flip) is a non-breaking change
    since the model uses optional defaults.
    """
    label: SanitizedStr | None = Field(default=None, max_length=120)


# #094 / Item 3 redesign / domain-aware doc-type generator — body for the
# inline-edit endpoint. Lives at module scope for the same FastAPI /
# `from __future__ annotations` reason as the BusinessPlan* siblings: an
# in-function pydantic class gets misclassified as a query param and the
# request returns 422 before the handler runs. Both fields default to
# None so partial updates are supported (the handler enforces "at least
# one present" with a 422 — pydantic alone can't express that).
class DocumentSectionPatchBody(BaseModel):
    """PATCH /document/{document_id}/section/{section_id} body — user inline edit.

    Either field may be omitted (partial updates supported). At least one
    must be present (validated in the handler). Caps mirror the LLM
    sanitizer's headroom so the field can't grow unboundedly. The BE
    sanitizer doesn't run on user edits — they're trusted relative to
    the user's own data, and renderMarkdown's allowlist on the FE is
    the rendering guardrail.
    """

    title: str | None = Field(default=None, min_length=1, max_length=200)
    prose_markdown: str | None = Field(default=None, min_length=1, max_length=4000)


# #094 follow-up: the FE may now override the auto-derived doc_type
# before generating, in case the kickoff inferred the wrong domain
# (e.g. a "trip" was misidentified as event_plan when the user wanted
# a story_outline). The override is validated against VALID_DOC_TYPES;
# if absent, the handler falls back to project.metadata.domain
# derivation (the original behavior). Module-scope per the
# `from __future__ annotations` gotcha (FastAPI misclassifies
# in-function pydantic classes as query params).
class DocumentGenerateBody(BaseModel):
    """POST /document/generate body — optional doc_type override.

    Both fields default to None. When ``doc_type`` is omitted the
    handler derives it from project.metadata.domain (422 if unmapped).
    When present it's validated against VALID_DOC_TYPES; an invalid
    value 422s. The override applies to this generation only — the
    project's persisted domain is unchanged. (Persistent override is
    tracked as #097.)
    """

    doc_type: str | None = Field(default=None, max_length=64)
    locale: str | None = Field(default=None, max_length=16)


class ProjectCreateBody(BaseModel):
    title: SanitizedStr = Field(default="", max_length=_MAX_TITLE_CHARS)


class ProjectUpdateBody(BaseModel):
    title: SanitizedStr | None = Field(default=None, max_length=_MAX_TITLE_CHARS)


# v4 B3.3 / B1.1 — project state machine + Kanban manual override.
# Bodies live at module scope (not inside ``create_app``) for the same
# FastAPI forward-ref reason that drove ``ProjectCreateBody`` here:
# locally-scoped BaseModel subclasses get misread as query params.
class ProjectTransitionBody(BaseModel):
    """``POST /api/v2/projects/{id}/transition`` — verb-style state move.

    The ``action`` enum is the same set ``next_state_for_action``
    accepts; mismatches return 400 (unknown action) or 409 (illegal
    transition for the current state).
    """

    action: str = Field(
        ..., min_length=1, max_length=24,
        description="One of: start_review | approve | reject",
    )


class ProjectStateOverrideBody(BaseModel):
    """``POST /api/v2/projects/{id}/manual-state-override`` — escape hatch.

    ``note`` is optional — the audit trail captures the actor's
    ``user_id`` from the auth context regardless, so the WHO is always
    recorded; the WHY is a nice-to-have. Product decision:
    the dialog used to force a reason, which felt like friction on
    routine drag-overrides.
    """

    target_state: str = Field(..., min_length=1, max_length=24)
    note: str = Field(default="", max_length=2000)


class ProjectPriorityOrderBody(BaseModel):
    """``POST /api/v2/projects/{id}/manual-priority-order`` — Kanban
    drag-within-column. Sparse 1024-step int; 0 is allowed."""

    priority_order: int = Field(..., ge=0, le=10_000_000)


# Body for ``POST /api/v2/projects/from-template``. The slug is short
# and URL-safe by construction — the 80-char cap is comfortable with
# room for future templates without inviting abuse.
class ProjectFromTemplateBody(BaseModel):
    slug: str = Field(default="", max_length=80)


# Body for ``POST /api/v2/auth/transfer-anonymous-projects``. Lives at
# module scope to dodge the same FastAPI forward-ref quirk — the auth
# module defines an identical model, but we need a copy up here so the
# route handler sees it resolved at import time.
class TransferAnonymousProjectsBody(BaseModel):
    anonymous_user_id: str = Field(min_length=1, max_length=64)


# Body for ``POST /api/v2/projects/from-example``. Same shape as the
# template body — a short slug maps to one of the hand-authored example
# project seeds in :mod:`planning_studio_service.example_projects`.
# Kept at module scope to dodge the known FastAPI forward-ref bug where
# in-function Pydantic models get misread as query params.
from .example_projects import ExampleProjectBody  # noqa: E402, PLC0415


# Shelves — user-owned named containers for grouping projects. The 80-char
# name cap mirrors ``shelves.MAX_SHELF_NAME_CHARS``; all three bodies are
# declared at module scope so FastAPI / pydantic can resolve them (same
# forward-reference hazard that `ProjectCreateBody` fixed for project
# bodies — route handlers defined inside the factory misinterpret a
# locally-scoped BaseModel as a query parameter and return 422).
class ShelfCreateBody(BaseModel):
    name: SanitizedStr = Field(default="", max_length=80)


class ShelfUpdateBody(BaseModel):
    name: SanitizedStr | None = Field(default=None, max_length=80)
    sort_order: int | None = None


class ProjectShelveBody(BaseModel):
    # shelf_id=None un-shelves: the project falls back to the implicit
    # "Unfiled" shelf. An explicit empty string is normalised to None
    # by the route handler.
    shelf_id: str | None = Field(default=None, max_length=80)


# Artifact-mode endpoints (plan summary, outline, dedupe). Only the outline
# endpoint takes a meaningful request body — the artifact_type the user
# picked in the UI. Summary and dedupe take no body.
class OutlineBody(BaseModel):
    artifact_type: str = Field(default="", max_length=200)
    locale: str | None = Field(default=None, max_length=10)


class SummaryBody(BaseModel):
    locale: str | None = Field(default=None, max_length=10)


class DedupeBody(BaseModel):
    locale: str | None = Field(default=None, max_length=10)


class ScaffoldBody(BaseModel):
    locale: str | None = Field(default=None, max_length=10)


class ArtifactEditBody(BaseModel):
    """Body for the chat-driven artifact edit stream.

    The user message itself carries the change request. The current
    artifact state is read server-side from ``metadata_json.artifact``
    rather than passed in the body — keeps the request payload tiny
    and prevents a stale client from overwriting concurrent edits.
    """

    message: str = Field(..., min_length=1, max_length=4000)
    locale: str | None = Field(default=None, max_length=10)


# Billing — Stripe-backed checkout + customer-portal + webhook. The
# bodies are small; the real work happens in the billing provider.
class BillingCheckoutBody(BaseModel):
    plan_slug: str = Field(default="", max_length=40)
    # Billing period for the checkout: "monthly" (default — back-compat
    # for clients on the original API shape) or "annual". The route
    # validates the literal and passes it to start_checkout which picks
    # the matching STRIPE_PRICE_ID_*_ANNUAL env var on annual + falls back
    # to monthly when annual isn't configured for the plan (raises
    # NotConfiguredError so the FE can show a clean error rather than
    # silently switching the user to a monthly charge they didn't pick).
    period: str = Field(default="monthly", max_length=10)


import re as _re_locale


def _validate_locale(raw: str | None) -> str | None:
    """Validate a BCP-47 primary subtag. Returns the lowercased code on
    success, None on invalid input. Silently ignores bad values so a
    malformed locale never breaks LLM-backed routes (best-effort hint).
    """
    if not raw:
        return None
    primary = raw.strip().lower().split("-")[0]
    if _re_locale.fullmatch(r"[a-z]{2}", primary):
        return primary
    return None


# Strings that look like an explicit non-answer. We use this in the
# topic_turn heuristics to decide whether to auto-mark a checkpoint as
# answered + synthesize a proposed decision when gpt-4o-mini stays
# silent on checkpoint_updates / proposed_decisions despite a real reply
# from the user. Lowercased + trimmed before comparison.
_NON_SUBSTANTIVE_REPLIES: frozenset[str] = frozenset({
    "skip", "skipped", "skip this",
    "idk", "i don't know", "i dont know", "no idea", "not sure",
    "?", "??", "???",
    "no", "yes", "y", "n", "yep", "yeah", "yup", "nope", "nah",
    "ok", "okay", "k", "fine", "sure",
    "maybe", "later",
})


def _user_reply_is_substantive(text: str) -> bool:
    """True if a user reply looks like real content the planner can act on.

    Heuristic only — used to gate the auto-answer + decision-synthesis
    safety net in v2_topic_turn / v2_topic_turn_stream when the LLM
    omits checkpoint_updates / proposed_decisions. Conservative: skips
    short or filler replies so we don't synthesize garbage decisions.
    """
    if not text:
        return False
    stripped = text.strip()
    if len(stripped) < 4:
        return False
    if stripped.lower() in _NON_SUBSTANTIVE_REPLIES:
        return False
    return True


def _slugify_for_filename(raw: str) -> str:
    """Slugify a project title for use as a download-filename stem.

    Matches what a user would expect in their Downloads folder: lowercase
    ASCII letters and digits, hyphen-separated, no leading/trailing
    hyphens. Empty output is allowed — callers fall back to a default
    stem when the title doesn't normalize to anything.
    """
    import re as _re

    lowered = (raw or "").lower()
    cleaned = _re.sub(r"[^a-z0-9]+", "-", lowered)
    cleaned = cleaned.strip("-")
    # Keep the slug short; a 60-char filename stem plus the timestamp
    # and extension lands comfortably under common OS filename caps.
    if len(cleaned) > 60:
        cleaned = cleaned[:60].rstrip("-")
    return cleaned


# URL-fetch proxy — the frontend hands a user-supplied URL here because
# browsers block CORS for most sites. The backend fetcher enforces SSRF
# guards, size caps, and content-type allowlists; see fetchers/url.py.
# 2048 chars matches the per-URL length cap enforced by the fetcher.
class FetchUrlBody(BaseModel):
    url: str = Field(default="", max_length=2048)


# Client-error telemetry — the React ErrorBoundary POSTs here when it catches
# a render error in the browser. Body fields are all optional so a partial
# payload (e.g. old bundle without stack) still gets logged.
class ClientErrorBody(BaseModel):
    message: str = Field(default="", max_length=2000)
    stack: str | None = Field(default=None, max_length=20000)
    componentStack: str | None = Field(default=None, max_length=10000)
    href: str | None = Field(default=None, max_length=2048)


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


# Header / field names scrubbed from every Sentry event before send. Mirrors
# the frontend lists in ``app/src/observability/sentry.ts`` so an audit can
# verify both surfaces apply the same redaction policy.
_SENTRY_SENSITIVE_HEADERS = frozenset(
    {"authorization", "cookie", "set-cookie", "x-api-key", "x-auth-token"},
)
_SENTRY_SENSITIVE_FIELDS = frozenset(
    {
        "password", "password_hash", "new_password", "old_password",
        "current_password", "token", "access_token", "refresh_token",
        "session", "session_id", "api_key", "apikey", "secret",
        "client_secret", "openai_api_key", "anthropic_api_key",
    },
)
_SENTRY_SCRUB_PLACEHOLDER = "[scrubbed]"


def _sentry_scrub_fields(value: Any, depth: int = 0) -> Any:
    """Walk ``value``, replacing sensitive field values with the placeholder.

    Bounded to depth 6 to defang cyclic structures from third-party SDKs.
    Mutates dicts in place and returns the same reference.
    """
    if depth > 6 or value is None:
        return value
    if isinstance(value, dict):
        for key in list(value.keys()):
            if isinstance(key, str) and key.lower() in _SENTRY_SENSITIVE_FIELDS:
                value[key] = _SENTRY_SCRUB_PLACEHOLDER
            else:
                value[key] = _sentry_scrub_fields(value[key], depth + 1)
        return value
    if isinstance(value, list):
        for i, item in enumerate(value):
            value[i] = _sentry_scrub_fields(item, depth + 1)
        return value
    return value


def _sentry_before_send(event: dict, _hint: dict) -> dict | None:
    """Strip credentials from outgoing Sentry events.

    Rules:
      - ``request.headers``: drop Authorization / Cookie / API-key headers.
      - ``request.cookies``: replace entirely.
      - ``request.query_string``: replace entirely (reset tokens land here).
      - ``request.data`` for ``/api/auth/*`` routes: replaced wholesale —
        password, email, reset tokens never leave the box.
      - ``request.data`` elsewhere: walked for credential-named fields.
      - ``extra`` / ``contexts`` / ``tags``: walked for credential fields.
      - ``user.email`` / ``user.username``: dropped; ``user.id`` retained
        so traces remain correlatable to an opaque user_id.
    """
    try:
        req = event.get("request") if isinstance(event, dict) else None
        if isinstance(req, dict):
            headers = req.get("headers")
            if isinstance(headers, dict):
                for key in list(headers.keys()):
                    if isinstance(key, str) and key.lower() in _SENTRY_SENSITIVE_HEADERS:
                        headers[key] = _SENTRY_SCRUB_PLACEHOLDER
            if "cookies" in req:
                req["cookies"] = _SENTRY_SCRUB_PLACEHOLDER
            if "query_string" in req and req["query_string"]:
                req["query_string"] = _SENTRY_SCRUB_PLACEHOLDER
            url = req.get("url")
            if isinstance(url, str) and "?" in url:
                req["url"] = url.split("?", 1)[0] + "?[query-scrubbed]"
            data = req.get("data")
            if data is not None:
                # Auth route bodies always carry a credential — wipe the
                # entire payload rather than per-field scrubbing.
                is_auth_route = isinstance(url, str) and "/api/auth/" in url
                if is_auth_route:
                    req["data"] = _SENTRY_SCRUB_PLACEHOLDER
                else:
                    req["data"] = _sentry_scrub_fields(data)
        for key in ("extra", "contexts", "tags"):
            section = event.get(key) if isinstance(event, dict) else None
            if section is not None:
                _sentry_scrub_fields(section)
        user = event.get("user") if isinstance(event, dict) else None
        if isinstance(user, dict):
            user.pop("email", None)
            user.pop("username", None)
            user.pop("ip_address", None)
    except Exception:  # noqa: BLE001 — instrumentation must never crash send
        return event
    return event


def _maybe_init_sentry() -> None:
    """Initialize Sentry when SENTRY_DSN_BACKEND (or legacy SENTRY_DSN) is set.

    No-op when neither env var is present — the app boots normally in dev and
    in environments where error tracking is not configured.

    SENTRY_DSN_BACKEND is the canonical name (separate from the frontend DSN).
    SENTRY_DSN is kept as a fallback so existing deployments that already set
    the generic name continue to work without any change.

    Sentry wraps the FastAPI app automatically once ``sentry_sdk.init`` runs
    before app creation. Errors raised inside route handlers are captured.

    The ``before_send`` hook (see ``_sentry_before_send``) scrubs Authorization
    / Cookie headers, password fields, query strings, and the entire request
    body for ``/api/auth/*`` routes. ``send_default_pii=False`` keeps Sentry
    from auto-attaching IPs and cookies; the explicit hook is belt-and-braces
    against credential leaks via ``request.data`` and ``extra``/``contexts``.
    """
    dsn = (
        os.environ.get("SENTRY_DSN_BACKEND", "").strip()
        or os.environ.get("SENTRY_DSN", "").strip()
    )
    if not dsn:
        return
    try:
        import sentry_sdk  # type: ignore
        from sentry_sdk.integrations.logging import LoggingIntegration  # type: ignore

        sentry_sdk.init(
            dsn=dsn,
            traces_sample_rate=float(os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "0.1")),
            send_default_pii=False,
            environment=os.environ.get("ENVIRONMENT", "development"),
            integrations=[
                LoggingIntegration(level=logging.INFO, event_level=logging.ERROR),
            ],
            before_send=_sentry_before_send,
        )
        logger.info("Sentry initialized")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Sentry init failed: %s", exc)


def _assert_production_safe() -> None:
    """Refuse to start in production when critical secrets are unset.

    Catches the common deploy footgun: pushing to prod with no
    INSPIRA_SESSION_SECRET set, so session cookies are signed with the
    hardcoded dev fallback and trivially forgeable. Also gates CORS and
    cookie Secure flag so the deploy can't go public with dev-safe
    defaults. A loud failure here is far safer than a quiet compromise.
    """
    env = os.environ.get("ENVIRONMENT", "development").lower()
    if env != "production":
        return
    problems: list[str] = []
    secret = os.environ.get("INSPIRA_SESSION_SECRET", "").strip()
    if not secret or secret == "inspira-dev-only-change-me":
        problems.append(
            "INSPIRA_SESSION_SECRET is empty or still the dev fallback. "
            "Generate with `python -c 'import secrets; print(secrets.token_urlsafe(48))'`.",
        )
    if not os.environ.get("INSPIRA_ALLOWED_ORIGINS", "").strip():
        problems.append(
            "INSPIRA_ALLOWED_ORIGINS must be set in production "
            "(e.g. 'https://app.example.com,https://www.example.com').",
        )
    cookie_secure = os.environ.get("INSPIRA_COOKIE_SECURE", "false").lower()
    if cookie_secure != "true":
        problems.append(
            "INSPIRA_COOKIE_SECURE must be 'true' in production so session "
            "cookies require HTTPS.",
        )
    if not os.environ.get("OPENAI_API_KEY", "").strip():
        problems.append(
            "OPENAI_API_KEY must be set in production. Inject via deploy "
            "platform secrets, not via a file-based .env.",
        )
    if problems:
        raise RuntimeError(
            "Refusing to start — production environment missing required "
            "configuration:\n  - " + "\n  - ".join(problems),
        )


def create_app(store: PlanningStudioStore | None = None, adapter: Any = None) -> FastAPI:
    """Build a configured FastAPI app.

    Normally called once at process start. Tests can call it with a fresh
    in-memory store + a fake adapter to exercise routes without hitting
    OpenAI. The returned app is ready for ``uvicorn.run(app, ...)``.
    """
    _assert_production_safe()
    _maybe_init_sentry()

    config = load_config()
    _store = store or PlanningStudioStore(config)
    _adapter_holder: dict[str, Any] = {"adapter": adapter}

    def _require_adapter() -> Any:
        if _adapter_holder["adapter"] is None:
            # Lazy import so non-LLM routes work without OPENAI_API_KEY set.
            from .agents import OpenAIPlanningInterviewer

            _adapter_holder["adapter"] = OpenAIPlanningInterviewer()
        return _adapter_holder["adapter"]

    # Claude adapter for the frontier model tier. Lazily constructed on
    # first frontier turn; None when ``ANTHROPIC_API_KEY`` is unset, which
    # lets dev environments keep working (the tier dispatcher in
    # ``agents/tiers.py:tier_to_adapter`` falls back to OpenAI).
    # Tests can inject a mock via ``app.state.claude_adapter = MagicMock()``.
    _claude_adapter_holder: dict[str, Any] = {"adapter": None, "checked": False}

    def _get_claude_adapter() -> Any:
        """Return the Claude adapter or ``None`` when the key is absent.

        Does NOT raise — ``None`` is a valid answer and is how we signal
        "Claude unavailable, fall back to OpenAI" to the tier dispatcher.
        The first miss logs a warning so operators see why frontier turns
        are falling back.
        """
        injected = getattr(app.state, "claude_adapter", None) if hasattr(app, "state") else None
        if injected is not None:
            return injected
        if _claude_adapter_holder["checked"]:
            return _claude_adapter_holder["adapter"]
        _claude_adapter_holder["checked"] = True
        if not os.environ.get("ANTHROPIC_API_KEY"):
            logger.warning(
                "ANTHROPIC_API_KEY not set — frontier turns will fall back to OpenAI. "
                "Set ANTHROPIC_API_KEY to unlock Claude Sonnet on the frontier tier."
            )
            return None
        try:
            from .agents import ClaudePlanningInterviewer

            _claude_adapter_holder["adapter"] = ClaudePlanningInterviewer()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Failed to construct Claude adapter; falling back to OpenAI: %s", exc,
            )
            _claude_adapter_holder["adapter"] = None
        return _claude_adapter_holder["adapter"]

    # Holders for the three auxiliary artifact-writer adapters (summary,
    # outline, deduper). Each is lazily constructed on first use so routes
    # that never touch them work without an OpenAI key. Tests can inject
    # a fake via ``app.state.plan_summary_adapter = MagicMock()`` etc.
    _plan_summary_holder: dict[str, Any] = {"adapter": None}
    _outline_holder: dict[str, Any] = {"adapter": None}
    _deduper_holder: dict[str, Any] = {"adapter": None}
    # Contradiction detector — fires on every decision save to see if
    # the new statement clashes with another user's earlier decision.
    # Cheap + fast (gpt-4o-mini, 5s timeout, fail-open).
    _contradiction_holder: dict[str, Any] = {"adapter": None}

    def _get_contradiction_adapter() -> Any:
        injected = getattr(app.state, "contradiction_adapter", None) if hasattr(app, "state") else None
        if injected is not None:
            return injected
        if _contradiction_holder["adapter"] is None:
            from .agents.contradiction import ContradictionAdapter

            _contradiction_holder["adapter"] = ContradictionAdapter()
        return _contradiction_holder["adapter"]

    def _maybe_check_contradiction_and_push(
        *,
        project_id: str,
        new_decision: dict[str, Any],
        user: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Run the LLM contradiction check and (if hit) push over WS.

        Returns a ``contradiction_hint`` dict for the HTTP response
        (client-side fallback path if WS isn't connected) or None.
        Never raises — LLM failures log + fail-open.
        """
        try:
            earlier = _store.list_decisions(
                project_id=project_id, user_id=user["user_id"],
            )
        except Exception:  # noqa: BLE001
            return None
        # Filter: only OTHER users' decisions, and only non-retracted.
        # (create_decision already dropped retracted from list_decisions.)
        other_users = [
            {
                "decision_id": d.get("decision_id"),
                "statement": d.get("statement") or "",
                "author_display_name": _author_display_name(d),
                "author_color": d.get("_author_color"),  # filled in below
                "created_at": d.get("created_at"),
                "proposed_by": d.get("proposed_by"),
            }
            for d in earlier
            if d.get("decision_id") != new_decision.get("decision_id")
            and d.get("proposed_by") != "planner"
            and _decision_author_user_id(d) != user["user_id"]
        ]
        if not other_users:
            return None
        adapter = _get_contradiction_adapter()
        result = adapter.check(
            new_statement=new_decision.get("statement") or "",
            earlier_decisions=other_users,
        )
        cid = result.get("contradicts_id")
        if not cid:
            return None
        conflicting = next(
            (d for d in other_users if d.get("decision_id") == cid),
            None,
        )
        if conflicting is None:
            return None
        hint = {
            "decision_id": new_decision.get("decision_id"),
            "conflicting_decision_id": conflicting.get("decision_id"),
            "conflicting_statement": conflicting.get("statement"),
            "conflicting_author_display_name": conflicting.get("author_display_name"),
            "conflicting_created_at": conflicting.get("created_at"),
            "reason": result.get("reason"),
        }
        # Fire-and-forget WS push. We don't block the HTTP response
        # on it — the hint field covers the WS-down case.
        try:
            import asyncio as _asyncio
            # ``get_running_loop()`` instead of ``get_event_loop()``:
            # the latter is deprecated in 3.12+ when called from async
            # code, and this whole route runs under uvicorn's loop.
            loop = _asyncio.get_running_loop()
            loop.create_task(
                realtime.push_to_user_sessions(
                    _store, project_id, user["user_id"],
                    {"t": "contradiction", **hint},
                ),
            )
        except RuntimeError:
            # No running loop — caller is in a sync context. Skip the
            # WS push; the contradiction hint in the response body is
            # the durable signal.
            pass
        except Exception:  # noqa: BLE001
            pass
        return hint

    def _author_display_name(decision: dict[str, Any]) -> str:
        """Best-effort author label for a decision. Decisions don't carry
        a user_id column directly on the row today, but they have
        ``proposed_by`` ('user', 'planner', or a free-form string). For
        the contradiction modal we just need something human-readable.
        """
        pb = (decision.get("proposed_by") or "").strip()
        if pb and pb != "user" and pb != "planner":
            return pb
        return "Someone"

    def _decision_author_user_id(decision: dict[str, Any]) -> str:
        """Try to recover who saved this decision. The store doesn't
        store user_id on decisions, so the best we have is
        ``confirmed_by_user_id`` or the implicit project owner. For
        contradiction detection we treat proposed_by='planner' as
        non-user (already filtered) and everything else as the project
        owner. Real per-user attribution would require adding a
        ``proposed_by_user_id`` column — left as a follow-up.
        """
        return str(decision.get("confirmed_by_user_id") or "")

    def _get_summary_adapter() -> Any:
        injected = getattr(app.state, "plan_summary_adapter", None) if hasattr(app, "state") else None
        if injected is not None:
            return injected
        if _plan_summary_holder["adapter"] is None:
            from .agents import PlanSummaryAdapter

            _plan_summary_holder["adapter"] = PlanSummaryAdapter()
        return _plan_summary_holder["adapter"]

    def _get_outline_adapter() -> Any:
        injected = getattr(app.state, "outline_adapter", None) if hasattr(app, "state") else None
        if injected is not None:
            return injected
        if _outline_holder["adapter"] is None:
            from .agents import OutlineAdapter

            _outline_holder["adapter"] = OutlineAdapter()
        return _outline_holder["adapter"]

    def _get_deduper_adapter() -> Any:
        injected = getattr(app.state, "deduper_adapter", None) if hasattr(app, "state") else None
        if injected is not None:
            return injected
        if _deduper_holder["adapter"] is None:
            from .agents import DeduperAdapter

            _deduper_holder["adapter"] = DeduperAdapter()
        return _deduper_holder["adapter"]

    # Code-scaffold adapter — paid-tier feature. Same lazy-construction
    # pattern as the three prose adapters above; tests inject a mock via
    # ``app.state.code_scaffold_adapter``.
    _code_scaffold_holder: dict[str, Any] = {"adapter": None}

    def _get_code_scaffold_adapter() -> Any:
        injected = getattr(app.state, "code_scaffold_adapter", None) if hasattr(app, "state") else None
        if injected is not None:
            return injected
        if _code_scaffold_holder["adapter"] is None:
            from .agents import CodeScaffoldAdapter

            _code_scaffold_holder["adapter"] = CodeScaffoldAdapter()
        return _code_scaffold_holder["adapter"]

    # Claude-backed code-scaffold adapter — activated for FRONTIER /
    # ENTERPRISE tiers from the artifact endpoint via tier dispatch.
    # Returns ``None`` when ``ANTHROPIC_API_KEY`` isn't set so the
    # caller's ``tier_to_adapter`` fallback path serves OpenAI rather
    # than 500'ing on every frontier turn. Tests inject a mock via
    # ``app.state.claude_code_scaffold_adapter``.
    _claude_code_scaffold_holder: dict[str, Any] = {"adapter": None, "tried": False}

    def _get_claude_code_scaffold_adapter() -> Any | None:
        injected = (
            getattr(app.state, "claude_code_scaffold_adapter", None)
            if hasattr(app, "state")
            else None
        )
        if injected is not None:
            return injected
        if _claude_code_scaffold_holder["adapter"] is not None:
            return _claude_code_scaffold_holder["adapter"]
        if _claude_code_scaffold_holder["tried"]:
            return None
        _claude_code_scaffold_holder["tried"] = True
        try:
            from .agents import ClaudeCodeScaffoldAdapter

            _claude_code_scaffold_holder["adapter"] = ClaudeCodeScaffoldAdapter()
        except RuntimeError as exc:
            # Most common: ANTHROPIC_API_KEY not set in this environment.
            # The artifact endpoint's tier-dispatch will fall through to
            # the OpenAI scaffold adapter when this returns None.
            logger.info(
                "Claude code-scaffold adapter unavailable (%s); FRONTIER/"
                "ENTERPRISE artifact requests will fall back to OpenAI.",
                exc,
            )
            return None
        return _claude_code_scaffold_holder["adapter"]

    # Auto-link adapter. Lives in the `agents` package but is NOT
    # re-exported through `__init__` because its prompt/schema is
    # small, purpose-built, and nothing else in the service needs to
    # touch it. Tests inject fakes via
    # `client.app.state.auto_link_adapter`.
    _auto_link_holder: dict[str, Any] = {"adapter": None}

    def _get_auto_link_adapter() -> Any:
        injected = getattr(app.state, "auto_link_adapter", None) if hasattr(app, "state") else None
        if injected is not None:
            return injected
        if _auto_link_holder["adapter"] is None:
            from .agents.auto_link import AutoLinkAdapter

            _auto_link_holder["adapter"] = AutoLinkAdapter()
        return _auto_link_holder["adapter"]

    # ---- Trial-ending email sweeper -------------------------------------
    #
    # Fires the `trial_ending` email ~3 days before `trial_ends_at`, once
    # per trial (guarded by `users.trial_ending_emailed_at`). Disabled
    # by default — operators opt in via INSPIRA_TRIAL_ENDING_SWEEPER=1.
    _TRIAL_SWEEP_INTERVAL_S = 15 * 60  # 15 minutes

    def _trial_ending_run_once() -> int:
        """One iteration of the trial-ending sweeper. Returns send count."""
        import datetime as _dt

        # ``utcnow()`` is deprecated in 3.12+ and returns naive datetime;
        # downstream isoformat() then produces strings without ``+00:00``
        # offset that compare incorrectly against tz-aware columns.
        now = _dt.datetime.now(_dt.timezone.utc)
        horizon = now + _dt.timedelta(days=3)
        # The 2d lower bound keeps us roughly within a 24h "about-3d"
        # window so a user that landed mid-slot still gets the email.
        lower = now + _dt.timedelta(days=2)
        try:
            rows = _store.list_users_for_trial_ending_sweep(
                now_iso=lower.isoformat(timespec="seconds"),
                horizon_iso=horizon.isoformat(timespec="seconds"),
            )
        except Exception:  # noqa: BLE001 — DB blip shouldn't kill sweeper
            logger.exception("trial sweeper: query failed")
            return 0
        # Local imports keep this sweeper decoupled from the rest of
        # the app-construction closure where .billing is imported later.
        from .billing import free_plan, get_plan

        billing_base = next(
            (
                os.environ.get(key, "").strip().rstrip("/")
                for key in ("INSPIRA_APP_BASE_URL", "INSPIRA_FRONTEND_URL")
                if os.environ.get(key, "").strip()
            ),
            "http://localhost:5173",
        )
        sent = 0
        for row in rows:
            try:
                from .mail import get_email_sender  # local import to avoid app-construction-closure cycle

                sender = get_email_sender()
                plan_title = (
                    get_plan(row.get("plan") or "pro") or free_plan()
                ).title
                sender.send(
                    to_email=row["email"],
                    template_id="trial_ending",
                    context={
                        "display_name": row.get("display_name")
                        or row["email"].split("@", 1)[0],
                        "plan_name": plan_title,
                        "trial_ends_at_human": row.get("trial_ends_at") or "",
                        "upgrade_url": f"{billing_base}/billing",
                        "stay_free_url": f"{billing_base}/billing",
                    },
                )
                _store.mark_trial_ending_emailed(row["user_id"])
                sent += 1
            except Exception:  # noqa: BLE001 — one bad row shouldn't halt sweep
                logger.exception(
                    "trial sweeper: send failed for user_id=%s",
                    row.get("user_id"),
                )
        if sent:
            logger.info("trial sweeper: sent=%d", sent)
        return sent

    def _trial_ending_sweeper_enabled() -> bool:
        flag = os.environ.get("INSPIRA_TRIAL_ENDING_SWEEPER", "").strip().lower()
        return flag in {"1", "true", "yes"}

    async def _trial_ending_sweep_loop(stop_event: asyncio.Event) -> None:
        logger.info(
            "trial sweeper: starting, interval=%ds", _TRIAL_SWEEP_INTERVAL_S,
        )
        while not stop_event.is_set():
            try:
                await asyncio.to_thread(_trial_ending_run_once)
            except Exception:  # noqa: BLE001 — keep loop alive
                logger.exception("trial sweeper: iteration failed")
            try:
                await asyncio.wait_for(
                    stop_event.wait(), timeout=_TRIAL_SWEEP_INTERVAL_S,
                )
            except asyncio.TimeoutError:
                continue

    @asynccontextmanager
    async def lifespan(_app: FastAPI):  # noqa: ARG001
        logger.info("Inspira service starting on %s:%s", config.host, config.port)
        # Trial-ending email sweeper. Opt-in via
        # INSPIRA_TRIAL_ENDING_SWEEPER=1.
        trial_sweep_task: asyncio.Task[None] | None = None
        trial_sweep_stop: asyncio.Event | None = None
        if _trial_ending_sweeper_enabled():
            trial_sweep_stop = asyncio.Event()
            trial_sweep_task = asyncio.create_task(
                _trial_ending_sweep_loop(trial_sweep_stop),
                name="trial-ending-sweeper",
            )

        # v4 W2 C3: connector-sync polling loop. Opt-in via
        # INSPIRA_CONNECTOR_SYNC=1. The loop's first action is the
        # orphan reconciler (catches Fly-restart orphans), then a
        # small jittered delay before the first cycle. Graceful
        # shutdown: stop_event is set in the finally below; the
        # loop exits cleanly between ticks. An in-flight sync_workspace
        # call gets ~10s to finish; any sync still 'running' at
        # hard-kill is reconciled on the next process startup.
        from .jobs.sync_scheduler import (  # noqa: PLC0415
            connector_sync_loop,
            is_scheduler_enabled,
        )

        sync_task: asyncio.Task[None] | None = None
        sync_stop: asyncio.Event | None = None
        if is_scheduler_enabled():
            sync_stop = asyncio.Event()
            sync_task = asyncio.create_task(
                connector_sync_loop(_store, sync_stop),
                name="connector-sync-scheduler",
            )

        try:
            yield
        finally:
            logger.info("Inspira service stopping")
            if trial_sweep_task is not None and trial_sweep_stop is not None:
                trial_sweep_stop.set()
                try:
                    await asyncio.wait_for(trial_sweep_task, timeout=5.0)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    trial_sweep_task.cancel()
            if sync_task is not None and sync_stop is not None:
                sync_stop.set()
                try:
                    # 10s shutdown window — enough for one in-flight
                    # sync_workspace to finish at v2 throughput
                    # (max ~30s for a slow GitHub response, but the
                    # client side has its own timeouts well under
                    # the Fly graceful-shutdown ceiling).
                    await asyncio.wait_for(sync_task, timeout=10.0)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    sync_task.cancel()
            # Close the process-wide pg_notify connection opened in
            # realtime._NotifyChannel. Safe to call even if the channel
            # was never used (SQLite-only dev).
            try:
                await realtime.shutdown_notify_channel()
            except Exception:  # noqa: BLE001
                logger.warning("realtime notify channel shutdown failed", exc_info=True)

    app = FastAPI(
        title="Inspira backend",
        description=(
            "HTTP API for the Inspira planning product (repo codename: "
            "planning-studio). Routes preserve the shapes the web client "
            "already expects."
        ),
        version="0.2.0",
        lifespan=lifespan,
    )

    # CORS — two modes:
    # - ``INSPIRA_ALLOWED_ORIGINS`` set → explicit allowlist (production path).
    #   Optionally combine with ``INSPIRA_ALLOWED_ORIGIN_REGEX`` to match a
    #   family of origins (e.g. Cloudflare Pages preview URLs like
    #   ``https://<hash>.inspira-frontend.pages.dev``) without listing every
    #   hash. The regex is OR'd with the explicit allowlist inside
    #   Starlette's CORSMiddleware.
    # - Not set in dev → regex match any localhost / 127.0.0.1 / LAN-IP
    #   origin on any port. Lets the Vite dev server bind to whatever
    #   network interface (192.168.1.50:4175, etc.) and still carry credentials
    #   without a per-machine env var. Production never reaches this branch —
    #   ``_assert_production_safe`` raises first.
    #
    # Header policy: explicit allowlist (Content-Type, Authorization,
    # X-Requested-With) instead of "*" so credentialed requests stay
    # spec-compliant and we don't accidentally accept arbitrary client
    # headers. ``allow_credentials=True`` is required so the session cookie
    # flows on cross-origin requests from the Pages-hosted SPA to the
    # Fly-hosted API. ``max_age=3600`` lets browsers cache the preflight
    # for an hour, cutting OPTIONS chatter on chatty endpoints (canvas,
    # SSE handshakes).
    _cors_allow_methods = ["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"]
    _cors_allow_headers = [
        "Content-Type",
        "Authorization",
        "X-Requested-With",
        # Injected by app/src/lib/httpClient.ts on every workspace-scoped
        # request. Without this in the allow-list, browser preflights
        # fail and every cross-origin GET that needs workspace context
        # (inbox, connectors, orchestrator polling, …) is blocked.
        "X-Workspace-Id",
    ]
    _cors_max_age = 3600

    allowed_origins_raw = os.environ.get("INSPIRA_ALLOWED_ORIGINS", "").strip()
    allowed_origin_regex = (
        os.environ.get("INSPIRA_ALLOWED_ORIGIN_REGEX", "").strip() or None
    )
    if allowed_origins_raw:
        origins = [o.strip() for o in allowed_origins_raw.split(",") if o.strip()]
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_origin_regex=allowed_origin_regex,
            allow_credentials=True,
            allow_methods=_cors_allow_methods,
            allow_headers=_cors_allow_headers,
            max_age=_cors_max_age,
        )
    else:
        # Dev fallback — regex is specific enough that third-party sites
        # can't satisfy it, so allow_credentials=True is safe. An explicit
        # INSPIRA_ALLOWED_ORIGIN_REGEX from the deployer wins over the
        # built-in pattern so dev environments can opt into preview URLs
        # too without also setting INSPIRA_ALLOWED_ORIGINS.
        dev_origin_pattern = allowed_origin_regex or (
            r"^https?://"
            r"("
            r"localhost"
            r"|127\.0\.0\.1"
            r"|10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
            r"|192\.168\.\d{1,3}\.\d{1,3}"
            r"|172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}"
            r")"
            r"(:\d+)?$"
        )
        app.add_middleware(
            CORSMiddleware,
            allow_origin_regex=dev_origin_pattern,
            allow_credentials=True,
            allow_methods=_cors_allow_methods,
            allow_headers=_cors_allow_headers,
            max_age=_cors_max_age,
        )

    # Security headers — applied to every response. Registered AFTER CORS
    # so CORS preflight headers are generated first, and BEFORE slowapi so
    # 429 rate-limit responses also carry the full security suite.
    # Rationale for each header: docs/ops/security-headers.md.
    _is_production = (
        os.environ.get("ENVIRONMENT", "development").lower() == "production"
    )
    app.add_middleware(
        _SecurityHeadersMiddleware, is_production=_is_production,
    )
    # Request-ID middleware — attaches X-Request-ID to every response so
    # the client and the 500 handler share the same correlation id that
    # fly logs and Sentry record. Reads ``Fly-Request-Id`` when present so
    # the edge's id survives into the body; otherwise mints a fresh uuid.
    app.add_middleware(_RequestIdMiddleware)

    # OpenAI circuit-breaker open — return a structured 503 with a
    # Retry-After header so the frontend can distinguish "service
    # degraded, retry shortly" from a real 500. Raised by the per-endpoint
    # breaker plumbing in agents/openai_adapter.py whenever fail_max
    # consecutive transient failures (timeout, 5xx, rate-limit) accumulate
    # against one endpoint group; remains open for reset_timeout seconds
    # before the breaker enters HALF_OPEN and probes again.
    from .agents.openai_adapter import OpenAICircuitOpenError  # noqa: PLC0415

    @app.exception_handler(OpenAICircuitOpenError)
    async def _openai_circuit_open(_request: Request, exc: OpenAICircuitOpenError):  # noqa: ARG001
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "error": "openai_unavailable",
                "endpoint": exc.breaker_key,
                "retry_after_s": exc.retry_after_s,
            },
            headers={"Retry-After": str(exc.retry_after_s)},
        )

    # Global 500 handler — FastAPI/Starlette's default for an unhandled
    # exception is a plain-text ``Internal Server Error`` body. That's
    # unparseable for the frontend's generic error toast and makes on-call
    # debugging harder because the reference id the user sees never
    # matches a log line. Replace with a stable JSON shape.
    @app.exception_handler(Exception)
    async def _generic_500(request: Request, exc: Exception):  # noqa: ARG001
        # Never intercept HTTPException — FastAPI already owns that flow
        # (the framework registers its own handler and it fires before
        # this one; this check is defensive in case that ever changes).
        if isinstance(exc, HTTPException):
            raise exc
        reference = (
            getattr(request.state, "request_id", None)
            or request.headers.get("x-request-id")
            or ""
        )
        if not reference:
            import uuid as _uuid  # noqa: PLC0415

            reference = _uuid.uuid4().hex[:16]
        # Log with the same reference so support can grep fly logs.
        logger.exception(
            "unhandled exception [reference=%s] path=%s method=%s",
            reference,
            request.url.path,
            request.method,
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": "internal_server_error",
                "reference": reference,
            },
            headers={"X-Request-ID": reference},
        )

    # Rate limiting — per-IP for anonymous/cookie traffic, per-PAT for
    # bearer-authed traffic.  The ``bearer_rate_limit_key`` key function
    # (see bearer_auth.py) routes each request to the right bucket so
    # a rogue integration with a stolen PAT can't consume a user's
    # browser session budget, and vice versa.  Per-user LLM token
    # budgets live in a separate check inside LLM-hitting routes.
    try:
        from slowapi import Limiter
        from slowapi.errors import RateLimitExceeded
        from slowapi.middleware import SlowAPIMiddleware
        from slowapi.util import get_remote_address  # noqa: F401 -- kept for later per-route use

        from .bearer_auth import bearer_rate_limit_key  # noqa: PLC0415

        limiter = Limiter(
            key_func=bearer_rate_limit_key,
            default_limits=[os.environ.get("INSPIRA_RATE_LIMIT", "120/minute")],
        )
        app.state.limiter = limiter
        app.add_middleware(SlowAPIMiddleware)

        @app.exception_handler(RateLimitExceeded)
        async def _rate_limited(_request: Request, exc: RateLimitExceeded):  # noqa: ARG001
            # slowapi exposes the parsed window via ``exc.limit`` — derive
            # a ``Retry-After`` hint from it so clients and proxies can
            # back off without having to parse the detail string.
            retry_after = 60  # safe default — the tightest auth limit is /minute
            try:
                limit_obj = getattr(exc, "limit", None)
                limit_detail = getattr(limit_obj, "limit", None)
                if limit_detail is not None:
                    # ``limits.RateLimitItem.get_expiry()`` returns seconds
                    # until the window ends. Fall through silently if the
                    # slowapi version in use doesn't expose it.
                    get_expiry = getattr(limit_detail, "get_expiry", None)
                    if callable(get_expiry):
                        retry_after = int(get_expiry())
            except Exception:  # noqa: BLE001 -- best-effort hint only
                pass
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={
                    "error": "rate_limited",
                    "detail": str(exc.detail),
                    "retry_after_seconds": retry_after,
                },
                headers={"Retry-After": str(retry_after)},
            )
    except ImportError:  # slowapi is optional at import time for tests
        logger.warning("slowapi not installed; rate limiting disabled")

    # Auth — sessions are set/read via itsdangerous-signed cookies. A
    # missing/invalid session resolves to a bootstrap "system" user so the
    # existing single-tenant UI keeps working during the transition.
    from .auth import router as auth_router, current_user_dependency, SESSION_COOKIE_NAME  # noqa: PLC0415
    from .bearer_auth import try_resolve_bearer_user  # noqa: PLC0415

    app.include_router(auth_router, prefix="/api/auth", tags=["auth"])
    _cookie_user = current_user_dependency(_store)

    # Wrap the cookie dependency so every v2 route accepts EITHER a
    # valid PAT bearer header OR a signed session cookie.  The bearer
    # path is checked FIRST so a request carrying both (say, a curl
    # invocation with --cookie-jar populated) always authenticates as
    # the PAT owner, not whichever user the cookie happens to point at.
    # When no bearer is present we fall through to the cookie path,
    # which also handles the anon-on-first-contact minting.
    from fastapi import Cookie as _Cookie  # noqa: PLC0415

    def _current_user(
        request: Request,
        response: Response,
        inspira_session: str | None = _Cookie(  # type: ignore[assignment]
            default=None, alias=SESSION_COOKIE_NAME,
        ),
    ) -> dict[str, Any]:
        bearer_user = try_resolve_bearer_user(request, _store)
        raw = bearer_user if bearer_user is not None else _cookie_user(
            request=request, response=response, inspira_session=inspira_session,
        )
        # Derive ``is_system`` server-side from the authenticated user_id so
        # every downstream ``user.get("is_system")`` check operates on a
        # trusted value — never on a caller-supplied key.  The raw DB dict
        # has no ``is_system`` column; computing it here closes the gap
        # where a caller could forge the flag by injecting a crafted payload.
        from .auth import SYSTEM_USER_ID as _SYSTEM_UID, _is_anon_user_id  # noqa: PLC0415
        uid = raw.get("user_id") or ""
        is_system = uid == _SYSTEM_UID or _is_anon_user_id(uid)
        return {**raw, "is_system": is_system}

    # v4 B2B pivot: workspace surface (W1 F1). The dependency
    # factory + router both close over the request-scoped store and
    # _current_user, so they must be wired here rather than at
    # module level. The router prefix is set inside the factory so
    # we don't double-prefix on include.
    from .workspaces.dependencies import (  # noqa: PLC0415
        make_current_workspace_member,
    )
    from .workspaces.router import make_workspaces_router  # noqa: PLC0415

    current_workspace_member = make_current_workspace_member(
        _store, _current_user
    )
    app.include_router(
        make_workspaces_router(
            _store, _current_user, current_workspace_member
        )
    )

    # v4 W2 C1+C2: connectors surface. C1 ships the GET state
    # endpoint (drives the Connectors page B1.3 on mount). C2 adds
    # the five GitHub App OAuth + sync endpoints under /github/*.
    # Linear + CSV/JSON paste-in land in C5 alongside their own
    # per-provider modules. The factory takes _current_user too
    # because the OAuth /callback path needs the session-resolved
    # user_id without going through current_workspace_member (no
    # X-Workspace-Id header on the redirect from GitHub).
    from .connectors.router import make_connectors_router  # noqa: PLC0415

    app.include_router(
        make_connectors_router(
            _store, _current_user, current_workspace_member
        )
    )

    # W3 (F6 + F7-REVISED): orchestrator surface — autonomous canvas
    # generation. POST /prioritize → ROI scorer; POST /run → spawns
    # one sub-agent per top-N theme; SSE /runs/{id}/events for live
    # state. Gated by INSPIRA_ORCHESTRATOR_ENABLED in prod (defaults
    # on inside pytest).
    #
    # Same factory also returns the /api/v2/projects/* surface
    # the canvas-review chrome depends on (closes #115 + #116):
    # POST /promote-from-cluster spawns an orchestrator run for one
    # cluster + waits for the canvas; GET /{project_id}/events proxies
    # the project-keyed SSE stream by looking up orchestrator_run_id
    # from v2_projects.metadata_json.
    from .orchestrator_router import make_orchestrator_router  # noqa: PLC0415

    _orchestrator_router, _projects_router = make_orchestrator_router(
        _store, _current_user, current_workspace_member
    )
    app.include_router(_orchestrator_router)
    app.include_router(_projects_router)

    # W2: comment cascade — text-select on a decision → comment →
    # gpt-5-mini regenerates affected scope with diff badges. Three
    # endpoints under /api/v2/projects/{project_id}/regenerate-cascade*.
    from .cascade_router import make_cascade_router  # noqa: PLC0415

    app.include_router(
        make_cascade_router(
            _store, _current_user, current_workspace_member
        )
    )

    # W2: Send-to-Linear / Send-to-GitHub export modals.
    # Two POST endpoints under /api/v2/projects/{id}/export/{provider}.
    # Reuses the connectors/* layer for credentials + provider clients.
    from .exports.router import make_exports_router  # noqa: PLC0415

    app.include_router(
        make_exports_router(
            _store, _current_user, current_workspace_member
        )
    )

    # Wave F.4 (#147): inline IDE-style comments on generated scaffold
    # code. Three endpoints under
    # /api/v2/projects/{project_id}/artifact/comments — POST/GET/PATCH.
    # Body models live at module scope in artifact_comments_router so
    # the future-annotations resolution works (api.py imports them
    # transitively via the factory).
    from .artifact_comments_router import (  # noqa: PLC0415
        make_artifact_comments_router,
    )

    app.include_router(
        make_artifact_comments_router(_store, current_workspace_member)
    )

    # Tighten per-route rate limits on auth mutation routes using the
    # same limiter instance registered above on app.state. Follows the
    # identical pattern as /api/client-errors and /api/v2/search. The
    # endpoint functions already declare `request: Request` in their
    # signatures so slowapi's decorator is satisfied. No-ops when slowapi
    # is absent because app.state.limiter won't be set in that case.
    #
    # Limits (audit hardening — see also the bulk wiring at the bottom of
    # create_app for the v2 sensitive routes):
    #   /login             — 10/minute (brute-force / credential-stuffing
    #                        defence on a short window; per-IP because the
    #                        attacker doesn't yet have a session).
    #   /signup            — 5/hour    (bot-account spam control; new
    #                        accounts are expensive — argon2 hash + welcome
    #                        email — so per-hour makes more sense than per-
    #                        minute, and 5/hour from one IP is well above
    #                        any organic signup pattern).
    #   /forgot-password   — 5/hour    (per-IP burst cap; the per-email
    #                        invalidation cap in the store is the real
    #                        defence, this prevents enumeration scans from
    #                        a single host).
    #   /reset-password    — 10/minute (token-redemption window; the token
    #                        itself is single-use and short-lived, this
    #                        just protects against brute-forcing the token).
    _auth_limiter = getattr(app.state, "limiter", None)
    if _auth_limiter is not None:
        # Swap the endpoint callable with the slowapi wrapper, then
        # rebuild the route's dependant + ASGI app so FastAPI actually
        # dispatches THROUGH the wrapper. Assigning ``_route.endpoint``
        # alone is insufficient because ``APIRoute.__init__`` captures
        # both ``self.dependant`` (from the original callable) and
        # ``self.app`` (a closure over the unwrapped handler) at route
        # construction time — a subsequent assignment to ``.endpoint``
        # leaves both stale and slowapi's decorator never runs.
        from fastapi.dependencies.utils import (  # noqa: PLC0415
            get_dependant, get_flat_dependant,
        )
        from fastapi.routing import request_response  # noqa: PLC0415

        _route_rates: dict[str, Any] = {
            "/api/auth/login": _auth_limiter.limit("10/minute"),
            "/api/auth/signup": _auth_limiter.limit("5/hour"),
            "/api/auth/forgot-password": _auth_limiter.limit("5/hour"),
            "/api/auth/reset-password": _auth_limiter.limit("10/minute"),
        }
        for _route in app.routes:
            _route_path = getattr(_route, "path", None)
            if _route_path in _route_rates:
                _wrapped = _route_rates[_route_path](
                    _route.endpoint,  # type: ignore[union-attr]
                )
                _route.endpoint = _wrapped  # type: ignore[union-attr]
                # Rebuild ``.dependant`` from the wrapped callable so FastAPI
                # injects ``Request`` / body / cookies into the wrapper
                # (slowapi needs ``request`` or it raises).
                _route.dependant = get_dependant(  # type: ignore[union-attr]
                    path=_route.path_format,  # type: ignore[union-attr]
                    call=_wrapped,
                    scope="function",
                )
                _route._flat_dependant = get_flat_dependant(  # type: ignore[attr-defined]
                    _route.dependant,  # type: ignore[union-attr]
                )
                # Rebuild the ASGI handler so the new dependant is used.
                _route.app = request_response(  # type: ignore[union-attr]
                    _route.get_route_handler(),  # type: ignore[union-attr]
                )

    # -----------------------------------------------------------------------
    # Health
    # -----------------------------------------------------------------------

    @app.get("/api/health", tags=["meta"])
    def health() -> dict[str, Any]:
        # Trimmed to `{service, status, generated_at}` — the full path
        # detail in store.health() includes absolute filesystem paths,
        # which is reconnaissance for an attacker. Keep the endpoint
        # unauthenticated but don't leak internals.
        #
        # When startup column-retrofit migrations failed, surface them
        # so on-call sees the degraded state without 500-ing the probe.
        full = _store.health()
        failed = full.get("failed_migrations") or []
        payload: dict[str, Any] = {
            "service": "planning-studio",
            "status": "degraded" if failed else full.get("status", "ok"),
            "generated_at": full.get("generated_at"),
        }
        if failed:
            payload["failed_migrations"] = list(failed)
        return payload

    # -----------------------------------------------------------------------
    # Public status (richer than /api/health)
    # -----------------------------------------------------------------------
    # Backs the public /status page. Unauthenticated. Returns a coarse
    # "ok|degraded|down" rollup plus per-component checks (db / openai /
    # stripe / version / generated_at). Cached for 30s so a status-page
    # refresh storm can't hammer the DB.
    #
    # Why "unknown" for openai/stripe? Probing those vendors per status
    # hit means an outbound call (and dollars) on every refresh. We only
    # surface "fail" via separate background signals; the public page
    # treats "unknown" as a non-blocking neutral.

    _STATUS_CACHE_TTL_SECONDS = 30.0
    _STATUS_DB_DEGRADED_MS = 500
    _status_cache: dict[str, Any] = {
        "expires_at": 0.0,
        "payload": None,
    }

    def _resolve_build_version() -> str:
        # Operators set INSPIRA_GIT_SHA (preferred), INSPIRA_BUILD_VERSION,
        # GIT_SHA, or BUILD_ID at deploy time. We fall back to "dev" so
        # the field is always present rather than null. INSPIRA_GIT_SHA is
        # the canonical var the Dockerfile wires up via `--build-arg`.
        for var in (
            "INSPIRA_GIT_SHA",
            "INSPIRA_BUILD_VERSION",
            "GIT_SHA",
            "BUILD_ID",
        ):
            value = os.environ.get(var)
            if value:
                return value.strip()[:64]
        return "dev"

    def _probe_db() -> tuple[str, int | None]:
        # SELECT 1 round-trip. Reuses the store's connection so we honour
        # whichever dialect (sqlite / postgres) is configured. Errors are
        # logged but not surfaced to the caller — the public status page
        # only needs ok|fail, not driver-level reconnaissance.
        start = time.perf_counter()
        try:
            with _store._connect() as connection:  # noqa: SLF001
                cur = connection.execute("SELECT 1")
                try:
                    cur.fetchone()
                except Exception:  # noqa: BLE001
                    pass
        except Exception as exc:  # noqa: BLE001
            logging.getLogger("planning_studio.status").warning(
                "status db probe failed: %s", exc,
            )
            return "fail", None
        latency_ms = int((time.perf_counter() - start) * 1000)
        return "ok", latency_ms

    @app.get("/api/status", tags=["meta"])
    def public_status() -> dict[str, Any]:
        now = time.monotonic()
        cached = _status_cache.get("payload")
        if cached is not None and now < float(_status_cache.get("expires_at", 0.0)):
            return cached

        db_status, db_latency_ms = _probe_db()
        # OpenAI / Stripe are not actively probed — see the comment on
        # the cache block above. Set them to "unknown" so the schema is
        # stable; a future background job can flip them to "ok" or "fail".
        openai_status = "unknown"
        stripe_status = "unknown"

        if db_status == "fail":
            overall = "down"
        elif db_latency_ms is not None and db_latency_ms > _STATUS_DB_DEGRADED_MS:
            overall = "degraded"
        else:
            overall = "ok"

        payload: dict[str, Any] = {
            "status": overall,
            "checks": {
                "db": db_status,
                "openai": openai_status,
                "stripe": stripe_status,
            },
            "version": _resolve_build_version(),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        if db_latency_ms is not None:
            payload["checks"]["db_latency_ms"] = db_latency_ms

        _status_cache["payload"] = payload
        _status_cache["expires_at"] = now + _STATUS_CACHE_TTL_SECONDS
        return payload

    # -----------------------------------------------------------------------
    # Client-error telemetry
    # -----------------------------------------------------------------------
    # The React ErrorBoundary POSTs here when it catches a render error.
    # No auth required — the boundary fires before the user may have a
    # session, and the payload carries no secrets. Rate-limited to 30/min
    # per IP via the per-route slowapi decorator (falls back to the global
    # 120/min default if slowapi isn't installed). We only log; no DB write.

    _client_error_logger = logging.getLogger("planning_studio.client_errors")

    # Per-route 30/min/IP limit — applied via decorator when slowapi is
    # available (captured from the outer try block). Falls back gracefully
    # to the global 120/min SlowAPIMiddleware default if slowapi is absent.
    _ce_limiter = getattr(app.state, "limiter", None)

    async def _client_errors_handler(
        body: ClientErrorBody, request: Request,  # noqa: ARG001
    ) -> Response:
        # Log at WARNING so it surfaces in default log configs without
        # needing DEBUG noise. Truncate to keep log lines sane. Component
        # stack moved from DEBUG to WARNING temporarily to debug a prod
        # React error #300 (rules-of-hooks violation post-signin).
        _client_error_logger.warning(
            "client render error | href=%s | message=%s",
            (body.href or "")[:200],
            (body.message or "")[:500],
        )
        if body.stack:
            _client_error_logger.warning(
                "client error stack:\n%s", body.stack[:3000],
            )
        if body.componentStack:
            _client_error_logger.warning(
                "client component stack:\n%s", body.componentStack[:3000],
            )
        return Response(status_code=204)

    if _ce_limiter is not None:
        # Wrap with a 30/min per-IP limit stricter than the global default.
        _client_errors_handler = _ce_limiter.limit("30/minute")(
            _client_errors_handler,
        )

    app.add_api_route(
        "/api/client-errors",
        _client_errors_handler,
        methods=["POST"],
        tags=["meta"],
        status_code=204,
    )

    # -----------------------------------------------------------------------
    # CSP violation reports
    # -----------------------------------------------------------------------
    # Browsers POST here when a Content-Security-Policy directive is
    # violated (the `report-uri` listed in _CSP_DIRECTIVES). We log only;
    # no DB write. The stream is read by the on-call rotation while CSP
    # is in report-only mode (INSPIRA_CSP_REPORT_ONLY=true) — every
    # report represents either a real injection attempt OR an inline
    # script we forgot to nonce. Once a few days of reports come back
    # clean, flip the env var to false and start enforcing.
    #
    # Request body shape: browsers send either the legacy
    # ``application/csp-report`` envelope ({"csp-report": {...}}) or the
    # newer ``application/reports+json`` array. We accept both, parse
    # minimally with json, and log the most useful fields rather than
    # binding a strict pydantic schema (the spec is loose and varies by
    # vendor; over-validating drops reports we want to see).
    #
    # No auth: the violation can fire before the user is signed in, and
    # the payload carries no secrets. Rate-limited to 60/min per IP via
    # slowapi to keep a malicious page from flooding the log; that's 1/sec
    # which is comfortable for real traffic but caps a runaway origin.

    _csp_report_logger = logging.getLogger("planning_studio.csp_reports")
    _csp_report_limiter = getattr(app.state, "limiter", None)

    async def _csp_report_handler(request: Request) -> Response:
        # Read raw bytes — both 'application/csp-report' and
        # 'application/reports+json' are JSON, but some browsers send
        # neither Content-Type, so don't depend on FastAPI parsing.
        raw = await request.body()
        if not raw:
            return Response(status_code=204)
        # Cap at 16 KiB. A real CSP report is well under 1 KiB; anything
        # bigger is almost certainly noise or an attempt to fill the log.
        if len(raw) > 16 * 1024:
            _csp_report_logger.warning(
                "csp report dropped: oversize body (%d bytes)", len(raw),
            )
            return Response(status_code=204)
        try:
            import json as _json  # noqa: PLC0415

            payload = _json.loads(raw.decode("utf-8", errors="replace"))
        except (ValueError, UnicodeDecodeError):
            _csp_report_logger.warning(
                "csp report dropped: invalid JSON body (%d bytes)", len(raw),
            )
            return Response(status_code=204)

        # Normalise both envelope shapes into a list[dict]. The legacy
        # 'application/csp-report' wraps a single report under the
        # "csp-report" key; the modern 'application/reports+json' is a
        # plain array, with each entry's payload under "body".
        reports: list[dict[str, Any]] = []
        if isinstance(payload, dict):
            inner = payload.get("csp-report")
            if isinstance(inner, dict):
                reports.append(inner)
        elif isinstance(payload, list):
            for entry in payload:
                if isinstance(entry, dict):
                    body = entry.get("body")
                    if isinstance(body, dict):
                        reports.append(body)
                    else:
                        reports.append(entry)

        # Log the high-signal fields. Anything not in this short list is
        # noise for triage — the full payload is recoverable from the
        # raw log line below if a deep dive is needed.
        for r in reports[:5]:  # also cap at 5/report-batch defensively
            _csp_report_logger.warning(
                "csp violation | doc=%s | violated=%s | blocked=%s | "
                "directive=%s | sample=%s",
                str(r.get("document-uri") or r.get("documentURL") or "")[:200],
                str(r.get("violated-directive") or r.get("effectiveDirective") or "")[:120],
                str(r.get("blocked-uri") or r.get("blockedURL") or "")[:200],
                str(r.get("effective-directive") or r.get("effectiveDirective") or "")[:120],
                str(r.get("script-sample") or r.get("sample") or "")[:200],
            )
        return Response(status_code=204)

    if _csp_report_limiter is not None:
        # 60/min/IP — 1/sec sustained, plenty for real reports while
        # capping a malicious origin trying to fill the log.
        _csp_report_handler = _csp_report_limiter.limit("60/minute")(
            _csp_report_handler,
        )

    app.add_api_route(
        "/api/csp-report",
        _csp_report_handler,
        methods=["POST"],
        tags=["meta"],
        status_code=204,
    )

    # -----------------------------------------------------------------------
    # Admin metrics
    # -----------------------------------------------------------------------
    # ``GET /api/admin/metrics`` returns the snapshot of the in-memory
    # MetricsCollector. This is the data source for the public status
    # page (via a scheduled job that writes the incidents.json) and for
    # future operator dashboards. Today it's gated by a soft email
    # check — real RBAC with an ``is_admin`` column on ``users`` is
    # planned (audit P3). Access still requires a valid signed session.

    @app.get("/api/admin/metrics", tags=["admin"])
    def admin_metrics(user: dict = Depends(_current_user)) -> dict[str, Any]:
        if not _ADMIN_EMAIL or (user.get("email") or "").lower() != _ADMIN_EMAIL:
            # 403, not 404 — an authenticated non-admin user is
            # explicitly disallowed. Mirrors the style used elsewhere
            # in the codebase for cross-user access, which uses 404
            # to avoid object-id enumeration; here the route itself
            # isn't secret, so 403 is appropriate.
            raise HTTPException(
                status_code=403, detail={"error": "forbidden"},
            )
        return metrics_collector.snapshot()

    # ------- ownership helpers --------------------------------------------
    #
    # Centralised WHERE-user-owns-this gate for routes that touch a
    # specific project/topic/decision/relationship. Each helper returns
    # the resolved row or raises 404 on absent OR cross-user access —
    # we do NOT distinguish those cases so the client can't enumerate
    # object IDs.

    def _require_owned_project(project_id: str, user: dict[str, Any]) -> None:
        # ensure_project is intentionally NOT called here — that creates.
        # For read/write on an existing project, ownership must already hold.
        if not _store.verify_project_ownership(
            project_id=project_id, user_id=user["user_id"],
        ):
            raise HTTPException(status_code=404, detail={"error": "project_not_found"})

    def _require_owned_topic(topic_id: str, user: dict[str, Any]) -> dict[str, Any]:
        topic = _store.get_topic_with_ownership(topic_id, user_id=user["user_id"])
        if topic is None:
            raise HTTPException(status_code=404, detail={"error": "topic_not_found"})
        return topic

    def _require_owned_decision(decision_id: str, user: dict[str, Any]) -> dict[str, Any]:
        decision = _store.get_decision_with_ownership(decision_id, user_id=user["user_id"])
        if decision is None:
            raise HTTPException(status_code=404, detail={"error": "decision_not_found"})
        return decision

    def _require_owned_relationship(relationship_id: str, user: dict[str, Any]) -> dict[str, Any]:
        rel = _store.get_relationship_with_ownership(relationship_id, user_id=user["user_id"])
        if rel is None:
            raise HTTPException(status_code=404, detail={"error": "relationship_not_found"})
        return rel

    def _planner_error_response(exc: BaseException) -> HTTPException:
        """Log the real exception, return a generic error to the client.

        The raw ``str(exc)`` from OpenAI leaks org ID, request ID, and
        sometimes model names — useful reconnaissance. We log internally
        (Sentry, stdout) so diagnosis is still possible; client just sees
        a generic failure with a correlation id.
        """
        import uuid as _uuid

        rid = _uuid.uuid4().hex[:12]
        logger.exception("[planner_call_failed rid=%s]", rid, exc_info=exc)
        return HTTPException(
            status_code=500,
            detail={"error": "planner_call_failed", "request_id": rid},
        )

    # ------- Token-budget gate (audit M5) ---------------------------------
    #
    # Enforced BEFORE the LLM call on any route that makes one. The gate
    # answers "has this user already spent today's budget?" — a cheap
    # SELECT, so it's fine to call on every request. After the call lands
    # we record the actual usage via ``_record_llm_usage`` below.

    def _require_token_budget(user: dict[str, Any]) -> None:
        budget = _load_user_daily_token_budget()
        if budget <= 0:
            return  # Gate disabled.
        try:
            usage = _store.get_usage_today(user_id=user["user_id"])
        except Exception as exc:  # noqa: BLE001
            # Instrumentation must never block users — if the usage table
            # is unreachable, log loudly and let the request through.
            logger.warning(
                "get_usage_today failed for user=%s: %s — letting request through",
                user.get("user_id"),
                exc,
            )
            return
        spent = int(usage.get("tokens_in", 0)) + int(usage.get("tokens_out", 0))
        if spent >= budget:
            logger.info(
                "user=%s exceeded daily token budget (%d >= %d)",
                user.get("user_id"),
                spent,
                budget,
            )
            retry_seconds = _seconds_until_utc_midnight()
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    "error": "daily_token_budget_exhausted",
                    "budget": budget,
                    "spent": spent,
                    "retry_after_seconds": retry_seconds,
                },
                headers={"Retry-After": str(retry_seconds)},
            )

    def _try_get_user_byok(
        byok_module: Any, user_id: str, provider: str,
    ) -> str | None:
        """Return the decrypted BYOK key for this user / provider, or ``None``.

        Swallows configuration errors so a BYOK outage (e.g.
        ``INSPIRA_BYOK_SECRET`` unset in this environment) degrades to
        the house-key path instead of 500'ing every user's turn. Logs
        the error so operators still see the problem.
        """
        try:
            return byok_module.store.get_user_byok(
                _store, user_id, provider,
            )
        except RuntimeError as exc:
            logger.warning(
                "byok lookup failed for user=%s provider=%s: %s — "
                "falling back to house key",
                user_id, provider, exc,
            )
            return None


    def _record_llm_usage(
        user: dict[str, Any],
        *,
        prompt_text: str,
        response_text: str,
        openai_usage: Any = None,
    ) -> None:
        """Accumulate tokens into today's user_usage row.

        Prefers real OpenAI ``response.usage`` when available; falls back
        to a chars/4 estimate when the adapter didn't hand back stats.
        Swallows all errors — instrumentation failures must not break
        user-facing flows.
        """
        tokens_in = 0
        tokens_out = 0
        if openai_usage is not None:
            try:
                tokens_in = int(getattr(openai_usage, "prompt_tokens", 0) or 0)
                tokens_out = int(getattr(openai_usage, "completion_tokens", 0) or 0)
            except (TypeError, ValueError):
                tokens_in = tokens_out = 0
        if tokens_in <= 0 and tokens_out <= 0:
            # Estimate path: conservative char-count / 4.
            tokens_in = max(0, len(prompt_text or "")) // _ESTIMATE_CHARS_PER_TOKEN
            tokens_out = max(0, len(response_text or "")) // _ESTIMATE_CHARS_PER_TOKEN
        try:
            _store.record_usage(
                user_id=user["user_id"],
                tokens_in=tokens_in,
                tokens_out=tokens_out,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "record_usage failed for user=%s: %s — usage not recorded",
                user.get("user_id"),
                exc,
            )

    # -----------------------------------------------------------------------
    # v2 — Inspira canvas
    # -----------------------------------------------------------------------

    @app.post(
        "/api/v2/projects/{project_id}/kickoff",
        status_code=status.HTTP_201_CREATED,
        tags=["v2"],
    )
    def v2_kickoff(
        project_id: str,
        body: KickoffBody,
        response: Response,
        request: Request,  # noqa: ARG001 — required for per-route slowapi rate limiting
        user: dict = Depends(_current_user),
    ) -> dict[str, Any]:
        user_idea = body.user_idea.strip()
        if not user_idea:
            raise HTTPException(
                status_code=400,
                detail={"error": "validation_error", "message": "user_idea is required"},
            )

        # Per-user daily token budget gate (audit M5). Runs BEFORE the LLM
        # call so an over-quota request never bills a single token.
        _require_token_budget(user)
        # Ensure the project exists AND is owned by this user BEFORE the
        # LLM call — no reason to burn tokens for a request we'd reject.
        try:
            _store.ensure_project(project_id=project_id, user_id=user["user_id"])
        except PermissionError:
            raise HTTPException(status_code=404, detail={"error": "project_not_found"})

        # Resolve the tier the turn actually runs under. Unknown slugs
        # (including values from a stale client) silently clamp down to the
        # plan default — see ``tiers.resolve_tier_for_user``.
        from .agents.tiers import (  # noqa: PLC0415
            credit_multiplier,
            kickoff_openai_model,
            parse_tier,
            resolve_tier_for_user,
        )
        resolved_tier = resolve_tier_for_user(
            _store, user["user_id"], parse_tier(body.model_tier),
        )
        # Kickoff is pinned to gpt-4o-mini regardless of tier — the
        # topic-skeleton output is constrained by a JSON schema, so
        # reasoning depth has diminishing returns. topic_turn dispatches
        # per-tier via ``tier_to_openai_model``: BASE → gpt-5-mini,
        # PRO → gpt-5, FRONTIER → gpt-5.5 (all reasoning models, root-
        # cause fix for #075's decision-emission gap on gpt-4o-mini).
        model_override = kickoff_openai_model()

        # BYOK — kickoff always runs through the OpenAI adapter today
        # (see ``tier_to_adapter`` docstring), so check only for an
        # OpenAI key. If the user stored one, we skip credit charging
        # entirely and pass the key through to the adapter.
        from . import byok as byok_module  # noqa: PLC0415

        byok_key = _try_get_user_byok(byok_module, user["user_id"], "openai")
        is_byok = byok_key is not None
        response.headers["X-Inspira-Llm-Mode"] = "byok" if is_byok else "house"

        try:
            adapter = _require_adapter()
            kickoff_result = adapter.kickoff(
                user_idea=user_idea,
                attached_sources=[s.model_dump() for s in body.attached_sources],
                locale=_validate_locale(body.locale),
                model_override=model_override,
                api_key_override=byok_key,
            )
        except RuntimeError as exc:
            raise _planner_error_response(exc)

        # Record usage AFTER the call returns. We don't have direct access
        # to the OpenAI response object (adapter owns it), so we fall back
        # to the char/4 estimate. Long-term the adapter should expose the
        # usage stats; for now the estimate is a conservative floor.
        _attached_excerpts = " ".join(
            s.excerpt or "" for s in body.attached_sources
        )
        _record_llm_usage(
            user,
            prompt_text=user_idea + " " + _attached_excerpts,
            response_text=str(kickoff_result),
        )

        # Persist the planner-inferred domain label so subsequent feature
        # routes (e.g. scaffold) can gate on it without re-running the LLM.
        domain_label = (kickoff_result.get("domain") or "").strip().lower()
        if domain_label:
            _store.set_project_domain(project_id=project_id, domain=domain_label)

        topics_raw = kickoff_result.get("topics") or []
        persisted_topics: list[dict[str, Any]] = []
        title_to_topic_id: dict[str, str] = {}
        x_step, y_rows = 440, [0, 320]
        for idx, topic in enumerate(topics_raw):
            persisted = _store.create_topic(
                project_id=project_id,
                title=topic["title"],
                icon=topic["icon"],
                position_x=float((idx // len(y_rows)) * x_step),
                position_y=float(y_rows[idx % len(y_rows)]),
                origin="planner_initial",
                order_index=idx,
                metadata={"why_this_topic": topic.get("why_this_topic")},
                user_id=user["user_id"],
            )
            persisted_topics.append(persisted)
            title_to_topic_id[topic["title"]] = persisted["topic_id"]

            # B1 (v4 reframe) — persist any pre-populated Q&A turns
            # the planner returned. Each q_and_a entry becomes a
            # planner-asked turn (the question), a user-roled turn (the
            # AI's best-guess answer the human will review), and a
            # proposed decision. Empty q_and_a → existing on-demand
            # topic_turn flow on the frontend, no inserts.
            for qa in topic.get("q_and_a") or []:
                question = (qa.get("question") or "").strip()
                answer = (qa.get("answer") or "").strip()
                decision = (qa.get("decision") or "").strip()
                if not question or not answer:
                    continue
                planner_turn = _store.append_qna_turn(
                    topic_id=persisted["topic_id"],
                    project_id=project_id,
                    role="planner",
                    body=question,
                    action="ask",
                    status="answered",
                    user_id=user["user_id"],
                )
                _store.append_qna_turn(
                    topic_id=persisted["topic_id"],
                    project_id=project_id,
                    role="user",
                    body=answer,
                    parent_turn_id=planner_turn["turn_id"],
                    status="answered",
                    user_id=user["user_id"],
                )
                if decision:
                    _store.create_decision(
                        topic_id=persisted["topic_id"],
                        project_id=project_id,
                        statement=decision,
                        proposed_by="planner",
                        rationale=None,
                        source_turn_id=planner_turn["turn_id"],
                        user_id=user["user_id"],
                    )

        relationships_raw = kickoff_result.get("relationships") or []
        persisted_relationships: list[dict[str, Any]] = []
        for rel in relationships_raw:
            src_id = title_to_topic_id.get(rel.get("from_topic_title", ""))
            tgt_id = title_to_topic_id.get(rel.get("to_topic_title", ""))
            if not src_id or not tgt_id:
                continue
            persisted_rel = _store.create_relationship(
                project_id=project_id,
                source_topic_id=src_id,
                target_topic_id=tgt_id,
                label=rel.get("label"),
                origin="planner_inferred",
                user_id=user["user_id"],
            )
            persisted_relationships.append(persisted_rel)

        return {
            "kickoff": kickoff_result,
            "topics": persisted_topics,
            "relationships": persisted_relationships,
        }

    # Static-path GETs (`/archived`, `/recently-deleted`) MUST register
    # before the `/{project_id}` wildcard below — FastAPI's first-match
    # routing otherwise binds those bare words as project_ids, 404s on
    # the lookup, and never reaches the real handlers further down the
    # file. Issue #134's 11 KeyError: 'projects' failures all traced
    # back to this drift; keep these blocks here even when other code
    # nearby moves around.

    @app.get("/api/v2/projects/archived", tags=["v2"])
    def v2_list_archived_projects_early(
        user: dict = Depends(_current_user),
    ) -> dict[str, Any]:
        return {
            "projects": _store.list_archived_v2_projects(
                user_id=user["user_id"],
            ),
        }

    @app.get("/api/v2/projects/recently-deleted", tags=["v2"])
    def v2_list_recently_deleted_projects_early(
        user: dict = Depends(_current_user),
    ) -> dict[str, Any]:
        return {
            "projects": _store.list_recently_deleted_v2_projects(
                user_id=user["user_id"],
            ),
        }

    # NOTE: Must be registered before /{project_id} wildcard routes —
    # FastAPI resolves the URL path template first, then the method,
    # so a POST to /api/v2/projects/bulk-delete that lands AFTER
    # /{project_id}/<X> registrations would 405 (wildcard captures
    # "bulk-delete" as project_id).
    #
    # Body is a Pydantic model (not fastapi.Body) on purpose: there's
    # an inner ``from fastapi import Body, Header`` later in
    # create_app that shadows the module-level Body symbol; Python
    # treats Body as a local in the whole function scope, so any
    # reference earlier than that import raises UnboundLocalError on
    # startup. The model approach sidesteps the collision entirely.
    # NOTE: BulkDeleteV2ProjectsBody is defined at module scope (top
    # of file) — function-scoped Pydantic models break FastAPI's
    # annotation resolver under ``from __future__ import annotations``.
    @app.post("/api/v2/projects/bulk-delete", tags=["v2"])
    def v2_bulk_delete_projects(
        body: BulkDeleteV2ProjectsBody,
        user: dict = Depends(_current_user),
    ) -> dict[str, Any]:
        deleted = _store.bulk_delete_v2_projects(
            project_ids=body.project_ids, user_id=user["user_id"],
        )
        return {"ok": True, "deleted": deleted}

    @app.get("/api/v2/projects/{project_id}", tags=["v2"])
    def v2_get_project(
        project_id: str, user: dict = Depends(_current_user)
    ) -> dict[str, Any]:
        """Return one v2_projects row.

        Single-project getter introduced for the W2 export modals,
        which need the project's title + metadata to compose the
        Decision Summary preview without round-tripping through
        ``listV2Projects``.
        """
        _require_owned_project(project_id, user)
        project = _store._get_v2_project(project_id)
        if project is None:
            raise HTTPException(
                status_code=404, detail={"error": "project_not_found"}
            )
        return {"project": project}

    @app.get("/api/v2/projects/{project_id}/topics", tags=["v2"])
    def v2_list_topics(project_id: str, user: dict = Depends(_current_user)) -> dict[str, Any]:
        _require_owned_project(project_id, user)
        return {"topics": _store.list_topics(project_id=project_id, user_id=user["user_id"])}

    @app.post(
        "/api/v2/projects/{project_id}/topics",
        status_code=status.HTTP_201_CREATED,
        tags=["v2"],
    )
    def v2_create_topic(
        project_id: str,
        body: TopicCreateBody,
        request: Request,  # noqa: ARG001 — required for per-route slowapi rate limiting
        user: dict = Depends(_current_user),
    ) -> dict[str, Any]:
        title = body.title.strip()
        if not title:
            raise HTTPException(
                status_code=400,
                detail={"error": "validation_error", "message": "title is required"},
            )
        # Must 404 if the project doesn't exist or isn't owned by this user.
        # ensure_project() would silently create the project row — use the
        # ownership-only check instead.
        _require_owned_project(project_id, user)
        topic = _store.create_topic(
            project_id=project_id,
            title=title,
            icon=body.icon.strip() or "flag",
            position_x=body.position_x,
            position_y=body.position_y,
            origin="user_manual",
            user_id=user["user_id"],
        )
        return {"topic": topic}

    @app.post("/api/v2/topics/{topic_id}/update", tags=["v2"])
    async def v2_update_topic(
        topic_id: str,
        request: Request,
        user: dict = Depends(_current_user),
    ) -> dict[str, Any]:
        _require_owned_topic(topic_id, user)
        # Manual body parse so we can turn "status not in whitelist" into a
        # 400 with the shape ``{"error": "invalid_status", "allowed": [...]}``
        # instead of FastAPI's default 422. The pydantic validator on
        # ``TopicUpdateBody.status`` is the authoritative gate — we just
        # translate its ValueError into the frontend-friendly error shape.
        from pydantic import ValidationError  # noqa: PLC0415

        try:
            raw = await request.json()
        except Exception:  # noqa: BLE001
            raise HTTPException(
                status_code=400,
                detail={"error": "invalid_json"},
            )
        if not isinstance(raw, dict):
            raise HTTPException(
                status_code=400,
                detail={"error": "invalid_json"},
            )
        try:
            body = TopicUpdateBody.model_validate(raw)
        except ValidationError as exc:
            # Only translate the specific ``invalid_status`` sentinel; other
            # ValidationErrors (e.g. a 1000-char icon) still bubble as 422
            # via re-raise so existing shape expectations don't drift.
            for err in exc.errors():
                loc = err.get("loc") or ()
                msg = err.get("msg") or ""
                if "status" in loc and "invalid_status" in msg:
                    raise HTTPException(
                        status_code=400,
                        detail={
                            "error": "invalid_status",
                            "allowed": sorted(VALID_TOPIC_STATUSES),
                        },
                    ) from exc
            raise
        updates = body.model_dump(exclude_unset=True, exclude_none=True)
        if not updates:
            raise HTTPException(
                status_code=400,
                detail={"error": "validation_error", "message": "no valid fields to update"},
            )
        topic = _store.update_topic(topic_id, user_id=user["user_id"], **updates)
        return {"topic": topic}

    @app.post("/api/v2/topics/{topic_id}/private-notes", tags=["v2"])
    def v2_update_topic_private_notes(
        topic_id: str,
        body: TopicPrivateNotesBody,
        user: dict = Depends(_current_user),
    ) -> dict[str, Any]:
        """Persist a private note on a topic. NEVER forwarded to the LLM.

        IDOR: the store helper verifies ownership against the caller's
        user_id; a non-owner sees the same 404 as a missing topic.

        Rate limiting: inherits the global slowapi per-IP default
        (``INSPIRA_RATE_LIMIT``, 120/min out of the box). The brief asks
        for 60/min when no other limiter exists — here the global one IS
        in place, so we rely on that rather than wiring a second custom
        limit with a separate backend. The global default is stricter than
        60/min for small deployments and already covers abuse.
        """
        topic = _store.update_topic_private_notes(
            topic_id=topic_id,
            user_id=user["user_id"],
            notes=body.notes,
        )
        if topic is None:
            raise HTTPException(status_code=404, detail={"error": "topic_not_found"})
        return {"topic": topic}

    @app.post("/api/v2/topics/{topic_id}/color", tags=["v2"])
    def v2_update_topic_color(
        topic_id: str,
        body: TopicColorBody,
        user: dict = Depends(_current_user),
    ) -> dict[str, Any]:
        """Tag a topic with one of the five palette colors, or clear the tag.

        IDOR: the store helper runs an ownership check and returns ``None``
        for both "topic missing" and "wrong owner" so we can't be used to
        probe topic_ids that belong to other users.

        Invalid color slugs raise ``ValueError`` in the store and we map
        that to a 400 here so the frontend can surface a specific error.
        """
        try:
            topic = _store.update_topic_color(
                topic_id=topic_id,
                user_id=user["user_id"],
                color=body.color,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail={"error": "invalid_topic_color", "message": str(exc)},
            ) from exc
        if topic is None:
            raise HTTPException(status_code=404, detail={"error": "topic_not_found"})
        return {"topic": topic}

    @app.post("/api/v2/topics/{topic_id}/delete", tags=["v2"])
    def v2_delete_topic(topic_id: str, user: dict = Depends(_current_user)) -> dict[str, Any]:
        _require_owned_topic(topic_id, user)
        ok = _store.delete_topic(topic_id, user_id=user["user_id"])
        if not ok:
            raise HTTPException(status_code=404, detail={"error": "topic_not_found"})
        return {"deleted": True, "topic_id": topic_id}

    # IDOR: 404 both for "the topic doesn't exist" and "you don't own the
    # project" so topic_ids stay un-enumerable — same policy as the other
    # /topics/{id}/* mutations. Returns 201 with the newly-created topic
    # so the frontend can refetch and surface the copy without a second
    # GET round-trip being mandatory.
    @app.post(
        "/api/v2/topics/{topic_id}/duplicate",
        status_code=status.HTTP_201_CREATED,
        tags=["v2"],
    )
    def v2_duplicate_topic(
        topic_id: str, user: dict = Depends(_current_user),
    ) -> dict[str, Any]:
        duplicate = _store.duplicate_topic(topic_id, user_id=user["user_id"])
        if duplicate is None:
            raise HTTPException(
                status_code=404, detail={"error": "topic_not_found"},
            )
        return {"topic": duplicate}

    # Merge two duplicate topics — the endpoint that powers the Duplicates
    # planner view's Accept button. The merge logic lives in dedupe_merge
    # (one transaction, all-or-nothing). Frontend expected this route;
    # it was missing, which is why Accept/Reject on that view were silently
    # no-oping (Reject works locally — it only removes the proposal from
    # the in-memory list — but Accept 404'd).
    #
    # The body model is declared at module scope (see MergeTopicsBody
    # below the create_app fn) — a local class inside create_app() causes
    # FastAPI to treat `body` as a query param ("loc":["query","body"]
    # 422), which silently broke the endpoint.
    @app.post(
        "/api/v2/projects/{project_id}/topics/merge",
        tags=["v2"],
    )
    def v2_merge_topics(
        project_id: str,
        body: MergeTopicsBody,
        user: dict = Depends(_current_user),
    ) -> dict[str, Any]:
        _require_owned_project(project_id, user)
        try:
            return merge_topics(
                _store,
                user_id=user["user_id"],
                project_id=project_id,
                keep_id=body.keep_topic_id,
                drop_id=body.drop_topic_id,
            )
        except ValueError as e:
            # Self-merge, cross-project, or unknown topic — return 400
            # with the error text so the frontend can toast it if needed.
            raise HTTPException(
                status_code=400, detail={"error": "merge_failed", "reason": str(e)},
            )

    @app.get("/api/v2/topics/{topic_id}/decisions", tags=["v2"])
    def v2_list_topic_decisions(topic_id: str, user: dict = Depends(_current_user)) -> dict[str, Any]:
        topic = _require_owned_topic(topic_id, user)
        decisions = _store.list_decisions(
            project_id=topic["project_id"], topic_id=topic_id, user_id=user["user_id"],
        )
        return {"decisions": decisions}

    @app.post(
        "/api/v2/topics/{topic_id}/decisions",
        status_code=status.HTTP_201_CREATED,
        tags=["v2"],
    )
    def v2_create_decision(
        topic_id: str,
        body: DecisionCreateBody,
        request: Request,  # noqa: ARG001 — required for per-route slowapi rate limiting
        user: dict = Depends(_current_user),
    ) -> dict[str, Any]:
        topic = _require_owned_topic(topic_id, user)
        statement = body.statement.strip()
        if not statement:
            raise HTTPException(
                status_code=400,
                detail={"error": "validation_error", "message": "statement is required"},
            )
        decision = _store.create_decision(
            topic_id=topic_id,
            project_id=topic["project_id"],
            statement=statement,
            proposed_by=body.proposed_by,
            rationale=(body.rationale.strip() if body.rationale else None),
            source_turn_id=body.source_turn_id,
            status=body.status,
            user_id=user["user_id"],
        )
        # Real-time collab: check whether this new decision contradicts
        # another user's earlier decision on the same project. Runs
        # synchronously on a 5s-timeout, fail-open LLM call so the save
        # never blocks. If a contradiction is detected, (a) push to this
        # user's open WebSocket sessions so the modal fires immediately,
        # and (b) include the hint in the HTTP response so the client
        # can still render the modal if the WS is down.
        contradiction_hint = _maybe_check_contradiction_and_push(
            project_id=topic["project_id"],
            new_decision=decision,
            user=user,
        )
        resp: dict[str, Any] = {"decision": decision}
        if contradiction_hint is not None:
            resp["contradiction_hint"] = contradiction_hint
        return resp

    @app.get("/api/v2/projects/{project_id}/decisions", tags=["v2"])
    def v2_list_project_decisions(project_id: str, user: dict = Depends(_current_user)) -> dict[str, Any]:
        _require_owned_project(project_id, user)
        return {
            "decisions": _store.list_decisions(
                project_id=project_id, user_id=user["user_id"],
            ),
        }

    @app.get(
        "/api/v2/projects/{project_id}/topics/{topic_id}/provenance",
        tags=["v2"],
    )
    def v2_list_topic_provenance(
        project_id: str,
        topic_id: str,
        user: dict = Depends(_current_user),
    ) -> dict[str, Any]:
        """Cited feedback items per decision for the reasoning expander.

        Live `decision.drafted` SSE events carry provenance, but cold-opens
        of completed canvases need a REST path to populate the expander.
        Returns a flat list — frontend groups by decision_id.
        """
        _require_owned_project(project_id, user)
        topic = _require_owned_topic(topic_id, user)
        if topic["project_id"] != project_id:
            raise HTTPException(
                status_code=404,
                detail={"error": "topic_not_in_project"},
            )
        # Defense in depth: feedback_items is workspace-scoped, and the
        # orchestrator only writes decision_provenance pointing at items
        # in the project's workspace. The ownership gates above already
        # block cross-tenant access via path-param tampering; the extra
        # `(p.workspace_id IS NULL OR fi.workspace_id = p.workspace_id)`
        # clause guards a future migration that mis-attributes provenance
        # from leaking other workspaces' feedback_item titles/bodies
        # through the join. Legacy projects with NULL workspace_id pass
        # through untouched.
        with _store._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    dp.decision_id, dp.feedback_item_id, dp.weight,
                    fi.title, fi.body, fi.source, fi.received_at,
                    fi.ingested_at
                FROM decision_provenance dp
                JOIN feedback_items fi ON dp.feedback_item_id = fi.item_id
                JOIN decisions d ON dp.decision_id = d.decision_id
                JOIN v2_projects p ON d.project_id = p.project_id
                WHERE d.topic_id = ?
                  AND d.retracted_at IS NULL
                  AND p.project_id = ?
                  AND (
                      p.workspace_id IS NULL
                      OR fi.workspace_id = p.workspace_id
                  )
                ORDER BY d.created_at, dp.weight DESC
                """,
                (topic_id, project_id),
            ).fetchall()
        provenance = [
            {
                "decision_id": r[0],
                "feedback_item_id": r[1],
                "weight": float(r[2]),
                "feedback_item": {
                    "item_id": r[1],
                    "title": r[3],
                    "body": r[4],
                    "source": r[5],
                    "received_at": r[6],
                    "ingested_at": r[7],
                },
            }
            for r in rows
        ]
        return {"provenance": provenance}

    @app.post("/api/v2/decisions/{decision_id}/delete", tags=["v2"])
    def v2_delete_decision(decision_id: str, user: dict = Depends(_current_user)) -> dict[str, Any]:
        _require_owned_decision(decision_id, user)
        ok = _store.delete_decision(decision_id, user_id=user["user_id"])
        if not ok:
            raise HTTPException(
                status_code=404,
                detail={"error": "decision_not_found_or_already_retracted"},
            )
        return {"deleted": True, "decision_id": decision_id}

    @app.get("/api/v2/projects/{project_id}/relationships", tags=["v2"])
    def v2_list_relationships(project_id: str, user: dict = Depends(_current_user)) -> dict[str, Any]:
        _require_owned_project(project_id, user)
        return {
            "relationships": _store.list_relationships(
                project_id=project_id, user_id=user["user_id"],
            ),
        }

    @app.post(
        "/api/v2/projects/{project_id}/relationships",
        status_code=status.HTTP_201_CREATED,
        tags=["v2"],
    )
    def v2_create_relationship(
        project_id: str, body: RelationshipCreateBody, user: dict = Depends(_current_user),
    ) -> dict[str, Any]:
        _require_owned_project(project_id, user)
        src = body.source_topic_id.strip()
        tgt = body.target_topic_id.strip()
        if not src or not tgt:
            raise HTTPException(
                status_code=400,
                detail={"error": "validation_error", "message": "source_topic_id and target_topic_id are required"},
            )
        if src == tgt:
            raise HTTPException(
                status_code=400,
                detail={"error": "self_relationship_forbidden", "message": "a relationship cannot connect a topic to itself"},
            )
        # Confirm both endpoints belong to this user too.
        _require_owned_topic(src, user)
        _require_owned_topic(tgt, user)
        label = body.label.strip() if body.label else None
        rel = _store.create_relationship(
            project_id=project_id,
            source_topic_id=src,
            target_topic_id=tgt,
            label=label,
            origin="user_drawn",
            user_id=user["user_id"],
        )
        return {"relationship": rel}

    @app.post("/api/v2/relationships/{relationship_id}/delete", tags=["v2"])
    def v2_delete_relationship(relationship_id: str, user: dict = Depends(_current_user)) -> dict[str, Any]:
        _require_owned_relationship(relationship_id, user)
        ok = _store.delete_relationship(relationship_id, user_id=user["user_id"])
        if not ok:
            raise HTTPException(
                status_code=404,
                detail={"error": "relationship_not_found"},
            )
        return {"deleted": True, "relationship_id": relationship_id}

    # L5a — PATCH for relationship label (so canvas edge-label edits
    # actually persist instead of staying local-only). Replaces a
    # long-standing TODO in `app/src/features/inspira/ProjectCanvas.tsx`.
    # Body: `{label: str | null}` — an explicit null OR empty string
    # clears the label so the edge renders without text.
    @app.patch("/api/v2/relationships/{relationship_id}", tags=["v2"])
    def v2_update_relationship(
        relationship_id: str,
        body: RelationshipPatchBody,
        user: dict = Depends(_current_user),
    ) -> dict[str, Any]:
        _require_owned_relationship(relationship_id, user)
        # Normalize empty/whitespace strings to None so the DB stores
        # NULL. The Pydantic SanitizedStr already strips control chars.
        label = body.label.strip() if body.label else None
        if label == "":
            label = None
        rel = _store.update_relationship_label(
            relationship_id=relationship_id,
            user_id=user["user_id"],
            label=label,
        )
        if rel is None:
            raise HTTPException(
                status_code=404,
                detail={"error": "relationship_not_found"},
            )
        return {"relationship": rel}

    @app.get("/api/v2/projects/{project_id}/activity", tags=["v2"])
    def v2_list_project_activity(
        project_id: str,
        limit: int = 50,
        offset: int = 0,
        user: dict = Depends(_current_user),
    ) -> dict[str, Any]:
        """Activity timeline for a single project.

        Reads the ``audit_log`` rows for this project, newest first,
        filtered to user-visible categories. Cross-user access returns
        404 (we convert the store's ``PermissionError`` to the same
        ``project_not_found`` shape as every other owned-project route
        — we never distinguish absent from forbidden on this surface).

        Query-string ``limit`` is clamped by the store (1..200) and
        ``offset`` is a plain numeric skip. The response carries a
        boolean ``has_more`` that the frontend uses to decide whether
        to render the "Load more" button.
        """
        # Cheap ownership check up-front so the store's PermissionError
        # path stays a pure internal safety net.
        _require_owned_project(project_id, user)
        if limit < 1:
            raise HTTPException(
                status_code=400,
                detail={"error": "validation_error", "message": "limit must be >= 1"},
            )
        if offset < 0:
            raise HTTPException(
                status_code=400,
                detail={"error": "validation_error", "message": "offset must be >= 0"},
            )
        try:
            return _store.list_project_activity(
                project_id=project_id,
                user_id=user["user_id"],
                limit=limit,
                offset=offset,
            )
        except PermissionError:
            raise HTTPException(status_code=404, detail={"error": "project_not_found"})

    # Export telemetry — exports are assembled client-side so there's
    # no mutation the backend can audit. This ping lets the frontend
    # tell us "I just exported {fmt}" so the Activity feed shows it.
    @app.post(
        "/api/v2/projects/{project_id}/activity/export-logged",
        tags=["v2"],
    )
    def v2_log_export(
        project_id: str,
        body: ExportLoggedBody,
        user: dict = Depends(_current_user),
    ) -> dict[str, Any]:
        _require_owned_project(project_id, user)
        fmt = body.fmt.strip().lower()
        # Reject unknown formats so a client-side typo doesn't pollute
        # the audit feed with arbitrary strings.
        allowed = {"pdf", "markdown", "json", "csv", "html"}
        if fmt not in allowed:
            raise HTTPException(
                status_code=400,
                detail={"error": "invalid_format", "allowed": sorted(allowed)},
            )
        _store._emit_audit_silent(  # noqa: SLF001
            user_id=user["user_id"],
            category="export",
            action="create",
            project_id=project_id,
            after={"format": fmt},
        )
        return {"ok": True}

    @app.post(
        "/api/v2/topics/{topic_id}/turn",
        status_code=status.HTTP_201_CREATED,
        tags=["v2"],
    )
    def v2_topic_turn(
        topic_id: str,
        body: TopicTurnBody,
        response: Response,
        request: Request,  # noqa: ARG001 — required for per-route slowapi rate limiting
        user: dict = Depends(_current_user),
    ) -> dict[str, Any]:
        topic = _require_owned_topic(topic_id, user)

        # Per-user daily token budget gate (audit M5). Runs BEFORE any
        # writes or the LLM call.
        _require_token_budget(user)

        project_id = topic["project_id"]

        user_answer = body.user_answer.strip()
        if user_answer:
            _store.append_qna_turn(
                topic_id=topic_id,
                project_id=project_id,
                role="user",
                body=user_answer,
                status="answered",
                user_id=user["user_id"],
            )

        turns = _store.list_qna_turns(topic_id=topic_id, user_id=user["user_id"])
        decisions = _store.list_decisions(
            project_id=project_id, topic_id=topic_id, user_id=user["user_id"],
        )

        # Load existing checkpoints from topic metadata so the adapter can
        # inject them into the prompt context (turns 2+).
        topic_metadata = topic.get("metadata") or {}
        existing_checkpoints: list[dict[str, Any]] = topic_metadata.get("checkpoints") or []

        # EXPLICIT WHITELIST — every field forwarded to the LLM adapter is
        # named here. ``topic`` carries ``private_notes`` as of migration
        # 20260422_0006, but private notes are user-only and must NEVER be
        # shown to the planner. Do not replace this literal dict with a
        # spread of ``topic`` or a shallow copy. See
        # services/tests/test_topic_private_notes.py for the guard test.
        current_topic_view = {
            "title": topic["title"],
            "icon": topic["icon"],
            "decisions": decisions,
            "turns": turns,
            "open_questions": [],
            "risks_assumptions": [],
            "checkpoints": existing_checkpoints,
        }

        all_topics = _store.list_topics(project_id=project_id, user_id=user["user_id"])

        # Build a title→topic_id index for decision rerouting; keep it here
        # rather than inside the LLM section so the sanitizer can stay title-only.
        # Topic titles are not unique in the DB; we use the first match and log
        # a warning on ambiguity (per spec).
        _sibling_title_to_id: dict[str, str] = {}
        for ot in all_topics:
            if ot["topic_id"] == topic_id:
                continue
            title_key = (ot.get("title") or "").strip().lower()
            if not title_key:
                continue
            if title_key in _sibling_title_to_id:
                logger.warning(
                    "Duplicate sibling topic title %r in project %s — "
                    "decision routing will use the first match",
                    ot.get("title"),
                    project_id,
                )
                continue
            _sibling_title_to_id[title_key] = ot["topic_id"]

        # One project-scoped query instead of N per-topic queries.
        # Prune to statement-only: the LLM needs neighbour context, not
        # full metadata (rationale, timestamps, IDs inflate input tokens
        # by ~3–5x on mature projects without adding reasoning value).
        _all_decisions = _store.list_decisions(
            project_id=project_id, user_id=user["user_id"],
        )
        _decisions_by_topic: dict[str, list[dict[str, Any]]] = {}
        for _d in _all_decisions:
            _decisions_by_topic.setdefault(_d["topic_id"], []).append(
                {"statement": _d["statement"]}
            )
        other_topics_view = [
            {
                "title": ot["title"],
                "decisions": _decisions_by_topic.get(ot["topic_id"], []),
            }
            for ot in all_topics
            if ot["topic_id"] != topic_id
        ]

        # Resolve model tier (per-turn override wins, clamped to plan).
        from .agents.tiers import (  # noqa: PLC0415
            ModelTier,
            credit_multiplier,
            parse_tier,
            resolve_tier_for_user,
            select_tier_after_cap_check,
            tier_to_adapter,
            tier_to_claude_model,
            tier_to_openai_model,
        )
        resolved_tier = resolve_tier_for_user(
            _store, user["user_id"], parse_tier(body.model_tier),
        )

        # #080 cap check: per-tier monthly output-token ceilings with
        # auto-fallback. If the user's currently-resolved tier is
        # exhausted, fall back to the next-cheaper tier (FRONTIER →
        # PRO → BASE). If everything is exhausted, 429 with
        # ``errors.monthly_cap_reached``. ``fell_back_from`` is non-
        # None when a fallback occurred — surfaced in the response
        # header so the FE can show a soft "switched to <tier>" toast.
        plan_slug_for_caps = (
            (_store.get_subscription(user_id=user["user_id"]) or {}).get("plan")
            or "free"
        )
        effective_tier, fell_back_from = select_tier_after_cap_check(
            _store, user["user_id"], plan_slug_for_caps, resolved_tier,
        )
        if effective_tier is None:
            raise HTTPException(
                status_code=429,
                detail={
                    "error": "monthly_cap_reached",
                    "tier": resolved_tier.value,
                    "plan_slug": plan_slug_for_caps,
                },
            )
        if fell_back_from is not None and fell_back_from is not effective_tier:
            response.headers["X-Inspira-Llm-Tier-Fallback"] = (
                f"{fell_back_from.value}->{effective_tier.value}"
            )
        resolved_tier = effective_tier

        # Resolve adapter choice now — we need to know which provider
        # we're about to bill (OpenAI vs Anthropic) to look up the right
        # BYOK key. ``tier_to_adapter`` returns the shared house-account
        # client regardless of BYOK; the per-call ``api_key_override``
        # is what swaps the credentials in without mutating the shared
        # client.
        openai_adapter_instance = _require_adapter()
        claude_adapter_instance = (
            _get_claude_adapter()
            if resolved_tier in (ModelTier.FRONTIER, ModelTier.ENTERPRISE)
            else None
        )
        selected_adapter = tier_to_adapter(
            resolved_tier,
            openai_adapter=openai_adapter_instance,
            claude_adapter=claude_adapter_instance,
        )
        is_claude = (
            selected_adapter is claude_adapter_instance
            and claude_adapter_instance is not None
        )
        model_override = (
            tier_to_claude_model(resolved_tier)
            if is_claude
            else tier_to_openai_model(resolved_tier)
        )

        # BYOK — look up the user's key for whichever provider this
        # turn is about to call. If present, we skip the credit charge
        # and pass the key as ``api_key_override``.
        from . import byok as byok_module  # noqa: PLC0415

        byok_provider = "anthropic" if is_claude else "openai"
        byok_key = _try_get_user_byok(
            byok_module, user["user_id"], byok_provider,
        )
        is_byok = byok_key is not None
        response.headers["X-Inspira-Llm-Mode"] = "byok" if is_byok else "house"

        # Per-tier reasoning effort + adapter timeout. BASE → low + 15s
        # default; PRO/FRONTIER → None (medium) + 60s. See
        # ``tiers.tier_to_reasoning_effort`` and ``tier_to_timeout_s``
        # for the locked policy (#075 iteration-4 fix for
        # gpt-5-mini timing out at default medium reasoning).
        from .agents.tiers import (  # noqa: PLC0415
            tier_to_reasoning_effort,
            tier_to_timeout_s,
        )
        try:
            turn_result = selected_adapter.topic_turn(
                current_topic=current_topic_view,
                other_topics=other_topics_view,
                sources=[s.model_dump() for s in body.attached_sources] or None,
                locale=_validate_locale(body.locale),
                model_override=model_override,
                api_key_override=byok_key,
                reasoning_effort=tier_to_reasoning_effort(resolved_tier),
                timeout_s=tier_to_timeout_s(resolved_tier),
            )
        except RuntimeError as exc:
            raise _planner_error_response(exc)
        except Exception:
            # PR 2: credits are gone, so there's nothing to refund here.
            # Re-raise so the outer handler emits the planner-error envelope.
            raise

        # Record token usage after the LLM returned. No direct usage stats
        # from the adapter right now — estimate from input/output text.
        _attached_excerpts = " ".join(
            s.excerpt or "" for s in body.attached_sources
        )
        _record_llm_usage(
            user,
            prompt_text=(user_answer + " " + _attached_excerpts),
            response_text=str(turn_result),
        )

        # #080: bump the per-tier monthly counter. Estimate output
        # tokens from the response shape (chars/4) — same heuristic as
        # ``_record_llm_usage`` falls back to. Failures are logged but
        # don't propagate (counter drift is preferable to losing the
        # user's turn).
        _tier_tokens_out = max(0, len(str(turn_result))) // _ESTIMATE_CHARS_PER_TOKEN
        try:
            _store.increment_tier_usage(
                user_id=user["user_id"],
                tier=resolved_tier.value,
                tokens=_tier_tokens_out,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "increment_tier_usage failed user=%s tier=%s tokens=%d: %s",
                user.get("user_id"), resolved_tier.value,
                _tier_tokens_out, exc,
            )

        # Compute the post-update checkpoint list first (independent of the
        # planner turn). We need this BEFORE deciding whether to persist the
        # planner turn, because if all checkpoints are now "answered" we may
        # need to override the action to suggest_close (which suppresses the
        # persist). The ``answered_in_turn_id`` wiring below points at the
        # planner turn that WOULD be appended this turn; it's backfilled after
        # the persist via a second pass when necessary.
        #
        # First turn: planned_checkpoints initializes the list (all "open").
        # Subsequent turns: checkpoint_updates merges status changes in.
        planned_checkpoints = turn_result.get("planned_checkpoints")
        checkpoint_updates = turn_result.get("checkpoint_updates")
        # E2E 2026-04-25 #2/#3: gpt-4o-mini occasionally re-emits a fresh
        # planned_checkpoints array on subsequent turns instead of leaving
        # it null. That used to wipe the existing list (all-"open" again)
        # and DECREASE the count whenever the LLM hallucinated a shorter
        # outline — visible to the user as "0 OF 5 → 0 OF 4 → 0 OF 2"
        # progress that never actually advances. Defend: only honour
        # planned_checkpoints when the topic has none yet (true first turn).
        if planned_checkpoints and not existing_checkpoints:
            # Initialize all checkpoints as "open".
            merged_checkpoints = [
                {"id": cp["id"], "question": cp["question"], "status": "open", "answered_in_turn_id": None}
                for cp in planned_checkpoints
            ]
        elif planned_checkpoints and existing_checkpoints:
            # Hallucinated re-init: log + keep existing list. Apply
            # checkpoint_updates if also supplied (rare belt-and-braces).
            logger.warning(
                "topic_turn: ignored planned_checkpoints (%d items) on a "
                "topic with %d existing checkpoints; preserving existing "
                "outline. checkpoint_updates=%s",
                len(planned_checkpoints), len(existing_checkpoints),
                "present" if checkpoint_updates else "absent",
            )
            if checkpoint_updates:
                update_map = {u["id"]: u["status"] for u in checkpoint_updates}
                merged_checkpoints = []
                for cp in existing_checkpoints:
                    if cp["id"] in update_map:
                        merged_checkpoints.append({**cp, "status": update_map[cp["id"]]})
                    else:
                        merged_checkpoints.append(cp)
            else:
                merged_checkpoints = existing_checkpoints
        elif checkpoint_updates:
            # Apply status updates to the existing list.
            update_map = {u["id"]: u["status"] for u in checkpoint_updates}
            merged_checkpoints = []
            for cp in existing_checkpoints:
                if cp["id"] in update_map:
                    new_status = update_map[cp["id"]]
                    answered_in = cp.get("answered_in_turn_id")
                    merged_checkpoints.append({
                        **cp, "status": new_status,
                        "answered_in_turn_id": answered_in,
                    })
                else:
                    merged_checkpoints.append(cp)
        else:
            merged_checkpoints = existing_checkpoints

        # E2E 2026-04-25 #2 follow-up: gpt-4o-mini regularly emits no
        # checkpoint_updates even on substantive replies, leaving the
        # progress counter stuck at 0/N forever. Backend safety net:
        # when the LLM stayed silent on checkpoint_updates AND the user
        # gave a substantive reply, mark the FIRST open checkpoint as
        # "answered". The LLM's own checkpoint_updates always wins (we
        # only fire when checkpoint_updates is empty/null). Synthesize
        # a fake update entry so persistence below still triggers.
        if (
            user_answer
            and not checkpoint_updates
            and existing_checkpoints
            and _user_reply_is_substantive(user_answer)
            and merged_checkpoints
        ):
            _open_idxs = [
                i for i, cp in enumerate(merged_checkpoints)
                if cp.get("status") == "open"
            ]
            if _open_idxs:
                _i = _open_idxs[0]
                _target_id = merged_checkpoints[_i].get("id")
                merged_checkpoints[_i] = {
                    **merged_checkpoints[_i],
                    "status": "answered",
                    "answered_in_turn_id": None,
                }
                if _target_id:
                    checkpoint_updates = [
                        {"id": _target_id, "status": "answered"}
                    ]

        # E2E 2026-04-25 #1 follow-up: same model-stubbornness pattern
        # for proposed_decisions — gpt-4o-mini stays silent. Synthesize
        # a single proposed decision from the user's reply text so the
        # Product feedback: the auto-synthesis heuristic
        # below was capturing the user's literal reply text as a
        # "decision" any time the LLM didn't emit one — even for short
        # replies like "No" or "No, we don't want that." That's not a
        # decision, it's just an answer. Decisions should be a SUMMARY
        # of meaningful choices, not a verbatim transcript.
        #
        # The heuristic is removed. The LLM is the authoritative source
        # of proposed_decisions; if it stays silent, no decision is
        # recorded for that turn (user still sees the reply in the
        # turn thread, can promote it manually if it was a real call).
        # The strengthened prompt in agents/prompts.py asks the LLM to
        # emit decisions per substantive reply, which is the right place
        # to enforce judgement about what "meaningful" means.

        # Auto-suggest_close when all checkpoints are answered but the LLM
        # forgot to close out. This closes a silent-stop gap where the thread
        # would end without an explicit "we're done here" turn.
        all_answered = (
            len(merged_checkpoints) > 0
            and all(cp.get("status") == "answered" for cp in merged_checkpoints)
        )
        if all_answered and turn_result.get("action") != "suggest_close":
            topic_title = topic.get("title") or "this topic"
            turn_result["action"] = "suggest_close"
            turn_result["question"] = (
                f"We've covered every checkpoint on {topic_title} — "
                "want to close this out?"
            )
            turn_result["why_this_matters"] = None
            turn_result["suggested_responses"] = [
                {"label": "Close the topic \u2192", "intent": "close"},
                {"label": "I want to keep going \u2192", "intent": "continue"},
            ]

        planner_turn = None
        if turn_result.get("action") != "suggest_close":
            planner_turn = _store.append_qna_turn(
                topic_id=topic_id,
                project_id=project_id,
                role="planner",
                body=turn_result.get("question") or "",
                status="open",
                why_this_matters=turn_result.get("why_this_matters"),
                action=turn_result.get("action"),
                suggested_responses=turn_result.get("suggested_responses") or [],
                user_id=user["user_id"],
            )

        # Backfill answered_in_turn_id for any checkpoint that just flipped to
        # "answered" this turn without one already set. Points at the planner
        # turn we just appended so the UI can link back to the confirming
        # exchange; if no planner turn was persisted (suggest_close path),
        # the field stays None and the UI falls back to untargeted display.
        if (planned_checkpoints or checkpoint_updates) and planner_turn:
            for cp in merged_checkpoints:
                if cp.get("status") == "answered" and not cp.get("answered_in_turn_id"):
                    cp["answered_in_turn_id"] = planner_turn["turn_id"]

        # Persist the merged checkpoint state once, after any backfill.
        if planned_checkpoints or checkpoint_updates:
            _store.update_topic_checkpoints(
                topic_id, user["user_id"], merged_checkpoints,
            )

        # Persist proposed_decisions, routing each to the correct topic.
        # The sanitizer already validated target_topic_title against sibling
        # titles; here we resolve them to IDs and write the DB rows.
        rerouted_decisions: list[dict[str, Any]] = []
        for proposal in (turn_result.get("proposed_decisions") or []):
            raw_target = (proposal.get("target_topic_title") or "").strip()
            target_title_lower = raw_target.lower()
            resolve_topic_id = (
                _sibling_title_to_id.get(target_title_lower)
                if raw_target
                else None
            )
            dest_topic_id = resolve_topic_id if resolve_topic_id else topic_id
            created = _store.create_decision(
                topic_id=dest_topic_id,
                project_id=project_id,
                statement=proposal.get("statement", ""),
                rationale=proposal.get("rationale"),
                source_turn_id=proposal.get("extracted_from_turn_id"),
                proposed_by="planner",
                status="proposed",
                user_id=user["user_id"],
            )
            if resolve_topic_id and resolve_topic_id != topic_id:
                rerouted_decisions.append({
                    "decision_id": created["decision_id"],
                    "original_topic_id": topic_id,
                    "actual_topic_id": resolve_topic_id,
                    "actual_topic_title": raw_target,
                })

        # Auto-persist new_topic_proposal if the sanitizer left it intact.
        # Runs the auto-linker and includes the new topic + relationships in
        # the response so the frontend can splice them in without a refetch.
        created_topic_payload: dict[str, Any] | None = None
        ntp = turn_result.get("new_topic_proposal")
        # API-layer duplicate-title guard: the adapter sanitizer already catches
        # collisions when running via the real OpenAI path, but mocked adapters
        # skip it. Re-check here so tests and non-OpenAI adapters are consistent.
        if ntp:
            proposed_lower = (ntp.get("title") or "").strip().lower()
            sibling_titles_lower = {
                (ot.get("title") or "").strip().lower()
                for ot in all_topics
                if ot.get("topic_id") != topic_id
            }
            if not proposed_lower or proposed_lower in sibling_titles_lower:
                logger.debug(
                    "Dropping new_topic_proposal %r — title collision or empty",
                    ntp.get("title"),
                )
                ntp = None
        if ntp:
            try:
                # Position the new topic near the current topic so it appears
                # in the user's field of view on the canvas.
                all_topics_for_pos = _store.list_topics(
                    project_id=project_id, user_id=user["user_id"],
                )
                if all_topics_for_pos:
                    sum_x = sum(float(t.get("position_x") or 0) for t in all_topics_for_pos)
                    sum_y = sum(float(t.get("position_y") or 0) for t in all_topics_for_pos)
                    pos_x = sum_x / len(all_topics_for_pos) + 80.0
                    pos_y = sum_y / len(all_topics_for_pos) + 80.0
                else:
                    pos_x, pos_y = 0.0, 0.0

                new_topic = _store.create_topic(
                    project_id=project_id,
                    title=ntp["title"],
                    icon=ntp.get("icon", "lightbulb"),
                    position_x=pos_x,
                    position_y=pos_y,
                    origin="planner_proposed",
                    metadata={"why_this_topic": ntp.get("why", "")},
                    user_id=user["user_id"],
                )
                # Re-read sibling topics (includes the new one) for auto-link.
                sibling_topics = [
                    t for t in _store.list_topics(
                        project_id=project_id, user_id=user["user_id"],
                    )
                    if t.get("topic_id") != new_topic.get("topic_id")
                ]
                try:
                    adapter_al = _get_auto_link_adapter()
                    al_proposals = adapter_al.propose_links(
                        new_topic=new_topic,
                        existing_topics=sibling_topics,
                    )
                    new_relationships = _resolve_auto_link_proposals(
                        new_topic=new_topic,
                        existing_topics=sibling_topics,
                        proposals=al_proposals,
                        project_id=project_id,
                        user=user,
                    )
                    _record_llm_usage(
                        user,
                        prompt_text=new_topic.get("title", "") + " ".join(
                            t.get("title", "") for t in sibling_topics
                        ),
                        response_text=str(al_proposals),
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "auto_link after topic_turn new_topic_proposal failed: %s", exc,
                    )
                    new_relationships = []
                created_topic_payload = {
                    "topic": new_topic,
                    "relationships": new_relationships,
                }
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Failed to auto-persist new_topic_proposal from topic_turn: %s", exc,
                )

        # Pass topic_deletion_suggestion through — do NOT mutate; UI-only until user confirms.
        # API-layer guards (the adapter sanitizer covers these on the real path, but
        # mocked adapters skip it, so we re-check here for consistency):
        # 1. target must be a known sibling topic (by topic_id or title)
        # 2. target must not be the current topic
        # 3. reason must be non-empty
        deletion_suggestion = turn_result.get("topic_deletion_suggestion")
        if deletion_suggestion:
            ds_target_id = (deletion_suggestion.get("target_topic_id") or "").strip()
            ds_target_title = (deletion_suggestion.get("target_topic_title") or "").strip().lower()
            ds_reason = (deletion_suggestion.get("reason") or "").strip()
            sibling_ids = {
                ot.get("topic_id") for ot in all_topics if ot.get("topic_id") != topic_id
            }
            sibling_titles_lower = {
                (ot.get("title") or "").strip().lower()
                for ot in all_topics
                if ot.get("topic_id") != topic_id
            }
            if (
                ds_target_id == topic_id
                or not ds_target_id
                or not ds_reason
                or (ds_target_id not in sibling_ids and ds_target_title not in sibling_titles_lower)
            ):
                logger.warning(
                    "[v2_topic_turn] deletion suggestion dropped — target_id=%r title=%r invalid",
                    ds_target_id,
                    deletion_suggestion.get("target_topic_title"),
                )
                deletion_suggestion = None

        return {
            "turn_result": turn_result,
            "planner_turn": planner_turn,
            "rerouted_decisions": rerouted_decisions,
            "checkpoints": merged_checkpoints,
            "created_topic": created_topic_payload,
            "topic_deletion_suggestion": deletion_suggestion,
        }

    # -----------------------------------------------------------------------
    # Phase 1 SSE streaming routes
    # -----------------------------------------------------------------------
    #
    # These mirror the non-streaming v2_kickoff and v2_topic_turn routes but
    # wrap the response in an SSE stream so the frontend can flip its UI to
    # "AI is thinking…" the moment the first heartbeat lands (~50ms) instead
    # of waiting 6-12s for the full LLM round-trip. The pre-call gates
    # (auth, ownership, budget, BYOK lookup, credit charge) all run
    # synchronously in the route handler so a 4xx is returned as regular
    # JSON before any stream starts. Once we yield the first heartbeat the
    # response headers are flushed and we're committed to SSE for the rest
    # of the response — including any error path, which is why errors
    # inside the generator emit an ``error`` event instead of raising.
    #
    # CRITICAL: ``adapter.kickoff`` and ``adapter.topic_turn`` are blocking
    # synchronous calls (they call into OpenAI's sync SDK). If we awaited
    # them directly from the async generator they'd hold the event loop
    # for the full duration of the LLM call and the heartbeat bytes would
    # never reach the client. ``run_in_executor`` shoves the blocking call
    # onto a worker thread so the event loop is free to flush the
    # heartbeat the moment it's yielded.

    def _streaming_enabled() -> bool:
        """Staged-rollout feature flag for the SSE streaming routes.

        Defaults to *off* in production so we can dark-launch the new
        routes without exposing them to users. Flip ``INSPIRA_ENABLE_
        STREAM_KICKOFF=1`` (or ``true``) on a deploy to opt that
        environment in. The frontend's ``ssePost`` callers wrap the
        request in a ``try/catch`` that falls back to the non-streaming
        endpoint on a ``503 streaming_disabled`` response, so flipping
        the flag is reversible without a frontend deploy.

        Read at request time (not import time) so test suites that toggle
        the env var inside ``setUpClass`` see the change without
        needing to rebuild the FastAPI app.
        """
        raw = os.environ.get("INSPIRA_ENABLE_STREAM_KICKOFF", "").strip().lower()
        return raw in ("1", "true", "yes", "on")

    @app.post(
        "/api/v2/projects/{project_id}/kickoff/stream",
        tags=["v2"],
    )
    async def v2_kickoff_stream(
        project_id: str,
        body: KickoffBody,
        user: dict = Depends(_current_user),
    ):
        if not _streaming_enabled():
            raise HTTPException(
                status_code=503,
                detail={"error": "streaming_disabled"},
            )
        user_idea = body.user_idea.strip()
        if not user_idea:
            raise HTTPException(
                status_code=400,
                detail={"error": "validation_error", "message": "user_idea is required"},
            )

        _require_token_budget(user)
        try:
            _store.ensure_project(project_id=project_id, user_id=user["user_id"])
        except PermissionError:
            raise HTTPException(
                status_code=404, detail={"error": "project_not_found"},
            )

        from .agents.tiers import (  # noqa: PLC0415
            credit_multiplier,
            kickoff_openai_model,
            parse_tier,
            resolve_tier_for_user,
        )
        resolved_tier = resolve_tier_for_user(
            _store, user["user_id"], parse_tier(body.model_tier),
        )
        model_override = kickoff_openai_model()

        from . import byok as byok_module  # noqa: PLC0415

        byok_key = _try_get_user_byok(byok_module, user["user_id"], "openai")
        is_byok = byok_key is not None
        llm_mode_header = "byok" if is_byok else "house"

        adapter = _require_adapter()
        attached_payload = [s.model_dump() for s in body.attached_sources]
        validated_locale = _validate_locale(body.locale)
        attached_excerpts_text = " ".join(
            s.excerpt or "" for s in body.attached_sources
        )

        async def _kickoff_generator():
            import asyncio  # noqa: PLC0415

            # P1.7 — localize the thinking-message into the user's UI
            # locale; falls back to English when locale is en/unknown.
            yield format_sse(
                "heartbeat",
                {
                    "status": "thinking",
                    "message": thinking_message(
                        "kickoff.reading_idea", validated_locale,
                    ),
                },
            )
            try:
                # ``get_running_loop()`` instead of ``get_event_loop()``:
                # the latter is deprecated in 3.12+ when called from
                # async code, and we're already inside an async generator.
                loop = asyncio.get_running_loop()
                # Run the (blocking) LLM call in the default executor and
                # emit progressive heartbeats every few seconds so the
                # client sees the stream is alive. Without these, the
                # browser sits on a single static message for the full
                # LLM roundtrip (~30-60s on frontier models) and the
                # user assumes the app hung.
                kickoff_task = loop.run_in_executor(
                    None,
                    lambda: adapter.kickoff(
                        user_idea=user_idea,
                        attached_sources=attached_payload,
                        locale=validated_locale,
                        model_override=model_override,
                        api_key_override=byok_key,
                    ),
                )
                # Progressive heartbeat loop. Walks through a short script
                # of reassuring status lines at ~3s intervals so the
                # canvas shell always looks alive. The final line repeats
                # until the LLM returns.
                # Progress script stops LYING about how close we are.
                # The original ended on "Almost ready…" and looped that
                # forever — a user whose LLM request was hung saw the
                # same phrase for 10 minutes (caught in mobile
                # testing). New script ramps from reassuring →
                # honest → apologetic as elapsed time grows. The final
                # phrase only fires after ~30s so it doesn't panic a
                # user whose turn is legitimately slow.
                # P1.7 — each tuple entry is a thinking_messages key;
                # localized at emit time below. Final 3 keys (taking_a_moment,
                # still_working, still_working_long) are shared with the
                # topic_turn ramp.
                _progress_script = (
                    "kickoff.finding_shape",     # 0s
                    "kickoff.sketching_topics",  # 3s
                    "kickoff.mapping_connections",  # 6s
                    "kickoff.polishing_map",     # 9s
                    "common.taking_a_moment",    # 12s
                    "common.still_working",      # 15s
                    "common.still_working_long",  # 18s
                )
                # Hard-stop after this many ticks. Each tick is ~3s, so
                # 10 ticks = ~30s total. Timeout hardening: the W2/F5
                # ingestion pipeline + worker has landed, so the
                # original 90s cap is back. The 30s floor was a
                # tactical hedge; the structural fix is now in place.
                # Reverted from 10 → 30 ticks.
                _HARD_TIMEOUT_TICKS = 30
                step = 0
                while not kickoff_task.done():
                    if step >= _HARD_TIMEOUT_TICKS:
                        kickoff_task.cancel()
                        yield format_sse(
                            "error",
                            {
                                "code": "planner_timeout",
                                "message": (
                                    "The planner took too long to respond. "
                                    "Please try again."
                                ),
                                "elapsed_s": step * 3,
                            },
                        )
                        return
                    try:
                        await asyncio.wait_for(
                            asyncio.shield(kickoff_task),
                            timeout=3.0,
                        )
                    except asyncio.TimeoutError:
                        # P1.7 — localize at emit time so a future
                        # locale switch mid-flight (rare but possible
                        # via a reconnect) picks up the new language.
                        key = _progress_script[
                            min(step, len(_progress_script) - 1)
                        ]
                        yield format_sse(
                            "heartbeat",
                            {
                                "status": "thinking",
                                "message": thinking_message(
                                    key, validated_locale,
                                ),
                                # Surface elapsed time so the FE can
                                # render a count-up alongside the
                                # rotating "thinking…" copy. Users
                                # then see motion (4s, 7s, 10s…) rather
                                # than a static spinner.
                                "elapsed_s": step * 3,
                            },
                        )
                        step += 1
                kickoff_result = kickoff_task.result()
            except RuntimeError as exc:
                rid_exc = _planner_error_response(exc)
                detail = (
                    rid_exc.detail if isinstance(rid_exc.detail, dict)
                    else {"error": "planner_call_failed"}
                )
                yield format_sse(
                    "error",
                    {
                        "code": "planner_error",
                        "message": "The planner failed to respond.",
                        "detail": detail,
                    },
                )
                return
            except Exception as exc:  # noqa: BLE001
                logger.exception("[v2_kickoff_stream] adapter.kickoff failed")
                yield format_sse(
                    "error",
                    {
                        "code": "planner_error",
                        "message": str(exc) or "Unexpected planner failure",
                    },
                )
                return

            try:
                _record_llm_usage(
                    user,
                    prompt_text=user_idea + " " + attached_excerpts_text,
                    response_text=str(kickoff_result),
                )

                domain_label = (
                    kickoff_result.get("domain") or ""
                ).strip().lower()
                if domain_label:
                    _store.set_project_domain(
                        project_id=project_id, domain=domain_label,
                    )

                topics_raw = kickoff_result.get("topics") or []
                persisted_topics: list[dict[str, Any]] = []
                title_to_topic_id: dict[str, str] = {}
                x_step, y_rows = 440, [0, 320]
                for idx, topic in enumerate(topics_raw):
                    persisted = _store.create_topic(
                        project_id=project_id,
                        title=topic["title"],
                        icon=topic["icon"],
                        position_x=float((idx // len(y_rows)) * x_step),
                        position_y=float(y_rows[idx % len(y_rows)]),
                        origin="planner_initial",
                        order_index=idx,
                        metadata={"why_this_topic": topic.get("why_this_topic")},
                        user_id=user["user_id"],
                    )
                    persisted_topics.append(persisted)
                    title_to_topic_id[topic["title"]] = persisted["topic_id"]

                    # B1 — same q_and_a persistence as the non-streaming
                    # v2_kickoff handler. See comment there for design.
                    for qa in topic.get("q_and_a") or []:
                        question = (qa.get("question") or "").strip()
                        answer = (qa.get("answer") or "").strip()
                        decision = (qa.get("decision") or "").strip()
                        if not question or not answer:
                            continue
                        planner_turn = _store.append_qna_turn(
                            topic_id=persisted["topic_id"],
                            project_id=project_id,
                            role="planner",
                            body=question,
                            action="ask",
                            status="answered",
                            user_id=user["user_id"],
                        )
                        _store.append_qna_turn(
                            topic_id=persisted["topic_id"],
                            project_id=project_id,
                            role="user",
                            body=answer,
                            parent_turn_id=planner_turn["turn_id"],
                            status="answered",
                            user_id=user["user_id"],
                        )
                        if decision:
                            _store.create_decision(
                                topic_id=persisted["topic_id"],
                                project_id=project_id,
                                statement=decision,
                                proposed_by="planner",
                                rationale=None,
                                source_turn_id=planner_turn["turn_id"],
                                user_id=user["user_id"],
                            )

                relationships_raw = kickoff_result.get("relationships") or []
                persisted_relationships: list[dict[str, Any]] = []
                for rel in relationships_raw:
                    src_id = title_to_topic_id.get(rel.get("from_topic_title", ""))
                    tgt_id = title_to_topic_id.get(rel.get("to_topic_title", ""))
                    if not src_id or not tgt_id:
                        continue
                    persisted_rel = _store.create_relationship(
                        project_id=project_id,
                        source_topic_id=src_id,
                        target_topic_id=tgt_id,
                        label=rel.get("label"),
                        origin="planner_inferred",
                        user_id=user["user_id"],
                    )
                    persisted_relationships.append(persisted_rel)

                envelope = {
                    "kickoff": kickoff_result,
                    "topics": persisted_topics,
                    "relationships": persisted_relationships,
                }
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "[v2_kickoff_stream] post-LLM persistence failed",
                )
                yield format_sse(
                    "error",
                    {"code": "planner_error", "message": str(exc)},
                )
                return

            yield format_sse("complete", envelope)

        return sse_stream(
            _kickoff_generator(),
            extra_headers={"X-Inspira-Llm-Mode": llm_mode_header},
        )

    @app.post(
        "/api/v2/topics/{topic_id}/turn/stream",
        tags=["v2"],
    )
    async def v2_topic_turn_stream(
        topic_id: str,
        body: TopicTurnBody,
        user: dict = Depends(_current_user),
    ):
        if not _streaming_enabled():
            raise HTTPException(
                status_code=503,
                detail={"error": "streaming_disabled"},
            )
        topic = _require_owned_topic(topic_id, user)
        _require_token_budget(user)

        project_id = topic["project_id"]

        user_answer = body.user_answer.strip()
        if user_answer:
            _store.append_qna_turn(
                topic_id=topic_id,
                project_id=project_id,
                role="user",
                body=user_answer,
                status="answered",
                user_id=user["user_id"],
            )

        turns = _store.list_qna_turns(topic_id=topic_id, user_id=user["user_id"])
        decisions = _store.list_decisions(
            project_id=project_id, topic_id=topic_id, user_id=user["user_id"],
        )

        topic_metadata = topic.get("metadata") or {}
        existing_checkpoints: list[dict[str, Any]] = (
            topic_metadata.get("checkpoints") or []
        )

        # Same EXPLICIT WHITELIST as v2_topic_turn — private_notes MUST NOT
        # be forwarded to the planner. See test_topic_private_notes.py.
        current_topic_view = {
            "title": topic["title"],
            "icon": topic["icon"],
            "decisions": decisions,
            "turns": turns,
            "open_questions": [],
            "risks_assumptions": [],
            "checkpoints": existing_checkpoints,
        }

        all_topics = _store.list_topics(
            project_id=project_id, user_id=user["user_id"],
        )

        _sibling_title_to_id: dict[str, str] = {}
        for ot in all_topics:
            if ot["topic_id"] == topic_id:
                continue
            title_key = (ot.get("title") or "").strip().lower()
            if not title_key:
                continue
            if title_key in _sibling_title_to_id:
                logger.warning(
                    "Duplicate sibling topic title %r in project %s — "
                    "decision routing will use the first match",
                    ot.get("title"), project_id,
                )
                continue
            _sibling_title_to_id[title_key] = ot["topic_id"]

        _all_decisions = _store.list_decisions(
            project_id=project_id, user_id=user["user_id"],
        )
        _decisions_by_topic: dict[str, list[dict[str, Any]]] = {}
        for _d in _all_decisions:
            _decisions_by_topic.setdefault(_d["topic_id"], []).append(
                {"statement": _d["statement"]}
            )
        other_topics_view = [
            {
                "title": ot["title"],
                "decisions": _decisions_by_topic.get(ot["topic_id"], []),
            }
            for ot in all_topics
            if ot["topic_id"] != topic_id
        ]

        from .agents.tiers import (  # noqa: PLC0415
            ModelTier,
            credit_multiplier,
            parse_tier,
            resolve_tier_for_user,
            select_tier_after_cap_check,
            tier_to_adapter,
            tier_to_claude_model,
            tier_to_openai_model,
        )
        resolved_tier = resolve_tier_for_user(
            _store, user["user_id"], parse_tier(body.model_tier),
        )

        # #080 cap check (mirrors v2_topic_turn).
        plan_slug_for_caps_stream = (
            (_store.get_subscription(user_id=user["user_id"]) or {}).get("plan")
            or "free"
        )
        effective_tier_stream, fell_back_from_stream = select_tier_after_cap_check(
            _store, user["user_id"], plan_slug_for_caps_stream, resolved_tier,
        )
        if effective_tier_stream is None:
            raise HTTPException(
                status_code=429,
                detail={
                    "error": "monthly_cap_reached",
                    "tier": resolved_tier.value,
                    "plan_slug": plan_slug_for_caps_stream,
                },
            )
        if (
            fell_back_from_stream is not None
            and fell_back_from_stream is not effective_tier_stream
        ):
            response.headers["X-Inspira-Llm-Tier-Fallback"] = (
                f"{fell_back_from_stream.value}->{effective_tier_stream.value}"
            )
        resolved_tier = effective_tier_stream

        openai_adapter_instance = _require_adapter()
        claude_adapter_instance = (
            _get_claude_adapter()
            if resolved_tier in (ModelTier.FRONTIER, ModelTier.ENTERPRISE)
            else None
        )
        selected_adapter = tier_to_adapter(
            resolved_tier,
            openai_adapter=openai_adapter_instance,
            claude_adapter=claude_adapter_instance,
        )
        is_claude = (
            selected_adapter is claude_adapter_instance
            and claude_adapter_instance is not None
        )
        model_override = (
            tier_to_claude_model(resolved_tier)
            if is_claude
            else tier_to_openai_model(resolved_tier)
        )

        from . import byok as byok_module  # noqa: PLC0415

        byok_provider = "anthropic" if is_claude else "openai"
        byok_key = _try_get_user_byok(
            byok_module, user["user_id"], byok_provider,
        )
        is_byok = byok_key is not None
        llm_mode_header = "byok" if is_byok else "house"

        validated_locale = _validate_locale(body.locale)
        attached_payload = [s.model_dump() for s in body.attached_sources] or None
        attached_excerpts_text = " ".join(
            s.excerpt or "" for s in body.attached_sources
        )

        async def _turn_generator():
            import asyncio  # noqa: PLC0415

            # P1.7 — localize.
            yield format_sse(
                "heartbeat",
                {
                    "status": "thinking",
                    "message": thinking_message(
                        "turn.reading_thread", validated_locale,
                    ),
                },
            )

            try:
                loop = asyncio.get_running_loop()
                # Mirror the kickoff generator's progressive-heartbeat
                # loop. Without it the client stares at a single static
                # "Thinking…" line for the entire LLM roundtrip — which
                # on frontier tiers is 15-40s and feels like a hang.
                # Per-tier reasoning effort + adapter timeout (mirrors
                # v2_topic_turn). BASE → low/15s; PRO/FRONTIER → None/60s.
                from .agents.tiers import (  # noqa: PLC0415
                    tier_to_reasoning_effort,
                    tier_to_timeout_s,
                )
                _stream_reasoning_effort = tier_to_reasoning_effort(resolved_tier)
                _stream_timeout_s = tier_to_timeout_s(resolved_tier)
                turn_task = loop.run_in_executor(
                    None,
                    lambda: selected_adapter.topic_turn(
                        current_topic=current_topic_view,
                        other_topics=other_topics_view,
                        sources=attached_payload,
                        locale=validated_locale,
                        model_override=model_override,
                        api_key_override=byok_key,
                        reasoning_effort=_stream_reasoning_effort,
                        timeout_s=_stream_timeout_s,
                    ),
                )
                # Same rework as the kickoff progress script — see the
                # 2026-04-24 comment block above on the kickoff loop.
                # Replace the looped "Almost ready…" (which lied when
                # the LLM hung) with a ramp that becomes progressively
                # honest about slow turns, and enforce a hard-stop at
                # ~90s to escape forever-spinners.
                # P1.7 — keys, localized at emit time below.
                _turn_progress_script = (
                    "turn.weighing_options",     # 0s
                    "turn.framing_question",     # 3s
                    "turn.drafting_response",    # 6s
                    "turn.polishing_phrasing",   # 9s
                    "common.taking_a_moment",    # 12s
                    "common.still_working",      # 15s
                    "common.still_working_long",  # 18s
                )
                # Timeout hardening: 30 ticks × 3s = 90s hard cap.
                # Reverted from the tactical 10-tick (30s) hedge;
                # W2/F5 ingestion + worker is the structural fix
                # and now in place.
                _TURN_HARD_TIMEOUT_TICKS = 30
                step = 0
                while not turn_task.done():
                    if step >= _TURN_HARD_TIMEOUT_TICKS:
                        turn_task.cancel()
                        yield format_sse(
                            "error",
                            {
                                "code": "planner_timeout",
                                "message": (
                                    "The planner took too long to respond. "
                                    "Please try again."
                                ),
                                "elapsed_s": step * 3,
                            },
                        )
                        return
                    try:
                        await asyncio.wait_for(
                            asyncio.shield(turn_task),
                            timeout=3.0,
                        )
                    except asyncio.TimeoutError:
                        # P1.7 — localize at emit time.
                        key = _turn_progress_script[
                            min(step, len(_turn_progress_script) - 1)
                        ]
                        yield format_sse(
                            "heartbeat",
                            {
                                "status": "thinking",
                                "message": thinking_message(
                                    key, validated_locale,
                                ),
                                # See the kickoff stream comment for
                                # why elapsed_s lands on heartbeats.
                                "elapsed_s": step * 3,
                            },
                        )
                        step += 1
                turn_result = turn_task.result()
            except RuntimeError as exc:
                rid_exc = _planner_error_response(exc)
                detail = (
                    rid_exc.detail if isinstance(rid_exc.detail, dict)
                    else {"error": "planner_call_failed"}
                )
                yield format_sse(
                    "error",
                    {
                        "code": "planner_error",
                        "message": "The planner failed to respond.",
                        "detail": detail,
                    },
                )
                return
            except Exception as exc:  # noqa: BLE001
                # PR 2: credits are gone — just log + emit error.
                logger.exception(
                    "[v2_topic_turn_stream] adapter.topic_turn failed",
                )
                yield format_sse(
                    "error",
                    {
                        "code": "planner_error",
                        "message": str(exc) or "Unexpected planner failure",
                    },
                )
                return

            try:
                _record_llm_usage(
                    user,
                    prompt_text=(user_answer + " " + attached_excerpts_text),
                    response_text=str(turn_result),
                )

                # #080: bump the per-tier monthly counter (mirrors
                # v2_topic_turn). Estimate output tokens via chars/4.
                _stream_tier_tokens_out = (
                    max(0, len(str(turn_result))) // _ESTIMATE_CHARS_PER_TOKEN
                )
                try:
                    _store.increment_tier_usage(
                        user_id=user["user_id"],
                        tier=resolved_tier.value,
                        tokens=_stream_tier_tokens_out,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "[stream] increment_tier_usage failed user=%s tier=%s tokens=%d: %s",
                        user.get("user_id"), resolved_tier.value,
                        _stream_tier_tokens_out, exc,
                    )

                planned_checkpoints = turn_result.get("planned_checkpoints")
                checkpoint_updates = turn_result.get("checkpoint_updates")
                # Streaming variant: same defense as the non-streaming
                # path at line 2802. Only honour planned_checkpoints when
                # the topic has none yet (true first turn). When the LLM
                # re-emits planned_checkpoints on a topic that already
                # has tracked checkpoints, log + preserve the existing
                # outline. (E2E 2026-04-25 #2/#3)
                if planned_checkpoints and not existing_checkpoints:
                    merged_checkpoints = [
                        {
                            "id": cp["id"], "question": cp["question"],
                            "status": "open", "answered_in_turn_id": None,
                        }
                        for cp in planned_checkpoints
                    ]
                elif planned_checkpoints and existing_checkpoints:
                    logger.warning(
                        "topic_turn (stream): ignored planned_checkpoints "
                        "(%d items) on a topic with %d existing checkpoints; "
                        "preserving existing outline. checkpoint_updates=%s",
                        len(planned_checkpoints), len(existing_checkpoints),
                        "present" if checkpoint_updates else "absent",
                    )
                    if checkpoint_updates:
                        update_map = {u["id"]: u["status"] for u in checkpoint_updates}
                        merged_checkpoints = []
                        for cp in existing_checkpoints:
                            if cp["id"] in update_map:
                                merged_checkpoints.append({**cp, "status": update_map[cp["id"]]})
                            else:
                                merged_checkpoints.append(cp)
                    else:
                        merged_checkpoints = existing_checkpoints
                elif checkpoint_updates:
                    update_map = {u["id"]: u["status"] for u in checkpoint_updates}
                    merged_checkpoints = []
                    for cp in existing_checkpoints:
                        if cp["id"] in update_map:
                            new_status = update_map[cp["id"]]
                            answered_in = cp.get("answered_in_turn_id")
                            merged_checkpoints.append({
                                **cp, "status": new_status,
                                "answered_in_turn_id": answered_in,
                            })
                        else:
                            merged_checkpoints.append(cp)
                else:
                    merged_checkpoints = existing_checkpoints

                # Heuristic safety net (mirrors v2_topic_turn): auto-mark
                # first open checkpoint as answered when LLM omitted
                # checkpoint_updates and the user answered substantively.
                # See E2E 2026-04-25 #2 follow-up.
                if (
                    user_answer
                    and not checkpoint_updates
                    and existing_checkpoints
                    and _user_reply_is_substantive(user_answer)
                    and merged_checkpoints
                ):
                    _open_idxs = [
                        i for i, cp in enumerate(merged_checkpoints)
                        if cp.get("status") == "open"
                    ]
                    if _open_idxs:
                        _i = _open_idxs[0]
                        _target_id = merged_checkpoints[_i].get("id")
                        merged_checkpoints[_i] = {
                            **merged_checkpoints[_i],
                            "status": "answered",
                            "answered_in_turn_id": None,
                        }
                        if _target_id:
                            checkpoint_updates = [
                                {"id": _target_id, "status": "answered"}
                            ]

                # Product feedback: proposed_decisions
                # auto-synthesis removed for the same reason as the
                # non-streaming branch above (LLM is authoritative;
                # synthesis was capturing short replies verbatim and
                # cluttering the DECISIONS panel with non-decisions).

                all_answered = (
                    len(merged_checkpoints) > 0
                    and all(
                        cp.get("status") == "answered"
                        for cp in merged_checkpoints
                    )
                )
                if all_answered and turn_result.get("action") != "suggest_close":
                    topic_title = topic.get("title") or "this topic"
                    turn_result["action"] = "suggest_close"
                    turn_result["question"] = (
                        f"We've covered every checkpoint on {topic_title} — "
                        "want to close this out?"
                    )
                    turn_result["why_this_matters"] = None
                    turn_result["suggested_responses"] = [
                        {"label": "Close the topic \u2192", "intent": "close"},
                        {"label": "I want to keep going \u2192", "intent": "continue"},
                    ]

                planner_turn = None
                if turn_result.get("action") != "suggest_close":
                    planner_turn = _store.append_qna_turn(
                        topic_id=topic_id,
                        project_id=project_id,
                        role="planner",
                        body=turn_result.get("question") or "",
                        status="open",
                        why_this_matters=turn_result.get("why_this_matters"),
                        action=turn_result.get("action"),
                        suggested_responses=turn_result.get("suggested_responses") or [],
                        user_id=user["user_id"],
                    )

                if (planned_checkpoints or checkpoint_updates) and planner_turn:
                    for cp in merged_checkpoints:
                        if (
                            cp.get("status") == "answered"
                            and not cp.get("answered_in_turn_id")
                        ):
                            cp["answered_in_turn_id"] = planner_turn["turn_id"]

                if planned_checkpoints or checkpoint_updates:
                    _store.update_topic_checkpoints(
                        topic_id, user["user_id"], merged_checkpoints,
                    )

                rerouted_decisions: list[dict[str, Any]] = []
                for proposal in (turn_result.get("proposed_decisions") or []):
                    raw_target = (
                        proposal.get("target_topic_title") or ""
                    ).strip()
                    target_title_lower = raw_target.lower()
                    resolve_topic_id = (
                        _sibling_title_to_id.get(target_title_lower)
                        if raw_target else None
                    )
                    dest_topic_id = (
                        resolve_topic_id if resolve_topic_id else topic_id
                    )
                    created = _store.create_decision(
                        topic_id=dest_topic_id,
                        project_id=project_id,
                        statement=proposal.get("statement", ""),
                        rationale=proposal.get("rationale"),
                        source_turn_id=proposal.get("extracted_from_turn_id"),
                        proposed_by="planner",
                        status="proposed",
                        user_id=user["user_id"],
                    )
                    if resolve_topic_id and resolve_topic_id != topic_id:
                        rerouted_decisions.append({
                            "decision_id": created["decision_id"],
                            "original_topic_id": topic_id,
                            "actual_topic_id": resolve_topic_id,
                            "actual_topic_title": raw_target,
                        })

                created_topic_payload: dict[str, Any] | None = None
                ntp = turn_result.get("new_topic_proposal")
                if ntp:
                    proposed_lower = (ntp.get("title") or "").strip().lower()
                    sibling_titles_lower = {
                        (ot.get("title") or "").strip().lower()
                        for ot in all_topics
                        if ot.get("topic_id") != topic_id
                    }
                    if not proposed_lower or proposed_lower in sibling_titles_lower:
                        ntp = None
                if ntp:
                    try:
                        all_topics_for_pos = _store.list_topics(
                            project_id=project_id, user_id=user["user_id"],
                        )
                        if all_topics_for_pos:
                            sum_x = sum(
                                float(t.get("position_x") or 0)
                                for t in all_topics_for_pos
                            )
                            sum_y = sum(
                                float(t.get("position_y") or 0)
                                for t in all_topics_for_pos
                            )
                            pos_x = sum_x / len(all_topics_for_pos) + 80.0
                            pos_y = sum_y / len(all_topics_for_pos) + 80.0
                        else:
                            pos_x, pos_y = 0.0, 0.0
                        new_topic = _store.create_topic(
                            project_id=project_id,
                            title=ntp["title"],
                            icon=ntp.get("icon", "lightbulb"),
                            position_x=pos_x,
                            position_y=pos_y,
                            origin="planner_proposed",
                            metadata={"why_this_topic": ntp.get("why", "")},
                            user_id=user["user_id"],
                        )
                        sibling_topics = [
                            t for t in _store.list_topics(
                                project_id=project_id, user_id=user["user_id"],
                            )
                            if t.get("topic_id") != new_topic.get("topic_id")
                        ]
                        try:
                            adapter_al = _get_auto_link_adapter()
                            al_proposals = adapter_al.propose_links(
                                new_topic=new_topic,
                                existing_topics=sibling_topics,
                            )
                            new_relationships = _resolve_auto_link_proposals(
                                new_topic=new_topic,
                                existing_topics=sibling_topics,
                                proposals=al_proposals,
                                project_id=project_id,
                                user=user,
                            )
                            _record_llm_usage(
                                user,
                                prompt_text=new_topic.get("title", "") + " ".join(
                                    t.get("title", "") for t in sibling_topics
                                ),
                                response_text=str(al_proposals),
                            )
                        except Exception as exc:  # noqa: BLE001
                            logger.warning(
                                "auto_link after topic_turn_stream new_topic_proposal failed: %s",
                                exc,
                            )
                            new_relationships = []
                        created_topic_payload = {
                            "topic": new_topic,
                            "relationships": new_relationships,
                        }
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "Failed to auto-persist new_topic_proposal from topic_turn_stream: %s",
                            exc,
                        )

                deletion_suggestion = turn_result.get("topic_deletion_suggestion")
                if deletion_suggestion:
                    ds_target_id = (
                        deletion_suggestion.get("target_topic_id") or ""
                    ).strip()
                    ds_target_title = (
                        deletion_suggestion.get("target_topic_title") or ""
                    ).strip().lower()
                    ds_reason = (
                        deletion_suggestion.get("reason") or ""
                    ).strip()
                    sibling_ids = {
                        ot.get("topic_id") for ot in all_topics
                        if ot.get("topic_id") != topic_id
                    }
                    sibling_titles_lower = {
                        (ot.get("title") or "").strip().lower()
                        for ot in all_topics
                        if ot.get("topic_id") != topic_id
                    }
                    if (
                        ds_target_id == topic_id
                        or not ds_target_id
                        or not ds_reason
                        or (
                            ds_target_id not in sibling_ids
                            and ds_target_title not in sibling_titles_lower
                        )
                    ):
                        deletion_suggestion = None

                envelope = {
                    "turn_result": turn_result,
                    "planner_turn": planner_turn,
                    "rerouted_decisions": rerouted_decisions,
                    "checkpoints": merged_checkpoints,
                    "created_topic": created_topic_payload,
                    "topic_deletion_suggestion": deletion_suggestion,
                }
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "[v2_topic_turn_stream] post-LLM persistence failed",
                )
                yield format_sse(
                    "error",
                    {"code": "planner_error", "message": str(exc)},
                )
                return

            yield format_sse("complete", envelope)

        return sse_stream(
            _turn_generator(),
            extra_headers={"X-Inspira-Llm-Mode": llm_mode_header},
        )

    # -----------------------------------------------------------------------
    # v2 — document generator (#094 / Item 3 redesign — replaces #092 BP pager)
    # -----------------------------------------------------------------------
    # 7 doc types in v1 (business_plan / prd / story_outline / event_plan /
    # marketing_plan / research_proposal / course_outline), all derived from
    # project.metadata.domain via DOMAIN_TO_DOC_TYPE. One-shot async generation
    # mirroring #089's 202-+-poll + BackgroundTask + advisory-lock pattern.
    # Cap shares the existing business_plan_usage table
    # — Pro 1 doc/mo (any type), Frontier 100/mo. Strict-block
    # ANY POST when at limit; increment ONLY on first generation of a new
    # (project_id, doc_type) pair.

    def _document_to_view(doc: dict[str, Any]) -> dict[str, Any]:
        """Convert a store ``documents`` row to the API view.

        Parses content_json to a dict on the way out so the FE consumer
        doesn't have to JSON.parse a string in a string. Drops
        ``user_id`` — the FE doesn't need it (the ownership check
        happened upstream).
        """
        import json as _json  # noqa: PLC0415

        content_obj: dict[str, Any] | None = None
        raw = doc.get("content_json")
        if raw:
            try:
                content_obj = _json.loads(raw)
            except (ValueError, TypeError):
                logger.warning(
                    "[document] failed to parse content_json for "
                    "document_id=%s — surfacing as null content",
                    doc.get("document_id"),
                )
                content_obj = None
        return {
            "document_id": doc.get("document_id"),
            "project_id": doc.get("project_id"),
            "doc_type": doc.get("doc_type"),
            "status": doc.get("status"),
            "content": content_obj,
            "error_message": doc.get("error_message"),
            "model_id": doc.get("model_id"),
            "plan_tier": doc.get("plan_tier"),
            "output_tokens_estimate": doc.get("output_tokens_estimate"),
            "generated_at": doc.get("generated_at"),
            "completed_at": doc.get("completed_at"),
        }

    def _should_count_document_start(*, has_existing_completed: bool) -> bool:
        """Option C predicate: does THIS POST start a NEW document?

        Returns True iff there's no pre-existing completed document for
        ``(project_id, doc_type)`` at the time of the call. The cap
        counter (``business_plan_usage``) increments only when this
        returns True. The strict-block cap-gate that fires before this
        check is independent — it 429s any POST when the user is
        already at cap. Failed / orphan in-progress rows do NOT count
        as "existing"; the user's first SUCCESSFUL doc is the trigger.
        """
        return not has_existing_completed

    def _adapter_method_for_doc_type(adapter: Any, doc_type: str) -> Any:
        """Dispatch table from doc_type → bound adapter method.

        Raises ``KeyError`` on unknown doc_type — the endpoint's
        domain-mapping gate ensures we never reach this with an
        invalid value. All 7 methods share the same signature
        (topics + decisions + domain + locale + project_title) and
        all delegate to the shared ``_generate_document`` engine
        pinned to gpt-5.5.
        """
        return {
            "business_plan": adapter.business_plan,
            "prd": adapter.prd,
            "story_outline": adapter.story_outline,
            "event_plan": adapter.event_plan,
            "marketing_plan": adapter.marketing_plan,
            "research_proposal": adapter.research_proposal,
            "course_outline": adapter.course_outline,
        }[doc_type]

    def _resolve_doc_type_for_project(project_id: str) -> str:
        """Read project.metadata.domain and map to a doc_type.

        Raises HTTPException(422) if the domain is missing, unknown,
        or unmapped (career, personal in v1). Mirrors the user-facing
        contract: the FE doesn't pick a doc type — the project's domain
        determines it. If the user disagrees they change the domain
        (separate flow).
        """
        from .store import DOMAIN_TO_DOC_TYPE  # noqa: PLC0415

        project_row = _store._get_v2_project(project_id)  # noqa: SLF001
        domain = None
        if project_row is not None:
            domain = (project_row.get("metadata") or {}).get("domain")
        if not domain or domain not in DOMAIN_TO_DOC_TYPE:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "domain_not_supported",
                    "feature": "document",
                    "domain": domain,
                    "supported_domains": sorted(DOMAIN_TO_DOC_TYPE.keys()),
                },
            )
        return DOMAIN_TO_DOC_TYPE[domain]

    def _run_document_generation(
        document_id: str,
        project_id: str,
        user_id: str,
        doc_type: str,
        locale: str | None,
        is_new_doc_start: bool,
    ) -> None:
        """BackgroundTask body — the gpt-5.5 doc-type generation.

        Runs after POST /api/v2/projects/{project_id}/document/generate
        returns 202. The document row is already in_progress at endpoint
        time; this fn flips it to completed (with content + token
        estimate) or failed (with error message). The FE poller picks
        up the transition on its next GET.

        Product decision: all 7 doc-type calls pin to
        MODEL_BUSINESS_PLAN ("gpt-5.5") + 60s timeout (set in the
        adapter); NO model_override, NO api_key_override, NO
        reasoning_effort overrides at this layer. House OpenAI key
        only. Increment ``business_plan_usage`` ONLY if
        ``is_new_doc_start`` (first generation of this
        ``(project_id, doc_type)`` pair). Regenerates of an existing
        completed doc don't increment — Option C semantics from #092.

        Errors here MUST NOT bubble out — FastAPI's BackgroundTasks
        runs this in the same worker after the response is sent, so an
        unhandled exception would log silently and orphan the
        in_progress row. We catch broadly and write a failure payload.
        """
        import json as _json  # noqa: PLC0415
        import time as _time  # noqa: PLC0415

        logger.info(
            "[document_bg] start document_id=%s project=%s doc_type=%s "
            "locale=%s is_new=%s",
            document_id, project_id, doc_type, locale, is_new_doc_start,
        )

        try:
            # 1. Load topics + per-topic decisions for the LLM context.
            topics_for_llm: list[dict[str, Any]] = []
            try:
                all_topics = _store.list_topics(
                    project_id=project_id, user_id=user_id,
                )
                all_decisions = _store.list_decisions(
                    project_id=project_id, user_id=user_id,
                )
                decisions_by_topic: dict[str, list[dict[str, Any]]] = {}
                for d in all_decisions:
                    decisions_by_topic.setdefault(d["topic_id"], []).append(
                        {"statement": d.get("statement", "")}
                    )
                for t in all_topics:
                    topics_for_llm.append({
                        "title": t.get("title", ""),
                        "decisions": decisions_by_topic.get(t["topic_id"], []),
                    })
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "[document_bg] failed to load project state "
                    "document_id=%s project=%s: %s",
                    document_id, project_id, exc,
                )
                _store.mark_document_failed(
                    document_id=document_id,
                    error_message=f"project_state_load_failed: {type(exc).__name__}",
                )
                return

            logger.info(
                "[document_bg] state_loaded document_id=%s topics=%d decisions=%d",
                document_id, len(topics_for_llm),
                sum(len(v) for v in decisions_by_topic.values()),
            )

            if not topics_for_llm:
                _store.mark_document_failed(
                    document_id=document_id,
                    error_message="empty_project_no_topics",
                )
                return

            # 2. Project metadata — domain + title for the prompt.
            project_row = _store._get_v2_project(project_id)  # noqa: SLF001
            project_title: str | None = None
            domain: str | None = None
            if project_row is not None:
                project_title = project_row.get("title")
                domain = (project_row.get("metadata") or {}).get("domain")

            # 3. Run the LLM call. Direct OpenAI adapter — Document is
            # OpenAI-only by product decision, NOT routed through
            # tier_to_adapter. All 7 doc-type methods share the same
            # signature and pin to gpt-5.5.
            adapter = _require_adapter()
            try:
                method = _adapter_method_for_doc_type(adapter, doc_type)
            except KeyError:
                # Should never happen — POST gate filters invalid
                # doc_type before scheduling. Be loud if it does.
                logger.error(
                    "[document_bg] unknown doc_type=%s document_id=%s "
                    "(POST gate should have caught this)",
                    doc_type, document_id,
                )
                _store.mark_document_failed(
                    document_id=document_id,
                    error_message=f"invalid_doc_type: {doc_type}",
                )
                return

            _doc_bg_t0 = _time.monotonic()
            logger.info(
                "[document_bg] adapter_call_start document_id=%s doc_type=%s "
                "model=gpt-5.5",
                document_id, doc_type,
            )

            try:
                result = method(
                    topics=topics_for_llm,
                    decisions=None,  # per-topic decisions cover the surface
                    domain=domain,
                    locale=locale,
                    project_title=project_title,
                )
            except RuntimeError as exc:
                logger.warning(
                    "[document_bg] sanitizer raised RuntimeError "
                    "document_id=%s doc_type=%s elapsed_s=%.1f: %s",
                    document_id, doc_type, _time.monotonic() - _doc_bg_t0, exc,
                )
                _store.mark_document_failed(
                    document_id=document_id,
                    error_message=f"sanitizer_failed: {type(exc).__name__}",
                )
                return
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "[document_bg] adapter call failed document_id=%s "
                    "doc_type=%s elapsed_s=%.1f: %s",
                    document_id, doc_type, _time.monotonic() - _doc_bg_t0, exc,
                )
                _store.mark_document_failed(
                    document_id=document_id,
                    error_message=f"adapter_failed: {type(exc).__name__}",
                )
                return

            logger.info(
                "[document_bg] adapter_call_ok document_id=%s doc_type=%s "
                "elapsed_s=%.1f sections=%d",
                document_id, doc_type,
                _time.monotonic() - _doc_bg_t0,
                len(result.get("sections", [])),
            )

            # 4. Persist. Strip the sanitizer-internal _sanitize key
            # from the result; only doc_type + sections are user-facing.
            persisted = {
                "doc_type": result.get("doc_type", doc_type),
                "sections": result.get("sections", []),
            }
            content_json = _json.dumps(persisted, ensure_ascii=False)
            output_tokens_estimate = max(
                0, len(content_json) // _ESTIMATE_CHARS_PER_TOKEN,
            )
            _store.mark_document_completed(
                document_id=document_id,
                content_json=content_json,
                output_tokens_estimate=output_tokens_estimate,
            )

            # 5. Conditional cap increment: only
            # when this is the user's first generation of this
            # (project, doc_type) pair. Failures logged but don't
            # propagate — the document is already saved; counter drift
            # on a 5xx write is preferable to losing the user's
            # generation.
            if is_new_doc_start:
                try:
                    _store.increment_business_plan_usage(
                        user_id=user_id,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "[document_bg] increment_business_plan_usage "
                        "failed user=%s document_id=%s: %s",
                        user_id, document_id, exc,
                    )

            logger.info(
                "[document_bg] complete document_id=%s elapsed_s=%.1f "
                "cap_incremented=%s",
                document_id, _time.monotonic() - _doc_bg_t0,
                is_new_doc_start,
            )
        except Exception as exc:  # noqa: BLE001
            # Catch-all so a BackgroundTask exception never silently
            # orphans an in_progress row. The 5-minute stale-orphan
            # guard in get_in_flight_document is the last line of
            # defence; this catch-all is the first.
            logger.exception(
                "[document_bg] catch-all failure document_id=%s: %s",
                document_id, exc,
            )
            try:
                _store.mark_document_failed(
                    document_id=document_id,
                    error_message=f"unexpected_failure: {type(exc).__name__}",
                )
            except Exception:  # noqa: BLE001
                logger.exception(
                    "[document_bg] even mark_failed errored — orphan "
                    "row remains; the 5-minute stale guard in "
                    "get_in_flight_document will free the project for "
                    "a fresh generation.",
                )

    @app.post(
        "/api/v2/projects/{project_id}/document/generate",
        status_code=202,
        tags=["v2", "document"],
    )
    def v2_document_generate(
        project_id: str,
        background_tasks: BackgroundTasks,
        body: DocumentGenerateBody = DocumentGenerateBody(),
        user: dict = Depends(_current_user),
    ) -> dict[str, Any]:
        """Kick off an async document generation. Returns 202 + document_id.

        Flow:

        1. Verify project ownership (404).
        2. Plan gate: Free → 402 ``plan_required``. Pro/Frontier proceed.
        3. Resolve doc_type:
           - If body.doc_type provided, validate against VALID_DOC_TYPES
             (422 ``invalid_doc_type`` if not in the allowlist) and use it.
           - Else derive from project.metadata.domain (422 if unmapped).
           This is the #094 follow-up override path — lets the user
           correct a misidentified domain without writing back to
           project metadata. The project's persisted domain stays
           untouched. (Persistent override tracked as #097.)
        4. Strict cap gate: 429 if business_plan_usage >= cap (Pro 1, Frontier
           100). Blocks regenerates too.
        5. Compute is_new_doc_start (no prior completed doc for this pair).
        6. Per-project advisory lock + in-flight idempotency:
           - In-flight already? Return its document_id + already_in_flight=True.
           - Lost lock + no in-flight? 409 (caller retry).
           - Otherwise create_document_in_progress + schedule BackgroundTask.
        7. Return 202 + {document_id, status: "in_progress"}.
        """
        from .store import VALID_DOC_TYPES  # noqa: PLC0415
        from .agents.tiers import get_business_plan_cap  # noqa: PLC0415
        from .billing.plans import get_plan  # noqa: PLC0415
        from .locks import try_project_advisory_lock  # noqa: PLC0415

        _require_owned_project(project_id, user)

        # Plan gate (Pro+ only). Reuses the allow_business_plan flag —
        # both Item 3 (#092) and Item 3 redesign (#094) share the same
        # cap counter (business_plan_usage) by design. The
        # business_plan_usage → document_usage rename is deferred to #095.
        plan_slug = (
            (_store.get_subscription(user_id=user["user_id"]) or {}).get("plan")
            or "free"
        )
        plan = get_plan(plan_slug)
        if plan is None or not plan.limits.allow_business_plan:
            raise HTTPException(
                status_code=402,
                detail={
                    "error": "plan_required",
                    "feature": "document",
                    "min_plan": "pro",
                },
            )

        # Resolve doc_type — explicit override (FE picker, #094 follow-up)
        # takes precedence over the project.metadata.domain derivation.
        if body.doc_type is not None:
            override = body.doc_type.strip()
            if override not in VALID_DOC_TYPES:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "error": "invalid_doc_type",
                        "feature": "document",
                        "doc_type": override,
                    },
                )
            doc_type = override
        else:
            # No override — derive from project domain (422 if unmapped).
            doc_type = _resolve_doc_type_for_project(project_id)

        # Strict cap gate: 429 ANY POST when at cap,
        # regenerates included. The increment side is nuanced (only on
        # NEW doc start) but the gate is simple.
        cap = get_business_plan_cap(plan_slug)
        try:
            usage = _store.get_business_plan_usage(user_id=user["user_id"])
            used = int(usage.get("plans_used_this_month", 0))
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[document] cap-counter read failed user=%s: %s — "
                "letting through",
                user["user_id"], exc,
            )
            used = 0
        if used >= cap:
            raise HTTPException(
                status_code=429,
                detail={
                    "error": "document_limit_reached",
                    "feature": "document",
                    "plan_slug": plan_slug,
                    "doc_type": doc_type,
                    "current_count": used,
                    "cap": cap,
                    "min_plan": "team",
                },
            )

        # Predicate: is THIS call the user's first generation of this
        # (project, doc_type)? Read the truth pre-call so the
        # BackgroundTask sees the right is_new_doc_start, not a value
        # tainted by the in-progress row we're about to insert.
        existing_completed = _store.get_latest_completed_document(
            project_id=project_id, doc_type=doc_type,
        )
        is_new_doc_start = _should_count_document_start(
            has_existing_completed=existing_completed is not None,
        )

        # Per-project advisory lock + in-flight idempotency check inside
        # the lock catches the case where two browser tabs each click
        # Generate — only one BackgroundTask should run; both tabs poll
        # the same document_id.
        validated_locale = _validate_locale(body.locale)
        with try_project_advisory_lock(_store, project_id) as acquired:
            existing_inflight = _store.get_in_flight_document(
                project_id=project_id, doc_type=doc_type,
            )
            if existing_inflight is not None:
                return {
                    "document_id": existing_inflight["document_id"],
                    "status": "in_progress",
                    "already_in_flight": True,
                }

            if not acquired:
                # Lost the advisory-lock race AND no in-flight visible
                # yet (winner's write hasn't committed). Tell the
                # client to retry — by then the in-flight read will
                # succeed and they'll join the existing generation.
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error": "document_already_in_flight",
                        "feature": "document",
                        "doc_type": doc_type,
                    },
                )

            document_id = _store.create_document_in_progress(
                project_id=project_id,
                user_id=user["user_id"],
                doc_type=doc_type,
                plan_tier=plan_slug,
                model_id="gpt-5.5",
            )

        # Lock released. Schedule the LLM call to run after the
        # response is sent (FastAPI's BackgroundTasks runs in the same
        # worker, post-return).
        background_tasks.add_task(
            _run_document_generation,
            document_id, project_id, user["user_id"], doc_type,
            validated_locale, is_new_doc_start,
        )
        return {"document_id": document_id, "status": "in_progress"}

    @app.get(
        "/api/v2/projects/{project_id}/document/{document_id}",
        tags=["v2", "document"],
    )
    def v2_document_get_by_id(
        project_id: str,
        document_id: str,
        user: dict = Depends(_current_user),
    ) -> dict[str, Any]:
        """Fetch a specific document by id (FE poll endpoint).

        Ownership-checked: 404 if the project isn't the user's, AND
        404 if the document's user_id doesn't match the requester.
        Both return the same opaque error so an attacker can't
        distinguish "wrong project" from "wrong user".
        """
        _require_owned_project(project_id, user)
        doc = _store.get_document(document_id=document_id)
        if (
            doc is None
            or doc["project_id"] != project_id
            or doc["user_id"] != user["user_id"]
        ):
            raise HTTPException(
                status_code=404,
                detail={"error": "document_not_found"},
            )
        return _document_to_view(doc)

    @app.get(
        "/api/v2/projects/{project_id}/document",
        tags=["v2", "document"],
    )
    def v2_document_get_latest(
        project_id: str,
        doc_type: str | None = None,
        user: dict = Depends(_current_user),
    ) -> dict[str, Any]:
        """Fetch the latest completed document for a project (tab open).

        If ``doc_type`` is omitted, derives it from project.metadata.domain
        (422 if unmapped — same gate as POST /generate). If specified,
        validated against ``VALID_DOC_TYPES``. 404 with
        ``document_not_found`` if no completed document exists yet.
        """
        from .store import VALID_DOC_TYPES  # noqa: PLC0415

        _require_owned_project(project_id, user)

        if doc_type is not None:
            if doc_type not in VALID_DOC_TYPES:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "error": "invalid_doc_type",
                        "feature": "document",
                        "expected": sorted(VALID_DOC_TYPES),
                        "got": doc_type,
                    },
                )
            resolved = doc_type
        else:
            resolved = _resolve_doc_type_for_project(project_id)

        doc = _store.get_latest_completed_document(
            project_id=project_id, doc_type=resolved,
        )
        if doc is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": "document_not_found",
                    "doc_type": resolved,
                },
            )
        return _document_to_view(doc)

    @app.patch(
        "/api/v2/projects/{project_id}/document/{document_id}/section/{section_id}",
        tags=["v2", "document"],
    )
    def v2_document_section_edit(
        project_id: str,
        document_id: str,
        section_id: str,
        body: DocumentSectionPatchBody,
        user: dict = Depends(_current_user),
    ) -> dict[str, Any]:
        """Save a user inline edit on one section.

        No LLM. No cap. No advisory lock (single-row UPDATE, Postgres-
        row-serialized). Plan-gate still applies as defense-in-depth —
        Free can't have rows in the first place but the gate is
        consistent. JSON-merges title/prose_markdown into the
        document's ``content.sections[*]`` entry matched on
        ``section_id``; 404 if no such section exists in the document.
        """
        import json as _json  # noqa: PLC0415

        from .billing.plans import get_plan  # noqa: PLC0415

        _require_owned_project(project_id, user)

        # Plan-gate (defense-in-depth — Free can't have rows here anyway).
        plan_slug = (
            (_store.get_subscription(user_id=user["user_id"]) or {}).get("plan")
            or "free"
        )
        plan = get_plan(plan_slug)
        if plan is None or not plan.limits.allow_business_plan:
            raise HTTPException(
                status_code=402,
                detail={
                    "error": "plan_required",
                    "feature": "document",
                    "min_plan": "pro",
                },
            )

        # Body must specify at least one field — pydantic alone can't
        # express "at least one of N optional fields present". 422 with
        # an explicit error code so the FE renders a useful toast.
        if body.title is None and body.prose_markdown is None:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "empty_patch",
                    "feature": "document",
                    "hint": "specify at least one of: title, prose_markdown",
                },
            )

        # Document lookup with ownership check (mirror GET-by-id).
        doc = _store.get_document(document_id=document_id)
        if (
            doc is None
            or doc["project_id"] != project_id
            or doc["user_id"] != user["user_id"]
        ):
            raise HTTPException(
                status_code=404,
                detail={"error": "document_not_found"},
            )

        # Parse content_json. A missing / unparseable content_json
        # blocks the edit — the user is editing a document that has no
        # content yet (in_progress that hasn't completed) or whose
        # content is corrupt. Surface 409 so the FE can suggest reload.
        raw_content = doc.get("content_json")
        if not raw_content:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "document_not_editable",
                    "reason": "no_content_yet",
                    "status": doc.get("status"),
                },
            )
        try:
            content_obj = _json.loads(raw_content)
        except (ValueError, TypeError) as exc:
            logger.warning(
                "[document] PATCH failed to parse content_json for "
                "document_id=%s: %s",
                document_id, exc,
            )
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "document_not_editable",
                    "reason": "content_corrupt",
                },
            ) from exc

        sections = (content_obj or {}).get("sections") or []
        # The sanitizer (agents/openai_adapter.py:_repair_one_doc_section)
        # emits each section with key ``section_id`` — not ``id``. Match
        # against that exact key. Any future re-shape of the persisted
        # content_json must keep this key name in sync with the sanitizer's
        # output, otherwise PATCH silently 404s.
        target = next(
            (s for s in sections if isinstance(s, dict) and s.get("section_id") == section_id),
            None,
        )
        if target is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": "section_not_found",
                    "section_id": section_id,
                },
            )

        # Merge — only fields the caller specified get written.
        if body.title is not None:
            target["title"] = body.title
        if body.prose_markdown is not None:
            target["prose_markdown"] = body.prose_markdown

        new_content_json = _json.dumps(content_obj, ensure_ascii=False)
        updated = _store.update_document_content_json(
            document_id=document_id, content_json=new_content_json,
        )
        if updated is None:
            # Should never happen — get_document just succeeded. Be
            # loud if it does (mirrors the BP "post-write read failed"
            # safety net).
            logger.error(
                "[document] update_document_content_json returned None "
                "but get_document just succeeded document_id=%s",
                document_id,
            )
            raise HTTPException(
                status_code=500,
                detail={"error": "document_post_write_read_failed"},
            )
        return _document_to_view(updated)

    @app.post("/api/v2/topics/{topic_id}/close", tags=["v2"])
    def v2_close_topic(
        topic_id: str, user: dict = Depends(_current_user),
    ) -> dict[str, Any]:
        """Mark a topic as fleshed_out. Called from the suggest_close UI flow."""
        _require_owned_topic(topic_id, user)
        topic = _store.update_topic(
            topic_id, user_id=user["user_id"], status="fleshed_out",
        )
        if topic is None:
            raise HTTPException(status_code=404, detail={"error": "topic_not_found"})
        return {"topic": topic}

    @app.get("/api/v2/topics/{topic_id}/turns", tags=["v2"])
    def v2_list_turns(topic_id: str, user: dict = Depends(_current_user)) -> dict[str, Any]:
        _require_owned_topic(topic_id, user)
        return {"turns": _store.list_qna_turns(topic_id=topic_id, user_id=user["user_id"])}

    # -----------------------------------------------------------------------
    # v2 — auto-link
    # -----------------------------------------------------------------------
    # POST /api/v2/topics/{id}/auto-link proposes relationship rows that
    # connect a topic (typically just-renamed) to other topics in the
    # same project. The frontend re-runs it on demand when a topic's
    # title moves semantically closer to something else.

    def _resolve_auto_link_proposals(
        *,
        new_topic: dict[str, Any],
        existing_topics: list[dict[str, Any]],
        proposals: list[dict[str, Any]],
        project_id: str,
        user: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Persist sanitized auto-link proposals as relationship rows.

        Turns the adapter's title-based proposals into topic-id-based
        relationship rows. Unknown titles are dropped defensively — the
        adapter already filters, but the allowlist is re-checked here
        because a rename could land in between calls.
        """
        title_to_id = {
            (t.get("title") or "").strip(): t.get("topic_id")
            for t in existing_topics
            if t.get("title") and t.get("topic_id")
        }
        persisted: list[dict[str, Any]] = []
        new_topic_id = new_topic.get("topic_id")
        if not new_topic_id:
            return persisted
        for proposal in proposals:
            target_title = (proposal.get("target_topic_title") or "").strip()
            target_id = title_to_id.get(target_title)
            if not target_id:
                continue
            label = (proposal.get("label") or "").strip() or None
            direction = proposal.get("direction")
            if direction == "from_new":
                source_id, sink_id = new_topic_id, target_id
            elif direction == "to_new":
                source_id, sink_id = target_id, new_topic_id
            else:
                continue
            persisted_rel = _store.create_relationship(
                project_id=project_id,
                source_topic_id=source_id,
                target_topic_id=sink_id,
                label=label,
                origin="planner_auto_link",
                user_id=user["user_id"],
            )
            persisted.append(persisted_rel)
        return persisted

    @app.post(
        "/api/v2/topics/{topic_id}/auto-link",
        status_code=status.HTTP_201_CREATED,
        tags=["v2"],
    )
    def v2_auto_link_topic(
        topic_id: str, user: dict = Depends(_current_user),
    ) -> dict[str, Any]:
        topic = _require_owned_topic(topic_id, user)
        _require_token_budget(user)
        project_id = topic["project_id"]
        all_topics = _store.list_topics(
            project_id=project_id, user_id=user["user_id"],
        )
        # Existing topics = everything OTHER than the one we're linking.
        existing = [t for t in all_topics if t.get("topic_id") != topic_id]
        try:
            adapter = _get_auto_link_adapter()
            proposals = adapter.propose_links(
                new_topic=topic,
                existing_topics=existing,
            )
        except RuntimeError as exc:
            raise _planner_error_response(exc)

        _record_llm_usage(
            user,
            prompt_text=(topic.get("title") or "") + " " + " ".join(
                (t.get("title") or "") for t in existing
            ),
            response_text=str(proposals),
        )

        relationships = _resolve_auto_link_proposals(
            new_topic=topic,
            existing_topics=existing,
            proposals=proposals,
            project_id=project_id,
            user=user,
        )
        return {"relationships": relationships}

    # -----------------------------------------------------------------------
    # v2 — artifact-writer modes (summary, outline, dedupe)
    # -----------------------------------------------------------------------
    # Three LLM endpoints that produce artifact-style output from an entire
    # project. Each one:
    # - Requires ownership of the named project (404 otherwise).
    # - Spends against the per-user daily token budget, same gate as
    #   kickoff and topic_turn.
    # - Sends ONLY structured data (titles + confirmed decisions +
    #   sampled turn bodies for the summary). Attachment excerpts are
    #   never sent — the memo's privacy rule.
    # - Returns 201 with the result under a mode-specific key, so the
    #   frontend can tell which artifact came back.

    # Cap applied when sampling Q&A turns for the summary. Keeps large
    # projects inside prompt budget and keeps the prompt focused on
    # texture, not transcript.
    _SUMMARY_SAMPLE_TURNS_PER_TOPIC = 2

    def _resolve_project_title(project_id: str, user: dict[str, Any]) -> str:
        """Best-effort lookup of the v2 project's display title.

        The store exposes ``list_v2_projects`` but no single-project
        getter in the public surface, so we scan the owner's active
        projects. Falls back to the project_id when the row isn't found
        — shouldn't happen after ``_require_owned_project`` passed, but
        the fallback is harmless and keeps the summary prompt honest.
        """
        for project in _store.list_v2_projects(user_id=user["user_id"]) or []:
            if project.get("project_id") == project_id:
                title = (project.get("title") or "").strip()
                if title:
                    return title
        return project_id

    @app.post(
        "/api/v2/projects/{project_id}/summary",
        status_code=status.HTTP_201_CREATED,
        tags=["v2"],
    )
    def v2_project_summary(
        project_id: str, body: SummaryBody = SummaryBody(), user: dict = Depends(_current_user),
    ) -> dict[str, Any]:
        _require_owned_project(project_id, user)
        _require_token_budget(user)

        # Fetch everything the summary needs — topics, decisions, and a
        # sampled set of Q&A turns (most recent first, capped per topic).
        topics = _store.list_topics(project_id=project_id, user_id=user["user_id"])
        decisions = _store.list_decisions(
            project_id=project_id, user_id=user["user_id"],
        )
        sample_turns: list[dict[str, Any]] = []
        for topic in topics:
            tid = topic["topic_id"]
            turns = _store.list_qna_turns(topic_id=tid, user_id=user["user_id"]) or []
            # Most-recent-first, capped per topic. Each turn carries its
            # topic_id forward so the adapter can group correctly.
            for turn in list(reversed(turns))[:_SUMMARY_SAMPLE_TURNS_PER_TOPIC]:
                turn_copy = dict(turn)
                turn_copy["topic_id"] = tid
                sample_turns.append(turn_copy)

        project_title = _resolve_project_title(project_id, user)

        try:
            adapter = _get_summary_adapter()
            result = adapter.generate(
                project_title=project_title,
                topics=topics,
                decisions=decisions,
                sample_turns=sample_turns,
                locale=_validate_locale(body.locale),
            )
        except RuntimeError as exc:
            raise _planner_error_response(exc)

        # Record usage — fall back to the chars/4 estimator because the
        # adapter doesn't currently surface real OpenAI usage.
        _record_llm_usage(
            user,
            prompt_text=project_title + " " + " ".join(
                (d.get("statement") or "") for d in decisions
            ),
            response_text=str(result),
        )

        return {"summary": result}

    @app.post(
        "/api/v2/projects/{project_id}/outline",
        status_code=status.HTTP_201_CREATED,
        tags=["v2"],
    )
    def v2_project_outline(
        project_id: str,
        body: OutlineBody,
        user: dict = Depends(_current_user),
    ) -> dict[str, Any]:
        _require_owned_project(project_id, user)
        artifact_type = body.artifact_type.strip()
        if not artifact_type:
            raise HTTPException(
                status_code=400,
                detail={"error": "validation_error", "message": "artifact_type is required"},
            )
        _require_token_budget(user)

        topics = _store.list_topics(project_id=project_id, user_id=user["user_id"])
        decisions = _store.list_decisions(
            project_id=project_id, user_id=user["user_id"],
        )
        project_title = _resolve_project_title(project_id, user)

        try:
            adapter = _get_outline_adapter()
            result = adapter.generate(
                project_title=project_title,
                artifact_type=artifact_type,
                topics=topics,
                decisions=decisions,
                locale=_validate_locale(body.locale),
            )
        except RuntimeError as exc:
            raise _planner_error_response(exc)

        _record_llm_usage(
            user,
            prompt_text=project_title + " " + artifact_type + " " + " ".join(
                (d.get("statement") or "") for d in decisions
            ),
            response_text=str(result),
        )

        return {"outline": result}

    @app.post(
        "/api/v2/projects/{project_id}/dedupe",
        status_code=status.HTTP_201_CREATED,
        tags=["v2"],
    )
    def v2_project_dedupe(
        project_id: str, body: DedupeBody = DedupeBody(), user: dict = Depends(_current_user),
    ) -> dict[str, Any]:
        _require_owned_project(project_id, user)
        _require_token_budget(user)

        topics = _store.list_topics(project_id=project_id, user_id=user["user_id"])
        decisions = _store.list_decisions(
            project_id=project_id, user_id=user["user_id"],
        )

        try:
            adapter = _get_deduper_adapter()
            result = adapter.generate(
                topics=topics,
                decisions=decisions,
                locale=_validate_locale(body.locale),
            )
        except RuntimeError as exc:
            raise _planner_error_response(exc)

        _record_llm_usage(
            user,
            prompt_text=" ".join((t.get("title") or "") for t in topics),
            response_text=str(result),
        )

        return {"dedupe": result}

    # -----------------------------------------------------------------------
    # Project management (new — was hardcoded DEFAULT_PROJECT_ID before)
    # -----------------------------------------------------------------------
    # NOTE: the ProjectCreateBody / ProjectUpdateBody models are defined
    # at module scope (above). FastAPI / pydantic can't resolve forward
    # references when bodies are declared inside a function — route
    # handlers misinterpret the body as a query parameter and return 422
    # (found by Agent R's regression test `test_create_project_route_is_known_broken`).

    @app.get("/api/v2/projects", tags=["v2"])
    def v2_list_user_projects(user: dict = Depends(_current_user)) -> dict[str, Any]:
        return {"projects": _store.list_v2_projects(user_id=user["user_id"])}

    # -----------------------------------------------------------------------
    # Anonymous → account transfer
    # -----------------------------------------------------------------------
    # Moves projects created under a per-session anonymous ``user-anon-<hex>``
    # user over to the caller's real account. The signup/login route stashes
    # the previous anon id on the new session cookie; we cross-check the
    # body's id against that stamp so a signed-in user cannot claim any
    # anonymous id they happen to know. Idempotent — zero-rows on re-run.
    from .auth import (  # noqa: PLC0415
        SESSION_COOKIE_NAME,
        SYSTEM_USER_ID,
        _is_anon_user_id,
        _peek_session_user_id,
        _peek_session_previous_anon_user_id,
    )
    from fastapi import Cookie  # noqa: PLC0415

    # Anon id regex: the minter in ``auth._create_anon_user`` formats ids as
    # ``user-anon-<12 hex chars>``. Anything outside that shape is definitely
    # not an id we ever minted and should be rejected as malformed instead of
    # conflated with the "legitimate shape but already claimed" case.
    import re as _re  # noqa: PLC0415
    _ANON_ID_RE = _re.compile(r"^user-anon-[0-9a-f]{6,32}$")

    def _transfer_anon_handler(
        request: Request,  # noqa: ARG001 — required for slowapi decorator
        body: TransferAnonymousProjectsBody,
        inspira_session: str | None = Cookie(
            default=None, alias=SESSION_COOKIE_NAME,
        ),
    ) -> dict[str, int]:
        current_user_id = _peek_session_user_id(inspira_session)
        if (
            not current_user_id
            or current_user_id == SYSTEM_USER_ID
            or _is_anon_user_id(current_user_id)
        ):
            raise HTTPException(
                status_code=401, detail={"error": "auth_required"},
            )
        claimed_anon = body.anonymous_user_id.strip()
        # Error-code split (was both ``not_an_anon_user_id``):
        # - ``malformed_anon_id``         : the id does not match the
        #   ``user-anon-<hex>`` shape we mint; nothing we can even look up.
        # - ``anon_user_already_claimed`` : the id IS shaped correctly AND
        #   exists in the users table, but the row has a ``password_hash``
        #   set. Anon rows never carry one (see ``auth._create_anon_user``),
        #   so a non-null hash on an ``user-anon-`` row is the promotion
        #   tell. The frontend uses the split to show the user a more
        #   actionable error: "that link has already been used" vs "that
        #   id is malformed / probably corrupt".
        if not _is_anon_user_id(claimed_anon) or not _ANON_ID_RE.match(claimed_anon):
            raise HTTPException(
                status_code=400, detail={"error": "malformed_anon_id"},
            )
        claimed_row = _store.get_user_by_id(claimed_anon)
        if claimed_row is not None and claimed_row.get("password_hash"):
            raise HTTPException(
                status_code=409,
                detail={"error": "anon_user_already_claimed"},
            )
        authorised_anon = _peek_session_previous_anon_user_id(inspira_session)
        if authorised_anon is None or authorised_anon != claimed_anon:
            # Either the session has no prior anon stamp (the caller
            # logged in from a device that never had an anon session),
            # or the body's id doesn't match the stamp (likely an
            # attacker probing a guessed id). 403 is the right signal:
            # the caller IS authenticated — they're just not
            # authorised for THIS anon id.
            raise HTTPException(
                status_code=403, detail={"error": "anon_id_mismatch"},
            )
        transferred = _store.transfer_projects_to_user(
            old_user_id=claimed_anon, new_user_id=current_user_id,
        )
        return {"transferred": transferred}

    _transfer_limiter = getattr(app.state, "limiter", None)
    if _transfer_limiter is not None:
        # Auth-adjacent surface; reuse the 10/min auth limit ceiling.
        _transfer_anon_handler = _transfer_limiter.limit("10/minute")(
            _transfer_anon_handler
        )

    app.add_api_route(
        "/api/v2/auth/transfer-anonymous-projects",
        _transfer_anon_handler,
        methods=["POST"],
        tags=["v2"],
    )

    # -----------------------------------------------------------------------
    # Homepage AI suggestions — 3 inferred project ideas from the user's work.
    # GET /api/v2/homepage/suggestions
    # -----------------------------------------------------------------------
    # Returns {suggestions: string[]} — empty array when the user has fewer
    # than 2 projects, or on any LLM failure (non-critical feature).
    # Rate-limited to 10/min/user via slowapi; system users are excluded.

    from .homepage import generate_suggestions as _generate_suggestions
    from .agents.openai_adapter import generate_homepage_suggestions as _gen_hp_suggestions

    _hp_limiter = getattr(app.state, "limiter", None)

    def _homepage_suggestions_handler(
        request: Request, user: dict = Depends(_current_user),
    ) -> dict[str, Any]:
        if user.get("is_system"):
            return {"suggestions": []}
        # Build a thin adapter shim: the route owns the client, homepage.py
        # accepts any object with ``generate_homepage_suggestions``.
        locale = _validate_locale(request.query_params.get("locale"))

        class _AdapterShim:
            def generate_homepage_suggestions(
                self, context: dict, loc: str | None,
            ) -> list[str]:
                return _gen_hp_suggestions(context, loc)

        suggestions = _generate_suggestions(
            _store,
            user_id=user["user_id"],
            adapter=_AdapterShim(),
            locale=locale,
        )
        return {"suggestions": suggestions}

    if _hp_limiter is not None:
        _homepage_suggestions_handler = _hp_limiter.limit("10/minute")(
            _homepage_suggestions_handler
        )

    app.add_api_route(
        "/api/v2/homepage/suggestions",
        _homepage_suggestions_handler,
        methods=["GET"],
        tags=["v2"],
    )

    @app.post(
        "/api/v2/projects",
        status_code=status.HTTP_201_CREATED,
        tags=["v2"],
    )
    def v2_create_project(
        body: ProjectCreateBody,
        request: Request,  # noqa: ARG001 — required for per-route slowapi rate limiting
        user: dict = Depends(_current_user),
    ) -> dict[str, Any]:
        title = body.title.strip() or "Untitled project"
        project = _store.create_v2_project(title=title, user_id=user["user_id"])
        return {"project": project}

    @app.post("/api/v2/projects/{project_id}/update", tags=["v2"])
    def v2_update_project(
        project_id: str, body: ProjectUpdateBody, user: dict = Depends(_current_user),
    ) -> dict[str, Any]:
        updates = body.model_dump(exclude_unset=True, exclude_none=True)
        if not updates:
            raise HTTPException(
                status_code=400,
                detail={"error": "validation_error", "message": "no valid fields to update"},
            )
        project = _store.update_v2_project(
            project_id=project_id, user_id=user["user_id"], **updates,
        )
        if project is None:
            raise HTTPException(status_code=404, detail={"error": "project_not_found"})
        return {"project": project}

    # ------------------------------------------------------------------
    # v4 B3.3 / B1.1 — project state machine + Kanban manual override
    # ------------------------------------------------------------------
    # Four endpoints share the same shape:
    #   - workspace-scoped via current_workspace_member (admin for
    #     mutations; viewer for the list)
    #   - project_id pulled from the path
    #   - 404 for missing-or-cross-workspace (the store collapses both
    #     cases to None to avoid leaking which it was)
    #   - 409 for illegal transitions / stale state, with the
    #     IllegalTransitionError.payload shape pinned by the
    #     project_state.py module for forward consistency
    #   - audit_log rows on every transition (success AND rejection) so
    #     the workspace audit timeline shows attempted hijacks too
    from .project_state import (  # noqa: PLC0415
        IllegalTransitionError,
        STATES as PROJECT_STATES,
        UnknownActionError,
        next_state_for_action,
    )
    from .store import StaleProjectStateError  # noqa: PLC0415
    from .workspaces.models import Role, WorkspaceMember  # noqa: PLC0415

    def _audit_transition_rejected(
        *,
        workspace_id: str,
        actor_user_id: str,
        project_id: str,
        current_state: str,
        attempted: str,
    ) -> None:
        """Write a ``transition_rejected`` audit row for a 409.

        Fire-and-forget — the rejection itself is the primary signal
        to the caller; an audit-write hiccup must not change the
        observable response. Mirrors the try/except in
        ``store._emit_audit_silent``.
        """
        try:
            _store.append_audit_event(
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                category="project_state",
                action="transition_rejected",
                project_id=project_id,
                subject_id=project_id,
                before={"state": current_state},
                after={"attempted": attempted},
            )
        except Exception:  # noqa: BLE001
            # Logged at the store level via append_audit_event itself
            # if it fails inside the connection; here we intentionally
            # swallow so the route returns 409 cleanly.
            pass

    @app.post("/api/v2/projects/{project_id}/transition", tags=["v2"])
    def v2_project_transition(
        project_id: str,
        body: ProjectTransitionBody,
        member: WorkspaceMember = Depends(
            current_workspace_member(Role.admin)
        ),
    ) -> dict[str, Any]:
        # Read the current state before we touch the state machine so
        # we can include it in any rejection audit / 409 payload.
        existing = _store._get_v2_project(project_id)
        if existing is None or existing.get("workspace_id") != member.workspace_id:
            raise HTTPException(
                status_code=404, detail={"error": "project_not_found"}
            )
        current_state = existing.get("project_state") or "pending_review"
        try:
            target_state = next_state_for_action(current_state, body.action)
        except UnknownActionError as exc:
            raise HTTPException(
                status_code=400,
                detail={"error": "unknown_action", "message": str(exc)},
            )
        except IllegalTransitionError as exc:
            _audit_transition_rejected(
                workspace_id=member.workspace_id,
                actor_user_id=member.user_id,
                project_id=project_id,
                current_state=current_state,
                attempted=exc.attempted,
            )
            raise HTTPException(status_code=409, detail=exc.payload)
        try:
            updated = _store.update_v2_project_state(
                project_id=project_id,
                workspace_id=member.workspace_id,
                actor_user_id=member.user_id,
                target_state=target_state,
                manual=False,
            )
        except IllegalTransitionError as exc:
            # Defense in depth — store re-validates internally; if the
            # state changed between our read and the store's read we
            # might land here instead of StaleProjectStateError.
            _audit_transition_rejected(
                workspace_id=member.workspace_id,
                actor_user_id=member.user_id,
                project_id=project_id,
                current_state=exc.current,
                attempted=exc.attempted,
            )
            raise HTTPException(status_code=409, detail=exc.payload)
        except StaleProjectStateError as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "stale_state",
                    "current": exc.observed,
                    "message": (
                        "Project state changed under you — refetch and "
                        "try again."
                    ),
                },
            )
        if updated is None:
            raise HTTPException(
                status_code=404, detail={"error": "project_not_found"}
            )
        return {"project": updated}

    @app.post(
        "/api/v2/projects/{project_id}/manual-state-override",
        tags=["v2"],
    )
    def v2_project_manual_state_override(
        project_id: str,
        body: ProjectStateOverrideBody,
        member: WorkspaceMember = Depends(
            current_workspace_member(Role.admin)
        ),
    ) -> dict[str, Any]:
        # Note is optional — the actor_user_id passed below captures
        # the WHO for audit, which is the load-bearing signal. The
        # WHY is a nice-to-have but no longer gate-blocking.
        note = body.note.strip()
        target_state = body.target_state
        if target_state not in PROJECT_STATES:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "unknown_target_state",
                    "allowed": list(PROJECT_STATES),
                },
            )
        try:
            updated = _store.update_v2_project_state(
                project_id=project_id,
                workspace_id=member.workspace_id,
                actor_user_id=member.user_id,
                target_state=target_state,
                note=note,
                manual=True,
            )
        except StaleProjectStateError as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "stale_state",
                    "current": exc.observed,
                },
            )
        if updated is None:
            raise HTTPException(
                status_code=404, detail={"error": "project_not_found"}
            )
        return {"project": updated}

    @app.post(
        "/api/v2/projects/{project_id}/manual-priority-order",
        tags=["v2"],
    )
    def v2_project_manual_priority_order(
        project_id: str,
        body: ProjectPriorityOrderBody,
        member: WorkspaceMember = Depends(
            current_workspace_member(Role.admin)
        ),
    ) -> dict[str, Any]:
        updated = _store.update_v2_project_priority_order(
            project_id=project_id,
            workspace_id=member.workspace_id,
            actor_user_id=member.user_id,
            priority_order=body.priority_order,
        )
        if updated is None:
            raise HTTPException(
                status_code=404, detail={"error": "project_not_found"}
            )
        return {"project": updated}

    @app.get(
        "/api/v2/workspaces/{workspace_id}/projects",
        tags=["v2"],
    )
    def v2_list_workspace_projects(
        workspace_id: str,  # noqa: ARG001 — bound by current_workspace_member
        state: str | None = None,
        include_archived: bool = False,
        member: WorkspaceMember = Depends(
            current_workspace_member(Role.viewer)
        ),
    ) -> dict[str, Any]:
        # Path scoping: the dependency already resolves workspace_id
        # from request.path_params (workspaces/dependencies.py:82) and
        # 403s the caller off if they're not a member. We re-read the
        # bound id off ``member`` so we can't drift from the validated
        # value.
        if state is not None and state not in PROJECT_STATES:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "unknown_state",
                    "allowed": list(PROJECT_STATES),
                },
            )
        projects = _store.list_v2_workspace_projects(
            workspace_id=member.workspace_id,
            state=state,
            include_archived=include_archived,
        )
        return {"projects": projects}

    @app.post("/api/v2/projects/{project_id}/delete", tags=["v2"])
    def v2_delete_project(project_id: str, user: dict = Depends(_current_user)) -> dict[str, Any]:
        ok = _store.delete_v2_project(project_id=project_id, user_id=user["user_id"])
        if not ok:
            raise HTTPException(status_code=404, detail={"error": "project_not_found"})
        return {"deleted": True, "project_id": project_id}

    # T5.3: REST-style DELETE alias of the POST /delete handler. The
    # original /delete subroute pre-dates the project's settle on
    # REST verbs; new external integrations (curl recipes, MCP tools,
    # Custom GPT actions) expect DELETE /api/v2/projects/{id} to work
    # without the /delete suffix. Keep the POST in place for backward
    # compat — every existing client still calls it.
    @app.delete("/api/v2/projects/{project_id}", tags=["v2"])
    def v2_delete_project_rest(
        project_id: str, user: dict = Depends(_current_user),
    ) -> dict[str, Any]:
        return v2_delete_project(project_id=project_id, user=user)

    # Deep-clone: copies topics (with positions), relationships, decisions,
    # open questions, risks/assumptions, and Q&A turns. Does NOT copy
    # shelf_id (the copy starts on the implicit "Unfiled" shelf) nor
    # shared_links (a fresh share token must be minted explicitly).
    #
    # IDOR: 404 both for "the project doesn't exist" and "you don't own it"
    # so project_ids stay un-enumerable — same policy as delete / shelve.
    @app.post(
        "/api/v2/projects/{project_id}/duplicate",
        status_code=status.HTTP_201_CREATED,
        tags=["v2"],
    )
    def v2_duplicate_project(
        project_id: str, user: dict = Depends(_current_user),
    ) -> dict[str, Any]:
        duplicated = _store.duplicate_v2_project(
            source_project_id=project_id, user_id=user["user_id"],
        )
        if duplicated is None:
            raise HTTPException(
                status_code=404, detail={"error": "project_not_found"},
            )
        return {"project": duplicated}

    # -----------------------------------------------------------------------
    # Project archiving — a softer middle ground between delete and nothing.
    # -----------------------------------------------------------------------
    # Archive hides the project from the default GET /api/v2/projects listing
    # but keeps every row intact (topics, decisions, Q&A, share tokens). The
    # user can restore it via unarchive; the user can also still delete an
    # archived project (delete always wins — soft-delete is a stronger state).
    # Three routes, all authenticated and user-scoped with the same
    # un-enumerable 404 pattern used by every other v2 mutation route.
    #
    # GET /api/v2/projects/archived is registered earlier in this file
    # (right before the `/{project_id}` wildcard) so FastAPI's
    # first-match routing doesn't bind "archived" as a project_id.
    # The archive / unarchive POSTs below take {project_id} as the
    # normal positional segment.

    @app.post("/api/v2/projects/{project_id}/archive", tags=["v2"])
    def v2_archive_project(
        project_id: str, user: dict = Depends(_current_user),
    ) -> dict[str, Any]:
        project = _store.archive_v2_project(
            project_id=project_id, user_id=user["user_id"],
        )
        if project is None:
            raise HTTPException(
                status_code=404, detail={"error": "project_not_found"},
            )
        return {"project": project}

    @app.post("/api/v2/projects/{project_id}/unarchive", tags=["v2"])
    def v2_unarchive_project(
        project_id: str, user: dict = Depends(_current_user),
    ) -> dict[str, Any]:
        project = _store.unarchive_v2_project(
            project_id=project_id, user_id=user["user_id"],
        )
        if project is None:
            raise HTTPException(
                status_code=404, detail={"error": "project_not_found"},
            )
        return {"project": project}

    # -----------------------------------------------------------------------
    # Recently-deleted recovery — soft-delete with grace.
    # -----------------------------------------------------------------------
    # GET /recently-deleted is registered earlier in this file (right
    # before the `/{project_id}` wildcard) so FastAPI's first-match
    # routing doesn't bind "recently-deleted" as a project_id. The
    # /restore + /purge POSTs below take {project_id} as the normal
    # positional segment. Restore returns the project on success, 410
    # Gone if the grace window has lapsed, 404 otherwise. Purge is
    # owner-only and only allowed on already-soft-deleted rows.

    @app.post("/api/v2/projects/{project_id}/restore", tags=["v2"])
    def v2_restore_project(
        project_id: str, user: dict = Depends(_current_user),
    ) -> dict[str, Any]:
        result = _store.restore_v2_project(
            project_id=project_id, user_id=user["user_id"],
        )
        if result == "expired":
            raise HTTPException(
                status_code=410,
                detail={"error": "grace_window_expired"},
            )
        if result is None:
            raise HTTPException(
                status_code=404, detail={"error": "project_not_found"},
            )
        return {"project": result}

    @app.post(
        "/api/v2/projects/{project_id}/purge",
        status_code=status.HTTP_204_NO_CONTENT,
        tags=["v2"],
    )
    def v2_purge_project(
        project_id: str, user: dict = Depends(_current_user),
    ) -> Response:
        ok = _store.purge_v2_project(
            project_id=project_id, user_id=user["user_id"],
        )
        if not ok:
            raise HTTPException(
                status_code=404, detail={"error": "project_not_found"},
            )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    # -----------------------------------------------------------------------
    # Shelves — user-owned named containers for grouping related projects.
    # -----------------------------------------------------------------------
    # Five routes:
    #   GET    /api/v2/shelves                     → list the user's shelves
    #   POST   /api/v2/shelves                     → create a new shelf
    #   POST   /api/v2/shelves/{shelf_id}/update   → rename / reorder
    #   POST   /api/v2/shelves/{shelf_id}/delete   → soft-delete + un-shelve
    #   POST   /api/v2/projects/{project_id}/shelve → move project onto a shelf
    #
    # All are authenticated and user-scoped. Cross-user attempts resolve to
    # 404 (not 403) to keep shelf and project IDs un-enumerable, matching
    # the pattern used by every other v2 mutation route. Name validation
    # surfaces as 400 with the reason in detail.message so the frontend
    # can render a toast without parsing an error code.

    from .shelves import (
        ShelfValidationError,
        create_shelf as _shelf_create,
        delete_shelf as _shelf_delete,
        list_shelves as _shelf_list,
        move_project_to_shelf as _shelf_move_project,
        rename_shelf as _shelf_rename,
        reorder_shelf as _shelf_reorder,
    )

    @app.get("/api/v2/shelves", tags=["v2"])
    def v2_list_shelves(user: dict = Depends(_current_user)) -> dict[str, Any]:
        shelves = _shelf_list(_store, user_id=user["user_id"])
        return {"shelves": shelves}

    @app.post(
        "/api/v2/shelves",
        status_code=status.HTTP_201_CREATED,
        tags=["v2"],
    )
    def v2_create_shelf(
        body: ShelfCreateBody, user: dict = Depends(_current_user),
    ) -> dict[str, Any]:
        try:
            shelf = _shelf_create(_store, user_id=user["user_id"], name=body.name)
        except ShelfValidationError as exc:
            raise HTTPException(
                status_code=400,
                detail={"error": "invalid_shelf_name", "message": str(exc)},
            )
        return {"shelf": shelf}

    @app.post("/api/v2/shelves/{shelf_id}/update", tags=["v2"])
    def v2_update_shelf(
        shelf_id: str,
        body: ShelfUpdateBody,
        user: dict = Depends(_current_user),
    ) -> dict[str, Any]:
        # The frontend can send either/both fields; at least one must be
        # present so we don't silently no-op a stale request.
        if body.name is None and body.sort_order is None:
            raise HTTPException(
                status_code=400,
                detail={"error": "no_fields_to_update"},
            )
        shelf: dict[str, Any] | None = None
        if body.name is not None:
            try:
                shelf = _shelf_rename(
                    _store,
                    shelf_id=shelf_id,
                    user_id=user["user_id"],
                    name=body.name,
                )
            except ShelfValidationError as exc:
                raise HTTPException(
                    status_code=400,
                    detail={"error": "invalid_shelf_name", "message": str(exc)},
                )
            if shelf is None:
                raise HTTPException(
                    status_code=404, detail={"error": "shelf_not_found"},
                )
        if body.sort_order is not None:
            shelf = _shelf_reorder(
                _store,
                shelf_id=shelf_id,
                user_id=user["user_id"],
                sort_order=int(body.sort_order),
            )
            if shelf is None:
                raise HTTPException(
                    status_code=404, detail={"error": "shelf_not_found"},
                )
        # shelf is guaranteed non-None when we reach here (one branch above
        # always assigns); the type checker wants the explicit fallback.
        if shelf is None:  # pragma: no cover — unreachable
            raise HTTPException(
                status_code=404, detail={"error": "shelf_not_found"},
            )
        # Add project_count so the frontend doesn't have to re-query. The
        # rename/reorder paths return the bare shelf row; we fold in the
        # count here for shape parity with the list endpoint.
        count_row = next(
            (s for s in _store.list_shelves(user_id=user["user_id"])
             if s["shelf_id"] == shelf["shelf_id"]),
            None,
        )
        if count_row is not None:
            shelf = {**shelf, "project_count": count_row.get("project_count", 0)}
        return {"shelf": shelf}

    @app.post("/api/v2/shelves/{shelf_id}/delete", tags=["v2"])
    def v2_delete_shelf(
        shelf_id: str, user: dict = Depends(_current_user),
    ) -> dict[str, Any]:
        ok = _shelf_delete(_store, shelf_id=shelf_id, user_id=user["user_id"])
        if not ok:
            raise HTTPException(
                status_code=404, detail={"error": "shelf_not_found"},
            )
        return {"deleted": True, "shelf_id": shelf_id}

    @app.post("/api/v2/projects/{project_id}/shelve", tags=["v2"])
    def v2_shelve_project(
        project_id: str,
        body: ProjectShelveBody,
        user: dict = Depends(_current_user),
    ) -> dict[str, Any]:
        # Normalise empty string to None — the frontend sometimes sends
        # "" when it means "un-shelve" (HTML select with an empty option).
        target = body.shelf_id if body.shelf_id else None
        project = _shelf_move_project(
            _store,
            project_id=project_id,
            user_id=user["user_id"],
            shelf_id=target,
        )
        if project is None:
            # Conflates "project doesn't exist", "shelf doesn't exist", and
            # "one of them belongs to another user" — intentional (IDOR).
            raise HTTPException(
                status_code=404, detail={"error": "project_or_shelf_not_found"},
            )
        return {"project": project}

    # -----------------------------------------------------------------------
    # Project templates — starter packs
    # -----------------------------------------------------------------------
    # Ten hand-authored templates ship with the service; see the
    # ``templates`` package for content. Two endpoints:
    #
    # - ``GET /api/v2/templates`` — summary list for the gallery card grid.
    #   Intentionally does NOT include the topics/relationships payload so
    #   a first visit stays small (the detail set is 10 * (7 topics +
    #   8 edges) of text, fine but not worth round-tripping before the
    #   user picks one).
    # - ``POST /api/v2/projects/from-template`` — creates a real v2 project
    #   for the authenticated user, seeds topics + relationships, and
    #   returns the same envelope shape the kickoff route does so the
    #   frontend can open the canvas directly without another round trip.

    @app.post("/api/v2/feedback/extract-themes", tags=["v2"])
    def v2_extract_themes(
        body: ExtractThemesBody,
        request: Request,  # noqa: ARG001 — required for per-route slowapi rate limiting
        user: dict = Depends(_current_user),
    ) -> dict[str, Any]:
        """v4 — cluster pasted customer feedback into 3-5 themes.

        The frontend's PasteFeedbackDialog calls this on Submit, then
        fires one kickoff per returned theme in parallel to auto-generate
        one project per theme on the workspace home.

        Cheap call (~$0.001 in OpenAI tokens, ~3-5s on gpt-4o-mini).
        Available to all tiers, including Free.
        """
        items = [s.strip() for s in body.items if s and s.strip()]
        if not items:
            raise HTTPException(
                status_code=400,
                detail={"error": "no_items", "message": "items must be non-empty"},
            )
        # Per-user daily token budget gate (audit M5). Runs BEFORE the LLM
        # call so an over-quota request never bills a single token.
        _require_token_budget(user)
        # Per-item char cap to bound the LLM input. The Pydantic model
        # caps the count; this caps individual items.
        truncated_items = [item[:2000] for item in items[:2000]]

        adapter = _require_adapter()
        try:
            result = adapter.extract_themes(items=truncated_items, locale=body.locale)
        except Exception as exc:  # noqa: BLE001
            logger.exception("[extract_themes] adapter call failed")
            raise HTTPException(
                status_code=502,
                detail={
                    "error": "extract_themes_failed",
                    "message": str(exc),
                },
            ) from exc

        return {
            "themes": result.get("themes") or [],
            "total_items": len(truncated_items),
        }

    @app.get("/api/v2/templates", tags=["v2"])
    def v2_list_templates() -> dict[str, Any]:
        from .templates import DOC_TYPE_ORPHAN_SLUGS, TEMPLATES

        # Filter out doc-type orphans (their document tab dead-ends in
        # production until each gets a proper domain mapping or doc-type
        # generator). Surface business-plan first — it's the load-bearing
        # curated template.
        visible = [t for t in TEMPLATES if t.slug not in DOC_TYPE_ORPHAN_SLUGS]
        visible.sort(key=lambda t: 0 if t.slug == "business-plan" else 1)

        return {
            "templates": [
                {
                    "slug": t.slug,
                    "title": t.title,
                    "description": t.description,
                    "tagline": t.tagline,
                    "topic_count": len(t.topics),
                    "relationship_count": len(t.relationships),
                    "domain_framing": t.domain_framing,
                }
                for t in visible
            ],
        }

    @app.post(
        "/api/v2/projects/from-template",
        status_code=status.HTTP_201_CREATED,
        tags=["v2"],
    )
    def v2_create_project_from_template(
        body: ProjectFromTemplateBody,
        user: dict = Depends(_current_user),
    ) -> dict[str, Any]:
        from .templates import DOC_TYPE_ORPHAN_SLUGS, get_template

        slug = body.slug.strip()
        if not slug:
            raise HTTPException(
                status_code=400, detail={"error": "slug_required"},
            )
        # Defense in depth: the slug is hidden from the kickoff picker
        # via v2_list_templates(), but a stale client (cached gallery)
        # or direct API call could still reach this handler. 404
        # mirrors the unknown-slug response so we don't leak that the
        # template exists but is gated.
        if slug in DOC_TYPE_ORPHAN_SLUGS:
            raise HTTPException(
                status_code=404,
                detail={"error": "template_not_found", "slug": slug},
            )
        template = get_template(slug)
        if template is None:
            raise HTTPException(
                status_code=404,
                detail={"error": "template_not_found", "slug": slug},
            )

        # Create the project row first. Title mirrors the template title
        # so the top-bar chip reads cleanly the moment the canvas opens;
        # the user can rename via the project switcher afterwards.
        project = _store.create_v2_project(
            title=template.title, user_id=user["user_id"],
        )
        project_id = project["project_id"]

        # Seed topics. Layout follows the same 2-row zigzag the kickoff
        # handler uses so the canvas doesn't open on top of the dagre
        # auto-layout pass the frontend runs next; our fresh positions
        # are a reasonable floor if the frontend can't re-layout.
        x_step, y_rows = 440, [0, 320]
        persisted_topics: list[dict[str, Any]] = []
        title_to_topic_id: dict[str, str] = {}
        for idx, topic in enumerate(template.topics):
            persisted = _store.create_topic(
                project_id=project_id,
                title=topic.title,
                icon=topic.icon,
                position_x=float((idx // len(y_rows)) * x_step),
                position_y=float(y_rows[idx % len(y_rows)]),
                origin="planner_initial",
                order_index=idx,
                metadata={
                    "why_this_topic": topic.why_this_topic,
                    "template_slug": template.slug,
                },
                user_id=user["user_id"],
            )
            persisted_topics.append(persisted)
            title_to_topic_id[topic.title] = persisted["topic_id"]

        # Seed relationships. Skip any edge whose endpoints fail to
        # resolve — shouldn't happen for a well-formed template, but we
        # don't want a stray typo in a template to fail the whole create.
        persisted_relationships: list[dict[str, Any]] = []
        for rel in template.relationships:
            src_id = title_to_topic_id.get(rel.from_title)
            tgt_id = title_to_topic_id.get(rel.to_title)
            if not src_id or not tgt_id:
                continue
            persisted_rel = _store.create_relationship(
                project_id=project_id,
                source_topic_id=src_id,
                target_topic_id=tgt_id,
                label=rel.label,
                origin="planner_inferred",
                user_id=user["user_id"],
            )
            persisted_relationships.append(persisted_rel)

        return {
            "project": project,
            "topics": persisted_topics,
            "relationships": persisted_relationships,
            "template": {
                "slug": template.slug,
                "title": template.title,
                "tagline": template.tagline,
                "domain_framing": template.domain_framing,
            },
        }

    # -----------------------------------------------------------------------
    # Example projects — pre-seeded project canvases used in the onboarding
    # walkthrough. A user picks a domain and lands on a canvas populated with
    # realistic topics, sample decisions, and sample Q&A turns so they can
    # poke around something real before committing to their own work. Marked
    # with ``metadata.is_example = True`` so count-gated features (homepage
    # AI suggestions, future project-count caps) can exclude them.
    # -----------------------------------------------------------------------

    @app.get("/api/v2/examples", tags=["v2"])
    def v2_list_examples() -> dict[str, Any]:
        from .example_projects import EXAMPLE_PROJECTS  # noqa: PLC0415
        return {
            "examples": [
                {
                    "slug": s.slug,
                    "display_name": s.display_name,
                    "one_liner": s.one_liner,
                    "topic_count": len(s.topics),
                }
                for s in EXAMPLE_PROJECTS
            ],
        }

    @app.post(
        "/api/v2/projects/from-example",
        status_code=status.HTTP_201_CREATED,
        tags=["v2"],
    )
    def v2_create_project_from_example(
        body: ExampleProjectBody,
        user: dict = Depends(_current_user),
    ) -> dict[str, Any]:
        from .example_projects import (  # noqa: PLC0415
            instantiate_example_project,
        )
        from .auth import (  # noqa: PLC0415
            SYSTEM_USER_ID as _SYS, _is_anon_user_id as _is_anon,
        )

        # Example projects are a paid-ish convenience — keep them behind
        # sign-up. Anonymous (``user-anon-...``) and the legacy shared
        # system user both land on the 401 path.
        if user["user_id"] == _SYS or _is_anon(user["user_id"]):
            raise HTTPException(
                status_code=401, detail={"error": "auth_required"},
            )
        try:
            project = instantiate_example_project(
                _store, user_id=user["user_id"], slug=body.slug,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=404, detail={"error": str(exc)},
            ) from exc
        topics = _store.list_topics(project_id=project["project_id"])
        return {"project": project, "topics": topics}

    # -----------------------------------------------------------------------
    # Shared read-only links (canonical registrations are in the "sharing"
    # section below ~line 2968; this older block was removed to eliminate
    # the duplicate POST /api/v2/projects/{project_id}/share registration
    # that FastAPI silently accepted (second always wins).
    # -----------------------------------------------------------------------

    # -----------------------------------------------------------------------
    # AI project suggestions — render on the new-project screen for returning
    # users. Only fires for real users with >=2 active projects; system-user
    # and single-project users get an empty suggestions list (the frontend
    # hides the chip row in that case).
    #
    # Privacy: the underlying prompt only receives project titles, topic
    # titles, and confirmed decision statements. Q&A bodies and attachment
    # excerpts are NEVER sent. See agents/suggestions.py for details.
    # -----------------------------------------------------------------------

    @app.post(
        "/api/v2/projects/suggest",
        status_code=status.HTTP_200_OK,
        tags=["v2"],
    )
    def v2_suggest_projects(user: dict = Depends(_current_user)) -> dict[str, Any]:
        # System user is the pre-auth fallback; don't burn tokens producing
        # suggestions for them — they have no real portfolio signal.
        from .auth import SYSTEM_USER_ID

        if user["user_id"] == SYSTEM_USER_ID:
            return {"suggestions": []}

        projects = _store.list_v2_projects(user_id=user["user_id"]) or []
        if len(projects) < 2:
            # Product memo: only surface for users with >= 2 active projects.
            return {"suggestions": []}

        # Cache hit? Return immediately; no tokens, no gate.
        cached = _store.get_cached_suggestions(user_id=user["user_id"])
        if cached is not None:
            generated_at = _parse_iso(cached.get("generated_at"))
            if generated_at is not None:
                age_seconds = (
                    datetime.now(timezone.utc) - generated_at
                ).total_seconds()
                if age_seconds < SUGGESTIONS_CACHE_TTL_SECONDS:
                    return {"suggestions": cached.get("suggestions") or []}

        # Miss — new call. Enforce the daily token budget first.
        _require_token_budget(user)

        from .agents.suggestions import generate_project_suggestions

        try:
            result = generate_project_suggestions(
                store=_store,
                user_id=user["user_id"],
                user_display_name=user.get("display_name") or "",
            )
        except RuntimeError as exc:
            raise _planner_error_response(exc)

        suggestions = result.get("suggestions") or []
        usage = result.get("usage") or {}

        # Record real usage when available. SimpleNamespace mirrors the
        # OpenAI usage object shape (.prompt_tokens / .completion_tokens)
        # so _record_llm_usage happily reads it. No prompt/response text
        # fallback needed — we always have real usage from this path.
        try:
            from types import SimpleNamespace

            _record_llm_usage(
                user,
                prompt_text="",
                response_text="",
                openai_usage=SimpleNamespace(
                    prompt_tokens=int(usage.get("prompt_tokens", 0)),
                    completion_tokens=int(usage.get("completion_tokens", 0)),
                ),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "could not record suggestions usage: %s",
                exc,
            )

        # Persist to cache so the next visit is free (4h TTL).
        try:
            _store.save_cached_suggestions(
                user_id=user["user_id"], suggestions=suggestions,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "could not cache suggestions for user=%s: %s",
                user.get("user_id"),
                exc,
            )

        return {"suggestions": suggestions}

    # -----------------------------------------------------------------------
    # Billing — plan catalog, subscription read-model, Stripe checkout,
    # customer portal, and the public Stripe webhook.
    # -----------------------------------------------------------------------
    # All routes except the webhook require a signed-in user. The webhook
    # is intentionally public so Stripe can POST to it; its trust boundary
    # is the signature check inside the StripeBillingProvider.
    #
    # The billing provider is chosen at app-construction time — see
    # billing.provider.get_billing_provider(). It picks the Noop provider
    # (dev default) unless ``STRIPE_SECRET_KEY`` is set, in which case it
    # returns the real Stripe provider. No application code needs to
    # change when Stripe is added; operators just set env vars.
    from .billing import (
        NotConfiguredError,
        get_billing_provider,
        plan_catalog_json,
        get_plan,
    )

    billing_provider = get_billing_provider()
    app.state.billing_provider = billing_provider

    def _success_url_for(request: Request) -> str:
        # Redirect the user back to the app's Billing page after checkout.
        # We prefer the Origin header so the redirect survives dev / prod
        # (localhost / your production domain) without a separate env var. If the
        # header is missing — same-origin or curl — fall back to the API's
        # own host so the user at least lands somewhere real.
        origin = request.headers.get("origin") or str(request.base_url).rstrip("/")
        return f"{origin}/billing?checkout=success"

    def _cancel_url_for(request: Request) -> str:
        origin = request.headers.get("origin") or str(request.base_url).rstrip("/")
        return f"{origin}/billing?checkout=canceled"

    @app.get("/api/v2/billing/plans", tags=["billing"])
    def v2_billing_plans(user: dict = Depends(_current_user)) -> dict[str, Any]:
        # The catalog is identical for every user, but we keep the route
        # authenticated so we never leak plan taxonomy to anonymous
        # scrapers. Changing this to public is one line if/when marketing
        # wants to render the same data on the pricing page.
        _ = user
        return {"plans": plan_catalog_json()}

    @app.get("/api/v2/billing/subscription", tags=["billing"])
    def v2_billing_subscription(
        user: dict = Depends(_current_user),
    ) -> dict[str, Any]:
        view = billing_provider.get_subscription(
            user_id=user["user_id"], store=_store,
        )
        return {
            "subscription": {
                "plan": view.plan.to_public_dict(),
                "status": view.status,
                "stripe_customer_id": view.stripe_customer_id,
                "stripe_subscription_id": view.stripe_subscription_id,
                "current_period_end": view.current_period_end,
                # Added for the Switch-to-annual offer (Pro monthly →
                # annual) which gates on plan age ≥ 30 days, and for
                # the trial_ending sweeper which reads trial_ends_at.
                "started_at": view.started_at,
                "trial_ends_at": view.trial_ends_at,
                "billing_period": view.billing_period,
            },
            "provider_configured": billing_provider.is_configured,
        }

    @app.post("/api/v2/billing/checkout", tags=["billing"])
    def v2_billing_checkout(
        body: BillingCheckoutBody,
        request: Request,
        user: dict = Depends(_current_user),
    ) -> dict[str, Any]:
        slug = body.plan_slug.strip().lower()
        if not slug:
            raise HTTPException(
                status_code=400,
                detail={"error": "validation_error", "message": "plan_slug is required"},
            )
        plan = get_plan(slug)
        if plan is None:
            raise HTTPException(
                status_code=404, detail={"error": "unknown_plan", "slug": slug},
            )
        if plan.slug == "free":
            # Downgrading to Free should go through customer portal or a
            # direct subscription cancellation — checkout doesn't apply.
            raise HTTPException(
                status_code=400,
                detail={"error": "cannot_checkout_free_plan"},
            )
        # Normalize + validate period. The Pydantic Field allows any
        # string up to 10 chars so an old client doesn't 422; we map
        # anything other than "annual" to "monthly" so a bogus value
        # never silently selects the wrong price id.
        period_raw = (body.period or "monthly").strip().lower()
        period = "annual" if period_raw == "annual" else "monthly"
        try:
            session = billing_provider.start_checkout(
                user_id=user["user_id"],
                user_email=user.get("email", "") or "",
                plan=plan,
                store=_store,
                success_url=_success_url_for(request),
                cancel_url=_cancel_url_for(request),
                period=period,
            )
        except NotConfiguredError as exc:
            raise HTTPException(
                status_code=status.HTTP_501_NOT_IMPLEMENTED,
                detail={
                    "error": "billing_not_configured",
                    "message": str(exc),
                },
            )
        return {"checkout": {"session_id": session.session_id, "url": session.url}}

    @app.post("/api/v2/billing/portal", tags=["billing"])
    def v2_billing_portal(
        request: Request, user: dict = Depends(_current_user),
    ) -> dict[str, Any]:
        origin = request.headers.get("origin") or str(request.base_url).rstrip("/")
        try:
            session = billing_provider.open_customer_portal(
                user_id=user["user_id"],
                store=_store,
                return_url=f"{origin}/billing",
            )
        except NotConfiguredError as exc:
            raise HTTPException(
                status_code=status.HTTP_501_NOT_IMPLEMENTED,
                detail={
                    "error": "billing_not_configured",
                    "message": str(exc),
                },
            )
        return {"portal": {"url": session.url}}

    @app.post("/api/v2/billing/webhook", tags=["billing"])
    async def v2_billing_webhook(request: Request) -> dict[str, Any]:
        # Intentionally unauthenticated — Stripe posts directly. The only
        # trust boundary is the signature check inside the provider. When
        # Stripe is not configured we log and 200 so Stripe doesn't retry
        # forever against a dashboard that'll never process the event.
        payload = await request.body()
        signature = request.headers.get("stripe-signature")
        try:
            result = billing_provider.handle_webhook(
                payload=payload, signature=signature, store=_store,
            )
        except Exception as exc:  # noqa: BLE001
            # Signature-verification errors and anything else from the
            # Stripe SDK land here. 400 so Stripe retries with a fresh
            # request id; we log the details rather than echoing them
            # back to an attacker.
            logger.warning("billing webhook rejected: %s", exc)
            raise HTTPException(
                status_code=400, detail={"error": "webhook_rejected"},
            )
        return result

    # -----------------------------------------------------------------------
    # v2 — LLM model tier picker
    # -----------------------------------------------------------------------
    # Three endpoints cover the picker:
    #   GET  /api/v2/model-tiers                    → catalog + current default
    #   PATCH /api/v2/auth/me/preferred-model-tier  → persist user preference
    #
    # The per-turn override (body.model_tier on kickoff / topic_turn) is
    # resolved in those routes themselves via ``tiers.resolve_tier_for_user``
    # — the picker catalog is just read-only reflection of what the user's
    # plan unlocks.

    @app.get("/api/v2/model-tiers", tags=["v2"])
    def v2_list_model_tiers(user: dict = Depends(_current_user)) -> dict[str, Any]:
        """Return the tier catalog scoped to the user's plan.

        Response shape:
        ``{tiers: [{slug, label, description, credit_multiplier, available}],
           current_default: str, persisted_default: str | null}``

        ``current_default`` is the tier that would run if no override is
        passed — either the persisted user preference (when inside the
        plan's allowlist) or the plan default. ``persisted_default`` is the
        raw user-picked value (may be null).
        """
        from .agents.tiers import (  # noqa: PLC0415
            default_tier_for_plan,
            parse_tier,
            resolve_tier_for_user,
            tier_catalog_for_plan,
        )
        plan_view = billing_provider.get_subscription(
            user_id=user["user_id"], store=_store,
        )
        plan_slug = plan_view.plan.slug
        persisted_raw = _store.get_preferred_model_tier(user["user_id"])
        # ``resolve_tier_for_user`` with requested=None returns the effective
        # default for this user (persisted pref if valid, else plan default).
        effective = resolve_tier_for_user(_store, user["user_id"], None)
        return {
            "tiers": tier_catalog_for_plan(plan_slug),
            "plan_slug": plan_slug,
            "current_default": effective.value,
            "persisted_default": (
                parse_tier(persisted_raw).value
                if parse_tier(persisted_raw) is not None
                else None
            ),
            # Echo the plan default for UX hints ("Pro users default to
            # gpt-5", etc.) without the frontend replicating the map.
            "plan_default": default_tier_for_plan(plan_slug).value,
        }

    @app.patch("/api/v2/auth/me/preferred-model-tier", tags=["v2"])
    def v2_set_preferred_model_tier(
        body: PreferredModelTierBody, user: dict = Depends(_current_user),
    ) -> dict[str, Any]:
        """Persist the user's default tier slug.

        400 if the requested tier is not in the user's plan's allowlist.
        ``tier: null`` clears the override (falls back to plan default).
        """
        from .agents.tiers import (  # noqa: PLC0415
            ModelTier,
            allowed_tiers_for_plan,
            parse_tier,
        )
        # Null = clear the override.
        if body.tier is None:
            _store.set_preferred_model_tier(user["user_id"], None)
            return {"tier": None, "cleared": True}
        parsed = parse_tier(body.tier)
        if parsed is None:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "unknown_tier",
                    "valid": sorted(t.value for t in ModelTier),
                },
            )
        plan_view = billing_provider.get_subscription(
            user_id=user["user_id"], store=_store,
        )
        allowed = allowed_tiers_for_plan(plan_view.plan.slug)
        if parsed not in allowed:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "tier_not_in_plan",
                    "tier": parsed.value,
                    "plan": plan_view.plan.slug,
                    "allowed": sorted(t.value for t in allowed),
                },
            )
        try:
            _store.set_preferred_model_tier(user["user_id"], parsed.value)
        except ValueError as exc:
            # Defence-in-depth — the store validates too.
            raise HTTPException(
                status_code=400, detail={"error": "unknown_tier", "message": str(exc)},
            )
        return {"tier": parsed.value, "cleared": False}

    @app.get("/api/v2/auth/me/usage", tags=["v2"])
    def v2_get_usage(user: dict = Depends(_current_user)) -> dict[str, Any]:
        """Per-user monthly usage view (#080).

        Returns:
        ``{
            "plan_slug": str,
            "tiers": [
                {"tier": "base"|"pro"|"frontier", "used": int, "cap": int, "percent": float}
            ],
            "business_plan": {"used": int, "cap": int, "percent": float}
        }``

        Tiers omitted from the array when the plan doesn't include them
        (e.g., Free users see BASE only; Pro users see BASE + PRO).
        Counters reflect the lazy month-boundary reset — a stale window
        reads as 0 even before the next increment overwrites the row.

        ``percent`` is rounded to 4 decimal places (FE renders as a
        whole number; we keep precision for the warning thresholds at
        80% and 100%).

        Auth-required (``_current_user``). The endpoint returns ONLY
        the authenticated caller's data; there's no user_id parameter.
        """
        from .agents.tiers import (  # noqa: PLC0415
            ModelTier,
            get_business_plan_cap,
            get_tier_cap,
        )

        plan_slug = (
            (_store.get_subscription(user_id=user["user_id"]) or {}).get("plan")
            or "free"
        )

        tier_data: list[dict[str, Any]] = []
        for tier in (ModelTier.BASE, ModelTier.PRO, ModelTier.FRONTIER):
            cap = get_tier_cap(plan_slug, tier)
            if cap is None:
                continue
            usage = _store.get_tier_usage(
                user_id=user["user_id"], tier=tier.value,
            )
            used = int(usage.get("output_tokens_used", 0))
            tier_data.append(
                {
                    "tier": tier.value,
                    "used": used,
                    "cap": cap,
                    "percent": round(used / cap, 4) if cap > 0 else 0.0,
                }
            )

        bp_cap = get_business_plan_cap(plan_slug)
        bp_usage = _store.get_business_plan_usage(user_id=user["user_id"])
        bp_used = int(bp_usage.get("plans_used_this_month", 0))
        return {
            "plan_slug": plan_slug,
            "tiers": tier_data,
            "business_plan": {
                "used": bp_used,
                "cap": bp_cap,
                "percent": round(bp_used / bp_cap, 4) if bp_cap > 0 else 0.0,
            },
        }

    # -----------------------------------------------------------------------
    # Personal Access Tokens (PATs)
    # -----------------------------------------------------------------------
    # Users mint named API tokens from Account Settings → "API tokens".
    # Each token is shown once (copy-once dialog) and then persisted
    # only as a SHA-256 hash.  Callers hit /api/v2/auth/tokens from a
    # logged-in browser session; the token grants Bearer access to the
    # v2 API afterwards (see bearer_auth.py).
    #
    # Anonymous/system users can't mint tokens — a PAT outlives any
    # single browser tab, so it only makes sense for real signed-in
    # accounts.  System users explicitly bounce with 403.

    @app.post("/api/v2/auth/tokens", status_code=201, tags=["v2"])
    def v2_mint_access_token(
        body: AccessTokenCreateBody,
        request: Request,  # noqa: ARG001 — required for per-route slowapi rate limiting
        user: dict = Depends(_current_user),
    ) -> dict[str, Any]:
        """Mint a fresh PAT.  Returns the raw token ONCE.

        The response body carries the plaintext token exactly once; the
        UI is responsible for showing it in a copy-once dialog.  The
        server persists only the SHA-256 hash — the raw value is
        unrecoverable after this response returns.
        """
        if user.get("is_system"):
            raise HTTPException(
                status_code=403,
                detail={"error": "sign_in_required"},
            )
        from .auth import SYSTEM_USER_ID, _is_anon_user_id  # noqa: PLC0415
        if user["user_id"] == SYSTEM_USER_ID or _is_anon_user_id(user["user_id"]):
            raise HTTPException(
                status_code=403,
                detail={"error": "sign_in_required"},
            )
        try:
            token_id, raw_token = _store.mint_access_token(
                user["user_id"], body.name,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail={"error": "invalid_name", "message": str(exc)},
            )
        # Hand back the raw token plus the metadata the UI needs for the
        # "just minted" row it optimistically inserts into the list.
        created_at = None
        for row in _store.list_access_tokens(user["user_id"]):
            if row["token_id"] == token_id:
                created_at = row["created_at"]
                break
        return {
            "token_id": token_id,
            "name": body.name.strip(),
            "token": raw_token,
            "created_at": created_at,
        }

    @app.get("/api/v2/auth/tokens", tags=["v2"])
    def v2_list_access_tokens(
        user: dict = Depends(_current_user),
    ) -> dict[str, Any]:
        """List every PAT this user has ever minted (active + revoked).

        Revoked tokens stay in the response so the user can audit their
        own history; the UI greys them out.  Never includes the raw
        token or its hash.
        """
        if user.get("is_system"):
            raise HTTPException(
                status_code=403,
                detail={"error": "sign_in_required"},
            )
        from .auth import SYSTEM_USER_ID, _is_anon_user_id  # noqa: PLC0415
        if user["user_id"] == SYSTEM_USER_ID or _is_anon_user_id(user["user_id"]):
            raise HTTPException(
                status_code=403,
                detail={"error": "sign_in_required"},
            )
        return {"tokens": _store.list_access_tokens(user["user_id"])}

    @app.delete("/api/v2/auth/tokens/{token_id}", tags=["v2"])
    def v2_revoke_access_token(
        token_id: str, user: dict = Depends(_current_user),
    ) -> dict[str, Any]:
        """Revoke a PAT.  IDOR-checked: only the token owner can revoke.

        Returns 404 when the token doesn't exist OR belongs to a
        different user — same response shape so a caller can't enumerate
        token_ids across accounts.  Re-revoking an already-revoked token
        also returns 404 (the row is excluded by the ``revoked_at IS NULL``
        WHERE clause in the store).
        """
        if user.get("is_system"):
            raise HTTPException(
                status_code=403,
                detail={"error": "sign_in_required"},
            )
        from .auth import SYSTEM_USER_ID, _is_anon_user_id  # noqa: PLC0415
        if user["user_id"] == SYSTEM_USER_ID or _is_anon_user_id(user["user_id"]):
            raise HTTPException(
                status_code=403,
                detail={"error": "sign_in_required"},
            )
        if not _store.revoke_access_token(user["user_id"], token_id):
            raise HTTPException(
                status_code=404,
                detail={"error": "token_not_found"},
            )
        return {"revoked": True, "token_id": token_id}

    # -----------------------------------------------------------------------
    # v2 — BYOK (Bring Your Own Key)
    # -----------------------------------------------------------------------
    # Three routes make up the Account Settings > BYOK section:
    #
    #   POST   /api/v2/auth/byok           — verify + encrypt + store
    #   DELETE /api/v2/auth/byok/{provider} — clear
    #   GET    /api/v2/auth/byok/status    — configured / verified-at
    #
    # Users who have a key stored for a given provider bill that
    # provider directly on every turn (see the header + credit skip in
    # v2_topic_turn / v2_kickoff). The status response NEVER echoes the
    # raw key back — only a boolean and the verified-at timestamp.
    #
    # Rate limits: POST and DELETE are tightened to 10/minute per IP via
    # the per-route slowapi wrapper below. GET is read-only and covered
    # by the global 120/min default.
    _BYOK_VALID_PROVIDERS = {"openai", "anthropic"}

    @app.post("/api/v2/auth/byok", tags=["v2", "auth"])
    async def v2_byok_save(
        body: ByokKeyBody,
        request: Request,  # noqa: ARG001 — slowapi inspects this
        user: dict = Depends(_current_user),
    ) -> dict[str, Any]:
        """Verify the key against the provider and persist the ciphertext.

        The key is POSTed in the clear over HTTPS; it is encrypted at
        rest via ``byok.encrypt_api_key`` and never written to logs.
        Returns ``200 {provider, verified_at}`` — the raw key is NEVER
        echoed back.
        """
        from . import byok as byok_module  # noqa: PLC0415

        provider = (body.provider or "").strip().lower()
        if provider not in _BYOK_VALID_PROVIDERS:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "invalid_provider",
                    "valid": sorted(_BYOK_VALID_PROVIDERS),
                },
            )
        raw_key = (body.api_key or "").strip()
        if not raw_key:
            raise HTTPException(
                status_code=400,
                detail={"error": "api_key_required"},
            )
        # Live verification FIRST so we never persist a bad key. Failure
        # surfaces as 400 with a clear error code so the frontend can show
        # a targeted "that key didn't authenticate" toast.
        if not byok_module.verify_key(provider, raw_key):
            raise HTTPException(
                status_code=400,
                detail={"error": "key_verification_failed", "provider": provider},
            )
        try:
            byok_module.store.set_user_byok(
                _store, user["user_id"], provider, raw_key,
            )
        except RuntimeError as exc:
            # Most likely cause: INSPIRA_BYOK_SECRET missing in this env.
            # Operators see a 503 with a diagnosable message; we don't
            # leak the env-var name in the user-visible error code.
            logger.error("byok save failed (secret misconfig?): %s", exc)
            raise HTTPException(
                status_code=503,
                detail={"error": "byok_unavailable"},
            )
        status_block = byok_module.store.status(_store, user["user_id"])
        return {
            "provider": provider,
            "verified_at": status_block.get(provider, {}).get("last_verified_at"),
        }

    @app.delete("/api/v2/auth/byok/{provider}", tags=["v2", "auth"])
    async def v2_byok_clear(
        provider: str,
        request: Request,  # noqa: ARG001
        user: dict = Depends(_current_user),
    ) -> dict[str, Any]:
        """Clear the stored BYOK for this provider. Returns 200 even when
        nothing was configured, so the UI can be idempotent."""
        from . import byok as byok_module  # noqa: PLC0415

        provider = (provider or "").strip().lower()
        if provider not in _BYOK_VALID_PROVIDERS:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "invalid_provider",
                    "valid": sorted(_BYOK_VALID_PROVIDERS),
                },
            )
        byok_module.store.clear_user_byok(
            _store, user["user_id"], provider,
        )
        return {"provider": provider, "cleared": True}

    @app.get("/api/v2/auth/byok/status", tags=["v2", "auth"])
    def v2_byok_status(user: dict = Depends(_current_user)) -> dict[str, Any]:
        """Return the non-secret BYOK configuration state.

        Shape::

            {
              "openai":    {"configured": bool, "last_verified_at": iso|null},
              "anthropic": {"configured": bool, "last_verified_at": iso|null}
            }

        Never returns the raw key — a caller with the session cookie
        can't exfiltrate the key via this endpoint.
        """
        from . import byok as byok_module  # noqa: PLC0415

        return byok_module.store.status(_store, user["user_id"])

    # Tighten POST / DELETE BYOK rate limits via slowapi. 10/minute is
    # generous for real use — a user pasting their key types it once —
    # and keeps a scripted attacker from using the verify endpoint as a
    # zero-cost key-validity oracle against OpenAI / Anthropic.
    _byok_limiter = getattr(app.state, "limiter", None)
    if _byok_limiter is not None:
        from fastapi.dependencies.utils import (  # noqa: PLC0415
            get_dependant, get_flat_dependant,
        )
        from fastapi.routing import request_response  # noqa: PLC0415

        _byok_route_rates: dict[str, Any] = {
            "/api/v2/auth/byok": _byok_limiter.limit("10/minute"),
            "/api/v2/auth/byok/{provider}": _byok_limiter.limit("10/minute"),
        }
        for _route in app.routes:
            _route_path = getattr(_route, "path", None)
            _route_methods = getattr(_route, "methods", None) or set()
            if _route_path not in _byok_route_rates:
                continue
            # The GET status route also lives at a /byok/... path — skip
            # GET so only the mutating POST/DELETE get the tight 10/min
            # cap. The status endpoint stays on the global 120/min default
            # so the UI can poll without churning the bucket.
            if "GET" in _route_methods and _route_path.endswith("/status"):
                continue
            _wrapped = _byok_route_rates[_route_path](
                _route.endpoint,  # type: ignore[union-attr]
            )
            _route.endpoint = _wrapped  # type: ignore[union-attr]
            _route.dependant = get_dependant(  # type: ignore[union-attr]
                path=_route.path_format,  # type: ignore[union-attr]
                call=_wrapped,
                scope="function",
            )
            _route._flat_dependant = get_flat_dependant(  # type: ignore[attr-defined]
                _route.dependant,  # type: ignore[union-attr]
            )
            _route.app = request_response(  # type: ignore[union-attr]
                _route.get_route_handler(),  # type: ignore[union-attr]
            )

    # -----------------------------------------------------------------------
    # URL-fetch proxy — wraps fetchers/url.py
    # -----------------------------------------------------------------------
    # The frontend used to `fetch(url)` directly from the browser, but
    # CORS blocks that for most sites. This endpoint runs the fetch
    # server-side with a heavy dose of safety guards:
    #
    #   - http(s) only, 2048-char URL cap, no file:/javascript:/data:
    #   - SSRF prevention via DNS pre-resolution + private-range block
    #   - redirects capped at 3 hops, each hop re-validated
    #   - 2 MB response cap, 10s total timeout
    #   - content-type allowlist (text/html, text/plain, application/json,
    #     application/xhtml+xml)
    #
    # Rate limit is inherited from the global slowapi per-IP limit.
    # Per-user cap is tracked in an in-memory counter keyed on
    # (user_id, UTC day) — in-process because the store layer is
    # off-limits for this change. Across process restarts the counter
    # resets, so the real ceiling is the global slowapi rate limit plus
    # whatever's still standing in this worker's dict.

    _url_fetch_counts: dict[tuple[str, str], int] = {}

    def _require_url_fetch_cap(user: dict[str, Any]) -> None:
        cap = _load_user_daily_url_fetch_cap()
        if cap <= 0:
            return
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        user_id = user.get("user_id") or ""
        key = (user_id, day)
        # Periodically drop stale entries so a long-running process
        # doesn't accumulate forever. Cheap pass — only runs when we
        # grow past a modest size.
        if len(_url_fetch_counts) > 1024:
            for stale_key in [k for k in _url_fetch_counts if k[1] != day]:
                _url_fetch_counts.pop(stale_key, None)
        count = _url_fetch_counts.get(key, 0)
        if count >= cap:
            retry_seconds = _seconds_until_utc_midnight()
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    "error": "daily_url_fetch_cap_exhausted",
                    "cap": cap,
                    "count": count,
                    "retry_after_seconds": retry_seconds,
                },
                headers={"Retry-After": str(retry_seconds)},
            )

    def _record_url_fetch(user: dict[str, Any]) -> None:
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        user_id = user.get("user_id") or ""
        key = (user_id, day)
        _url_fetch_counts[key] = _url_fetch_counts.get(key, 0) + 1

    @app.post("/api/v2/fetch-url", tags=["v2"])
    async def v2_fetch_url(
        body: FetchUrlBody,
        request: Request,  # noqa: ARG001 — required for per-route slowapi rate limiting
        user: dict = Depends(_current_user),
    ) -> dict[str, Any]:
        from .fetchers import FetchError, fetch_url_as_source

        raw_url = (body.url or "").strip()
        if not raw_url:
            raise HTTPException(
                status_code=400, detail={"error": "invalid_url"},
            )

        # Per-user daily cap runs BEFORE the fetch so a quota-exhausted
        # user never opens a socket. Count is incremented on attempt
        # (not just success) so retries don't let someone drain our
        # outbound bandwidth.
        _require_url_fetch_cap(user)
        _record_url_fetch(user)

        try:
            source = await fetch_url_as_source(raw_url)
        except FetchError as exc:
            body_payload: dict[str, Any] = {"error": exc.code}
            body_payload.update(exc.extra)
            raise HTTPException(
                status_code=exc.http_status, detail=body_payload,
            )
        except Exception as exc:  # noqa: BLE001
            # Any other exception is an internal bug — don't leak the
            # traceback to the client but do surface a generic 502 so
            # the frontend can show a clean "fetch failed" toast.
            logger.exception("url_fetch internal error: %s", exc)
            raise HTTPException(
                status_code=502, detail={"error": "upstream_error", "status": 0},
            )

        return source

    # -----------------------------------------------------------------------
    # v2 — Entitlements + Code Scaffold
    # -----------------------------------------------------------------------
    # PR 2: the credit ledger is gone. Scaffold (and any other paid feature)
    # is now plan-gated as a boolean — Pro+ unlocks. Endpoints:
    #
    # - GET  /api/v2/entitlements                     → plan + feature list
    # - POST /api/v2/projects/{id}/scaffold           → generate + persist
    # - GET  /api/v2/projects/{id}/scaffolds          → list past scaffolds
    # - GET  /api/v2/scaffolds/{id}/download          → stream zip file
    #
    # Gate semantics: the plan-tier check happens BEFORE the LLM call
    # (no wasted OpenAI spend on a Free-user request); on Pro+ the call
    # proceeds with no metering. The credit-pack purchase flow was
    # deleted along with credits.py — the Noop billing provider was
    # never going to charge real money for packs.

    from . import entitlements as _entitlements

    @app.get("/api/v2/entitlements", tags=["entitlements"])
    def v2_get_entitlements(user: dict = Depends(_current_user)) -> dict[str, Any]:
        """Return the user's current plan + the feature flags that flow
        from it. Single source of truth for the canvas's "is this CTA
        unlocked?" rendering.

        Replaces the legacy ``GET /api/v2/credits`` endpoint. The shape
        intentionally drops every credit-domain field (balance,
        allotment, packs, scaffold_cost, planner_turn_cost) — those
        primitives no longer exist.
        """
        return _entitlements.entitlements_payload(_store, user_id=user["user_id"])

    # Domain tokens that qualify a project for scaffold generation.
    # Must mirror ``SOFTWARE_DOMAIN_TOKENS`` in
    # ``app/src/features/llm-modes/SummaryView.tsx`` so the frontend
    # hide-rule and the backend guard agree.
    _SCAFFOLD_SOFTWARE_DOMAINS: frozenset[str] = frozenset(
        ["software", "product", "app", "tech"]
    )

    @app.post(
        "/api/v2/projects/{project_id}/scaffold",
        status_code=status.HTTP_201_CREATED,
        tags=["scaffolds"],
    )
    def v2_generate_scaffold(
        project_id: str, body: ScaffoldBody = ScaffoldBody(), user: dict = Depends(_current_user),
    ) -> dict[str, Any]:
        """Generate a code scaffold for this project — Pro/Team only.

        Order of operations:
        1. Require project ownership (404 if missing / cross-user).
        2. Reject non-software domains with 422 (BEFORE any credit check).
        3. Require sufficient credit balance (402 if short).
        4. Run the LLM call. On failure: log a ``scaffold_failed``
           ledger entry (no debit) and surface the generic 500.
        5. Debit the cost as a single ledger row tied to the new
           ``scaffold_id`` reference.
        6. Persist the scaffold row with its full manifest.
        """
        _require_owned_project(project_id, user)
        _require_token_budget(user)

        # Product decision: code-gen runs for any owned
        # project — autonomous-pipeline projects (paste-feedback →
        # orchestrator) carry no `metadata.domain` and the legacy
        # software-domain 422 gate would block every one of them.
        # Entitlements remain the only paid-tier gate below.

        # Plan-tier gate (replaces the old credit-balance preflight).
        # Free users get the structured 402 the frontend uses to render
        # an upgrade-CTA modal.
        if not _entitlements.has_feature(
            _store, user_id=user["user_id"], feature="scaffold",
        ):
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail={
                    "error": "upgrade_required",
                    "feature": "scaffold",
                    "min_plan": "pro",
                    "upgrade_url": "/pricing",
                },
            )

        topics = _store.list_topics(project_id=project_id, user_id=user["user_id"])
        decisions = _store.list_decisions(
            project_id=project_id, user_id=user["user_id"],
        )
        # Read the latest summary version if one exists — the scaffold
        # leans heavily on it. Ownership is already established by
        # _require_owned_project above, so a direct project-keyed read
        # is safe. Missing is fine; the prompt degrades gracefully.
        try:
            summary_row = _store.latest_summary_version(project_id=project_id)
        except Exception:  # noqa: BLE001 — defensive; store change-safe
            summary_row = None
        summary_markdown = ""
        if summary_row and isinstance(summary_row, dict):
            summary_markdown = (summary_row.get("content_markdown") or "").strip()

        project_title = _resolve_project_title(project_id, user)

        try:
            adapter = _get_code_scaffold_adapter()
            manifest = adapter.generate(
                project_title=project_title,
                summary_markdown=summary_markdown,
                topics=topics,
                decisions=decisions,
                locale=_validate_locale(body.locale),
            )
        except RuntimeError as exc:
            # LLM failure — surface the generic planner-error envelope.
            # Pre-PR2 this also logged a zero-delta credit_transactions
            # row; with credits gone, the application metrics
            # (Sentry + structured logs from _planner_error_response)
            # are the audit trail.
            raise _planner_error_response(exc)

        # Usage bookkeeping for the daily token gate (same as the other
        # LLM modes). Chars/4 estimate since the adapter doesn't surface
        # OpenAI usage stats directly.
        _record_llm_usage(
            user,
            prompt_text=project_title + " " + (summary_markdown or "") + " " + " ".join(
                (d.get("statement") or "") for d in decisions
            ),
            response_text=str(manifest),
        )

        import json as _json
        row = _store.create_scaffold(
            project_id=project_id,
            user_id=user["user_id"],
            framework=str(manifest.get("framework") or "other"),
            language=str(manifest.get("language") or "typescript"),
            manifest_json=_json.dumps(manifest),
        )

        # Strip the full file content from the response — the UI walks
        # the file tree without needing the bytes, and we'd rather not
        # ship a 400KB JSON response. The zip download endpoint is the
        # canonical content delivery path.
        file_headers = [
            {
                "path": f["path"],
                "size": len(f.get("content", "")),
            }
            for f in manifest.get("files", [])
        ]
        return {
            "scaffold": {
                "scaffold_id": row["scaffold_id"],
                "project_id": row["project_id"],
                "framework": row["framework"],
                "language": row["language"],
                "created_at": row["created_at"],
                "readme_preview": manifest.get("readme_preview") or "",
                "post_install_steps": manifest.get("post_install_steps") or [],
                "truncation_note": manifest.get("truncation_note") or "",
                "file_count": len(manifest.get("files") or []),
                "files": file_headers,
            },
        }

    @app.get("/api/v2/projects/{project_id}/scaffolds", tags=["scaffolds"])
    def v2_list_project_scaffolds(
        project_id: str, user: dict = Depends(_current_user),
    ) -> dict[str, Any]:
        """All past scaffolds for this project, most-recent first."""
        _require_owned_project(project_id, user)
        rows = _store.list_scaffolds_for_project(
            project_id=project_id, user_id=user["user_id"],
        )
        return {"scaffolds": rows}

    @app.get("/api/v2/scaffolds/{scaffold_id}/download", tags=["scaffolds"])
    def v2_download_scaffold(
        scaffold_id: str, user: dict = Depends(_current_user),
    ):
        """Stream the scaffold as a zip. 404 on missing or cross-user access.

        The zip is built fresh each call from the stored manifest — no
        intermediate files on disk. Streaming keeps peak memory low for
        scaffolds at the 40-file cap (around ~1.6 MB of text worst-case).
        """
        row = _store.get_scaffold(
            scaffold_id=scaffold_id, user_id=user["user_id"],
        )
        if row is None:
            raise HTTPException(
                status_code=404, detail={"error": "scaffold_not_found"},
            )
        import io
        import json as _json
        import zipfile
        from datetime import datetime as _dt, timezone as _tz
        from fastapi.responses import StreamingResponse

        try:
            manifest = _json.loads(row["manifest_json"] or "{}")
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=500, detail={"error": "corrupt_manifest"},
            )
        files = manifest.get("files") or []

        # Path-safety guard — the manifest was already sanitized on
        # generate, but zip-writing is an interesting privilege and the
        # cost of re-checking is trivial. Keeps the invariant
        # "never escape the project root" in one more place.
        from .agents.code_scaffold import _is_safe_path as _safe

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for entry in files:
                path = entry.get("path", "")
                content = entry.get("content", "")
                if not _safe(path):
                    continue
                if not isinstance(content, str):
                    continue
                zf.writestr(path, content)
        buf.seek(0)

        # Build a timestamped filename using the project title (slug) so
        # the user can tell multiple downloads apart in their Downloads
        # folder.
        project_title = _resolve_project_title(row["project_id"], user)
        slug = _slugify_for_filename(project_title) or "scaffold"
        now = _dt.now(_tz.utc).strftime("%Y%m%d-%H%M%S")
        filename = f"{slug}-scaffold-{now}.zip"

        return StreamingResponse(
            iter([buf.getvalue()]),
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                # Explicit Content-Length lets the browser show a real
                # progress bar instead of an indeterminate spinner.
                "Content-Length": str(buf.getbuffer().nbytes),
            },
        )

    # -----------------------------------------------------------------------
    # v2 — Artifact Viewer
    # -----------------------------------------------------------------------
    # Three endpoints that wrap the existing scaffold core into the
    # canvas → shipped-code "artifact" surface:
    #
    # - POST /api/v2/projects/{id}/artifact/generate/stream  (SSE)
    # - GET  /api/v2/projects/{id}/artifact
    # - POST /api/v2/projects/{id}/artifact/edit/stream      (SSE)
    #
    # Gated on ``project_state == "approved"`` (the project state machine is the
    # only legal writer for that field — these endpoints only read it).
    # Tier-dispatched: FRONTIER/ENTERPRISE → claude_code_scaffold_adapter
    # pinned to ``CLAUDE_CODEGEN_MODEL`` (Opus 4.7); BASE/PRO → OpenAI
    # scaffold adapter pinned per ``tier_to_openai_model``.

    def _artifact_thinking_progress_script() -> tuple[str, ...]:
        return (
            "artifact.scaffolding",      # 0s
            "artifact.writing_files",    # 3s
            "artifact.connecting_pieces",  # 6s
            "artifact.polishing",        # 9s
            "common.taking_a_moment",    # 12s
            "common.still_working",      # 15s
            "common.still_working_long",  # 18s+
        )

    # Hard ceiling of ~3-min wall clock for code-gen. Each tick is ~3s,
    # so 60 ticks ≈ 180s. Frontier scaffold typically lands at 30-90s;
    # the cap is for stuck-LLM cases where the canvas would otherwise
    # spin forever.
    _ARTIFACT_HARD_TIMEOUT_TICKS = 60

    def _artifact_resolve_dispatch(
        user_id: str,
    ) -> tuple[Any, str]:
        """Resolve (selected_adapter, model_override).

        Honors the user's persisted ``preferred_model_tier``. Pins
        ``CLAUDE_CODEGEN_MODEL`` for the Claude path; falls through to
        ``tier_to_openai_model`` on the OpenAI path. ``model_override``
        doubles as the ``model_used`` label persisted on the artifact
        overlay.
        """
        from .agents.tiers import (  # noqa: PLC0415
            CLAUDE_CODEGEN_MODEL,
            ModelTier,
            parse_tier,
            resolve_tier_for_user,
            tier_to_adapter,
            tier_to_openai_model,
        )

        resolved_tier = resolve_tier_for_user(
            _store, user_id, parse_tier(None),
        )
        openai_adapter = _get_code_scaffold_adapter()
        claude_adapter = _get_claude_code_scaffold_adapter()
        selected = tier_to_adapter(
            resolved_tier,
            openai_adapter=openai_adapter,
            claude_adapter=claude_adapter,
        )
        is_claude = (
            claude_adapter is not None and selected is claude_adapter
        )
        if is_claude:
            model_override = CLAUDE_CODEGEN_MODEL
        else:
            model_override = tier_to_openai_model(resolved_tier)
        return selected, model_override

    def _artifact_assert_software_and_entitled(
        project_id: str, user: dict[str, Any],
    ) -> dict[str, Any]:
        """Shared pre-flight: ownership + domain gate + entitlements.

        Product decision: the **artifact (code) IS the
        thing that gets reviewed and approved** — not the canvas. The
        canvas is upstream creative input; users can open the artifact
        viewer and generate code at any project_state. The
        ApprovalChip on the viewer's top bar handles Draft → In Review
        → Approved transitions on the *artifact*. Removed the
        project_state="approved" gate that previously 409'd the
        viewer for any pre-approval card.

        Returns the project row on success. Raises HTTPException with
        the structured error envelope on failure. The 404-not-403 rule
        for cross-workspace requests already lives inside
        ``_require_owned_project``.
        """
        _require_owned_project(project_id, user)
        project_row = _store._get_v2_project(project_id)  # noqa: SLF001
        if project_row is None:
            # Defensive — _require_owned_project already raises 404 in
            # this case, but the type-checker can't see that without
            # mypy hints, and ``project_row`` is dereferenced below.
            raise HTTPException(
                status_code=404, detail={"error": "project_not_found"},
            )
        # Product decision: the artifact (code) IS the
        # deliverable for every workspace project — autonomous-pipeline
        # projects (paste-feedback → orchestrator) don't carry a
        # `metadata.domain` and the legacy software-domain gate would
        # 422 every one of them. Code-gen is now allowed for any
        # owned project; entitlements remain the only paid-tier gate.
        if not _entitlements.has_feature(
            _store, user_id=user["user_id"], feature="scaffold",
        ):
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail={
                    "error": "upgrade_required",
                    "feature": "scaffold",
                    "min_plan": "pro",
                    "upgrade_url": "/pricing",
                },
            )
        return project_row

    def _artifact_replay_cached_if_present(
        *, project_id: str, user: dict[str, Any],
    ) -> StreamingResponse | None:
        """If a manifest is already persisted for this project, replay it
        as a single ``complete`` SSE frame so the impatient-race window
        (#187) costs 1× LLM spend instead of 2×.

        Returns None when no scaffold row exists (or the persisted
        manifest is corrupt) so the caller falls through to the fresh
        ``adapter.generate`` path.

        The replay envelope mirrors the success path's shape verbatim
        — the FE ``ssePost`` reader resolves on the first ``complete``
        event, so any consumer of ``generateArtifactStream`` sees the
        same callback shape whether the response is cached or fresh.
        """
        import json as _json  # noqa: PLC0415

        existing = _store.get_v2_project_artifact(project_id=project_id)
        scaffold_id = existing.get("latest_scaffold_id") if existing else None
        if not scaffold_id:
            return None
        scaffold_row = _store.get_scaffold(
            scaffold_id=str(scaffold_id), user_id=user["user_id"],
        )
        if scaffold_row is None:
            return None
        try:
            cached_manifest = _json.loads(scaffold_row["manifest_json"] or "{}")
        except (TypeError, ValueError):
            return None
        if not cached_manifest:
            return None
        envelope = {
            "artifact": {
                "latest_scaffold_id": scaffold_id,
                "model_used": (existing or {}).get("model_used"),
                "framework": str(
                    cached_manifest.get("framework") or "other",
                ),
                "language": str(
                    cached_manifest.get("language") or "typescript",
                ),
                "files": [
                    {
                        "path": entry.get("path", ""),
                        "content": entry.get("content", ""),
                    }
                    for entry in (cached_manifest.get("files") or [])
                    if isinstance(entry, dict)
                ],
                "messages": (existing or {}).get("messages") or [],
            },
        }

        async def _cached_generator():
            yield format_sse("complete", envelope)

        return sse_stream(
            _cached_generator(),
            extra_headers={"x-llm-mode": "cached"},
        )

    @app.post(
        "/api/v2/projects/{project_id}/artifact/generate/stream",
        tags=["artifact"],
    )
    def v2_artifact_generate_stream(
        project_id: str,
        body: ArtifactGenerateBody | None = None,
        user: dict = Depends(_current_user),
    ):
        """Generate the artifact for an approved canvas. Streamed via SSE.

        Idempotency (#187): when ``body.force`` is falsy and a manifest
        already exists for this project, replay it as a cached
        ``complete`` event instead of firing a fresh
        ``adapter.generate`` call. The Regenerate kebab on the viewer
        passes ``force=true`` to bypass this and re-run the LLM.
        """
        project_row = _artifact_assert_software_and_entitled(project_id, user)
        project_workspace_id = (
            project_row.get("workspace_id") if project_row else None
        )
        _require_token_budget(user)
        force = bool(body and body.force)

        if not force:
            cached_response = _artifact_replay_cached_if_present(
                project_id=project_id, user=user,
            )
            if cached_response is not None:
                return cached_response

        topics = _store.list_topics(
            project_id=project_id, user_id=user["user_id"],
        )
        decisions = _store.list_decisions(
            project_id=project_id, user_id=user["user_id"],
        )
        try:
            summary_row = _store.latest_summary_version(project_id=project_id)
        except Exception:  # noqa: BLE001
            summary_row = None
        summary_markdown = ""
        if summary_row and isinstance(summary_row, dict):
            summary_markdown = (summary_row.get("content_markdown") or "").strip()
        project_title = _resolve_project_title(project_id, user)
        validated_locale = _validate_locale(None)

        selected_adapter, model_override = _artifact_resolve_dispatch(
            user["user_id"],
        )
        model_used_label = model_override
        progress_script = _artifact_thinking_progress_script()

        async def _generator():
            import asyncio  # noqa: PLC0415
            import json as _json  # noqa: PLC0415

            yield format_sse(
                "heartbeat",
                {
                    "status": "thinking",
                    "message": thinking_message(
                        progress_script[0], validated_locale,
                    ),
                    "elapsed_s": 0,
                },
            )
            try:
                # Wave F.1 — ground the LLM on real repo files when the
                # project has a connected GitHub repo. Silently degrades
                # to None when no connector is wired / token expired /
                # repo metadata missing — never 5xx the artifact-gen
                # flow on a grounding-fetch failure. Mirrors the
                # canonical pattern at orchestrator_router.py:473-483.
                from .connectors.github.repo_context import (  # noqa: PLC0415
                    fetch_repo_context,
                )

                repo_context: dict | None = None
                if project_workspace_id:
                    try:
                        repo_context = await fetch_repo_context(
                            _store,
                            workspace_id=project_workspace_id,
                            timeout_s=12.0,
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.info(
                            "[artifact] repo_context unavailable "
                            "for project=%s: %s",
                            project_id,
                            exc,
                        )
                        repo_context = None
                if repo_context:
                    logger.info(
                        "[scaffold] repo_context attached: %s",
                        repo_context.get("repo_full_name"),
                    )

                loop = asyncio.get_running_loop()
                gen_task = loop.run_in_executor(
                    None,
                    lambda: selected_adapter.generate(
                        project_title=project_title,
                        summary_markdown=summary_markdown,
                        topics=topics,
                        decisions=decisions,
                        locale=validated_locale,
                        model_override=model_override,
                        repo_context=repo_context,
                    ),
                )
                step = 0
                while not gen_task.done():
                    if step >= _ARTIFACT_HARD_TIMEOUT_TICKS:
                        gen_task.cancel()
                        yield format_sse(
                            "error",
                            {
                                "code": "artifact_generation_timeout",
                                "message": (
                                    "Code generation took too long. "
                                    "Try regenerating from the chat."
                                ),
                                "elapsed_s": step * 3,
                            },
                        )
                        return
                    try:
                        await asyncio.wait_for(
                            asyncio.shield(gen_task), timeout=3.0,
                        )
                    except asyncio.TimeoutError:
                        key = progress_script[
                            min(step, len(progress_script) - 1)
                        ]
                        yield format_sse(
                            "heartbeat",
                            {
                                "status": "thinking",
                                "message": thinking_message(
                                    key, validated_locale,
                                ),
                                "elapsed_s": step * 3,
                            },
                        )
                        step += 1
                manifest = gen_task.result()
            except RuntimeError as exc:
                rid_exc = _planner_error_response(exc)
                detail = (
                    rid_exc.detail if isinstance(rid_exc.detail, dict)
                    else {"error": "planner_call_failed"}
                )
                yield format_sse(
                    "error",
                    {
                        "code": "planner_error",
                        "message": "The artifact writer failed to respond.",
                        "detail": detail,
                    },
                )
                return
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "[v2_artifact_generate_stream] adapter.generate failed",
                )
                yield format_sse(
                    "error",
                    {
                        "code": "planner_error",
                        "message": str(exc) or "Unexpected planner failure",
                    },
                )
                return

            try:
                _record_llm_usage(
                    user,
                    prompt_text=(
                        project_title + " " + (summary_markdown or "") + " "
                        + " ".join(
                            (d.get("statement") or "") for d in decisions
                        )
                    ),
                    response_text=str(manifest),
                )

                row = _store.create_scaffold(
                    project_id=project_id,
                    user_id=user["user_id"],
                    framework=str(manifest.get("framework") or "other"),
                    language=str(manifest.get("language") or "typescript"),
                    manifest_json=_json.dumps(manifest),
                )
                files = manifest.get("files") or []
                file_count = len(files)
                framework = str(manifest.get("framework") or "other")

                # Synthesize the first assistant chat message — generate
                # mode's schema doesn't include an explanation field, so
                # we surface a deterministic opener that names the
                # framework + file count. Edit mode persists the model's
                # actual ``explanation`` paragraph instead.
                opener = (
                    f"I drafted a {framework} scaffold with {file_count} "
                    "file(s). Open any file on the left, or ask me to "
                    "tweak it via the chat."
                )
                artifact_overlay = {
                    "version": 1,
                    "latest_scaffold_id": row["scaffold_id"],
                    "model_used": model_used_label,
                    "messages": [
                        {
                            "role": "assistant",
                            "body": opener,
                            "ts": row["created_at"],
                        },
                    ],
                }
                _store.set_v2_project_artifact(
                    project_id=project_id, artifact=artifact_overlay,
                )

                envelope = {
                    "artifact": {
                        "latest_scaffold_id": row["scaffold_id"],
                        "model_used": model_used_label,
                        "framework": framework,
                        "language": str(
                            manifest.get("language") or "typescript",
                        ),
                        "files": [
                            {
                                "path": f["path"],
                                "content": f.get("content", ""),
                            }
                            for f in files
                        ],
                        "messages": artifact_overlay["messages"],
                    },
                }
                yield format_sse("complete", envelope)
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "[v2_artifact_generate_stream] persist failed",
                )
                yield format_sse(
                    "error",
                    {
                        "code": "artifact_persist_failed",
                        "message": str(exc) or "Failed to persist artifact",
                    },
                )

        return sse_stream(
            _generator(),
            extra_headers={"x-llm-mode": "house"},
        )

    @app.get(
        "/api/v2/projects/{project_id}/artifact",
        tags=["artifact"],
    )
    def v2_get_artifact(
        project_id: str, user: dict = Depends(_current_user),
    ) -> dict[str, Any]:
        """Read the persisted artifact (overlay + hydrated files).

        Cross-workspace request returns 404 (not 403) — same pattern
        as the transition endpoint. Don't leak existence.
        """
        _require_owned_project(project_id, user)
        artifact = _store.get_v2_project_artifact(project_id=project_id)
        if artifact is None:
            raise HTTPException(
                status_code=404,
                detail={"error": "artifact_not_generated"},
            )
        scaffold_id = artifact.get("latest_scaffold_id")
        files: list[dict[str, Any]] = []
        framework = "other"
        language = "typescript"
        if scaffold_id:
            row = _store.get_scaffold(
                scaffold_id=str(scaffold_id), user_id=user["user_id"],
            )
            if row is not None:
                import json as _json  # noqa: PLC0415

                try:
                    manifest = _json.loads(row["manifest_json"] or "{}")
                except (TypeError, ValueError):
                    manifest = {}
                framework = str(manifest.get("framework") or framework)
                language = str(manifest.get("language") or language)
                for entry in manifest.get("files") or []:
                    if not isinstance(entry, dict):
                        continue
                    files.append(
                        {
                            "path": entry.get("path", ""),
                            "content": entry.get("content", ""),
                        },
                    )
        return {
            "artifact": {
                "latest_scaffold_id": scaffold_id,
                "model_used": artifact.get("model_used"),
                "framework": framework,
                "language": language,
                "files": files,
                "messages": artifact.get("messages") or [],
            },
        }

    @app.patch(
        "/api/v2/projects/{project_id}/artifact/files",
        tags=["artifact"],
    )
    def v2_artifact_patch_file(
        project_id: str,
        body: dict,
        user: dict = Depends(_current_user),
    ) -> dict[str, Any]:
        """Autosave one file's content into the project's latest scaffold.

        Body: ``{"path": "src/App.tsx", "content": "..."}``. The Code
        tab's editable textarea debounce-PATCHes here on each
        keystroke so edits survive page reloads + show up in the
        Preview embed on next open. Locked when project is past
        Draft (chip = In Review / Approved).
        """
        _require_owned_project(project_id, user)
        path = body.get("path")
        content = body.get("content")
        if not isinstance(path, str) or not path:
            raise HTTPException(
                status_code=400, detail={"error": "path_required"},
            )
        if not isinstance(content, str):
            raise HTTPException(
                status_code=400, detail={"error": "content_required"},
            )
        # Only Draft projects can autosave — match the FE's readOnly
        # gate. In Review / Approved are immutable until the user
        # transitions back via ApprovalChip.
        project_row = _store._get_v2_project(project_id)  # noqa: SLF001
        project_state = (project_row or {}).get(
            "project_state",
        ) or "pending_review"
        if project_state in ("in_review", "approved"):
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "project_locked",
                    "current_state": project_state,
                    "message": (
                        "Edit again from the Approval chip to unlock."
                    ),
                },
            )
        artifact = _store.get_v2_project_artifact(project_id=project_id)
        if artifact is None:
            raise HTTPException(
                status_code=404, detail={"error": "artifact_not_generated"},
            )
        scaffold_id = artifact.get("latest_scaffold_id")
        if not scaffold_id:
            raise HTTPException(
                status_code=404, detail={"error": "scaffold_not_found"},
            )
        ok = _store.update_scaffold_file_content(
            scaffold_id=str(scaffold_id),
            user_id=user["user_id"],
            path=path,
            content=content,
        )
        if not ok:
            raise HTTPException(
                status_code=404, detail={"error": "file_not_found"},
            )
        return {"ok": True, "saved_at": now_timestamp()}

    def _artifact_assert_draft_for_file_mgmt(
        project_id: str, user: dict[str, Any],
    ) -> tuple[dict[str, Any], str]:
        """Shared pre-flight for file create/rename/delete routes.

        Returns ``(artifact, scaffold_id)``. Raises 404 / 409 / 400 as
        appropriate. File mgmt is locked when project_state is
        in_review or approved, matching the autosave PATCH gate.
        """
        _require_owned_project(project_id, user)
        project_row = _store._get_v2_project(project_id)  # noqa: SLF001
        project_state = (project_row or {}).get(
            "project_state",
        ) or "pending_review"
        if project_state in ("in_review", "approved"):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "project_locked",
                    "current_state": project_state,
                    "message": (
                        "Edit again from the Approval chip to unlock."
                    ),
                },
            )
        artifact = _store.get_v2_project_artifact(project_id=project_id)
        if artifact is None:
            raise HTTPException(
                status_code=404, detail={"error": "artifact_not_generated"},
            )
        scaffold_id = artifact.get("latest_scaffold_id")
        if not scaffold_id:
            raise HTTPException(
                status_code=404, detail={"error": "scaffold_not_found"},
            )
        return artifact, str(scaffold_id)

    @app.post(
        "/api/v2/projects/{project_id}/artifact/files",
        status_code=status.HTTP_201_CREATED,
        tags=["artifact"],
    )
    def v2_artifact_create_file(
        project_id: str,
        body: dict,
        user: dict = Depends(_current_user),
    ) -> dict[str, Any]:
        """Create a new empty file in the project's scaffold.

        Body: ``{"path": "src/NewFile.tsx", "content"?: "..."}``.
        Returns 409 if a file with the same path already exists.
        """
        path = body.get("path")
        content = body.get("content", "")
        if not isinstance(path, str) or not path:
            raise HTTPException(
                status_code=400, detail={"error": "path_required"},
            )
        if not isinstance(content, str):
            raise HTTPException(
                status_code=400, detail={"error": "content_required"},
            )
        _, scaffold_id = _artifact_assert_draft_for_file_mgmt(
            project_id, user,
        )
        result = _store.add_scaffold_file(
            scaffold_id=scaffold_id,
            user_id=user["user_id"],
            path=path,
            content=content,
        )
        if result is None:
            raise HTTPException(
                status_code=404, detail={"error": "scaffold_not_found"},
            )
        if result == "exists":
            raise HTTPException(
                status_code=409,
                detail={"error": "file_exists", "path": path},
            )
        return {"ok": True, "path": path, "saved_at": now_timestamp()}

    @app.patch(
        "/api/v2/projects/{project_id}/artifact/files/rename",
        tags=["artifact"],
    )
    def v2_artifact_rename_file(
        project_id: str,
        body: dict,
        user: dict = Depends(_current_user),
    ) -> dict[str, Any]:
        """Rename / move one file in the scaffold.

        Body: ``{"old_path": "src/A.tsx", "new_path": "src/B.tsx"}``.
        Move-into-folder works by including the new folder in the
        path (no separate folder concept).
        """
        old_path = body.get("old_path")
        new_path = body.get("new_path")
        if not isinstance(old_path, str) or not old_path:
            raise HTTPException(
                status_code=400, detail={"error": "old_path_required"},
            )
        if not isinstance(new_path, str) or not new_path:
            raise HTTPException(
                status_code=400, detail={"error": "new_path_required"},
            )
        if old_path == new_path:
            return {"ok": True, "path": new_path}
        _, scaffold_id = _artifact_assert_draft_for_file_mgmt(
            project_id, user,
        )
        result = _store.rename_scaffold_file(
            scaffold_id=scaffold_id,
            user_id=user["user_id"],
            old_path=old_path,
            new_path=new_path,
        )
        if result is None:
            raise HTTPException(
                status_code=404, detail={"error": "scaffold_not_found"},
            )
        if result == "not_found":
            raise HTTPException(
                status_code=404,
                detail={"error": "file_not_found", "path": old_path},
            )
        if result == "exists":
            raise HTTPException(
                status_code=409,
                detail={"error": "file_exists", "path": new_path},
            )
        return {"ok": True, "path": new_path, "saved_at": now_timestamp()}

    @app.delete(
        "/api/v2/projects/{project_id}/artifact/files",
        tags=["artifact"],
    )
    def v2_artifact_delete_file(
        project_id: str,
        path: str,
        user: dict = Depends(_current_user),
    ) -> dict[str, Any]:
        """Delete one file from the scaffold (path passed as query param)."""
        if not path:
            raise HTTPException(
                status_code=400, detail={"error": "path_required"},
            )
        _, scaffold_id = _artifact_assert_draft_for_file_mgmt(
            project_id, user,
        )
        ok = _store.delete_scaffold_file(
            scaffold_id=scaffold_id,
            user_id=user["user_id"],
            path=path,
        )
        if not ok:
            raise HTTPException(
                status_code=404,
                detail={"error": "file_not_found", "path": path},
            )
        return {"ok": True, "path": path, "saved_at": now_timestamp()}

    # ------------------------------------------------------------------
    # PR-overlay tree + file (Wave F.3) — backs the dual-folder
    # `main/` + `PRs/<category>/<slug>/` view in the artifact-viewer
    # left rail. Owner-only auth matches the surrounding artifact CRUD.
    # ------------------------------------------------------------------

    @app.get(
        "/api/v2/projects/{project_id}/pr-overlay-tree",
        tags=["artifact"],
    )
    async def v2_pr_overlay_tree(
        project_id: str,
        user: dict = Depends(_current_user),
    ) -> dict[str, Any]:
        """Return the project's base-repo tree with the latest scaffold
        overlaid. Each entry tagged source: ``base|scaffold|modified``.

        404 ``project_not_found`` if missing or unowned (matches the
        surrounding artifact routes); 503 if GitHub App secrets aren't
        configured on this deployment.
        """
        _require_owned_project(project_id, user)
        from .connectors.github.oauth import (  # noqa: PLC0415
            load_app_config_from_env,
        )
        from .connectors.github.pr_overlay import (  # noqa: PLC0415
            PrOverlayError,
            build_overlay_tree,
        )
        from .connectors.github.repo_browse import (  # noqa: PLC0415
            RepoBrowseError,
        )

        configs = load_app_config_from_env()
        if configs is None:
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "github_not_configured",
                    "message": (
                        "GitHub App secrets are not set on the "
                        "deployment. Contact the workspace admin."
                    ),
                },
            )
        app_config, _ = configs
        try:
            return await build_overlay_tree(
                _store,
                project_id=project_id,
                user_id=user["user_id"],
                app_config=app_config,
            )
        except (PrOverlayError, RepoBrowseError) as exc:
            raise HTTPException(
                status_code=exc.status_code, detail=exc.detail,
            ) from exc

    @app.get(
        "/api/v2/projects/{project_id}/pr-overlay-file",
        tags=["artifact"],
    )
    async def v2_pr_overlay_file(
        project_id: str,
        path: str,
        user: dict = Depends(_current_user),
    ) -> dict[str, Any]:
        """Return scaffold content for ``scaffold`` / ``modified`` paths;
        ``source: "base"`` sentinel otherwise so the FE falls through to
        F.2's /repo/file route. Server-side redirects are deliberately
        avoided to keep the two cache layers cleanly separated.
        """
        if not isinstance(path, str) or not path:
            raise HTTPException(
                status_code=400, detail={"error": "path_required"},
            )
        _require_owned_project(project_id, user)
        from .connectors.github.oauth import (  # noqa: PLC0415
            load_app_config_from_env,
        )
        from .connectors.github.pr_overlay import (  # noqa: PLC0415
            PrOverlayError,
            fetch_overlay_file,
        )
        from .connectors.github.repo_browse import (  # noqa: PLC0415
            RepoBrowseError,
        )

        configs = load_app_config_from_env()
        if configs is None:
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "github_not_configured",
                    "message": (
                        "GitHub App secrets are not set on the "
                        "deployment. Contact the workspace admin."
                    ),
                },
            )
        app_config, _ = configs
        try:
            return await fetch_overlay_file(
                _store,
                project_id=project_id,
                user_id=user["user_id"],
                path=path,
                app_config=app_config,
            )
        except (PrOverlayError, RepoBrowseError) as exc:
            raise HTTPException(
                status_code=exc.status_code, detail=exc.detail,
            ) from exc

    @app.get(
        "/api/v2/projects/{project_id}/pr-overlay-staleness",
        tags=["artifact"],
    )
    async def v2_pr_overlay_staleness(
        project_id: str,
        user: dict = Depends(_current_user),
    ) -> dict[str, Any]:
        """Return drift between the recorded base main SHA and current
        main, intersected with this project's scaffold paths.

        Pre-F.5 projects (no recorded base SHA) get a ``legacy=True,
        is_stale=False`` payload back; the row self-heals on the next
        ``/pr-overlay-tree`` call via the write-through in
        ``build_overlay_tree``. 503 ``github_not_configured`` when the
        deployment's GitHub App secrets are missing, matching the
        surrounding overlay routes.
        """
        _require_owned_project(project_id, user)
        from .connectors.github.oauth import (  # noqa: PLC0415
            load_app_config_from_env,
        )
        from .connectors.github.pr_overlay import (  # noqa: PLC0415
            PrOverlayError,
        )
        from .connectors.github.repo_browse import (  # noqa: PLC0415
            RepoBrowseError,
        )
        from .connectors.github.staleness import (  # noqa: PLC0415
            compute_staleness,
        )

        configs = load_app_config_from_env()
        if configs is None:
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "github_not_configured",
                    "message": (
                        "GitHub App secrets are not set on the "
                        "deployment. Contact the workspace admin."
                    ),
                },
            )
        app_config, _ = configs
        try:
            return await compute_staleness(
                _store,
                project_id=project_id,
                user_id=user["user_id"],
                app_config=app_config,
            )
        except (PrOverlayError, RepoBrowseError) as exc:
            raise HTTPException(
                status_code=exc.status_code, detail=exc.detail,
            ) from exc

    # ------------------------------------------------------------------
    # Wave F.6 — "Refresh PR with Inspira" + 3-way diff (#147)
    # ------------------------------------------------------------------

    @app.post(
        "/api/v2/projects/{project_id}/refresh-overlay",
        tags=["artifact"],
    )
    async def v2_refresh_overlay(
        project_id: str,
        user: dict = Depends(_current_user),
    ) -> dict[str, Any]:
        """Re-run the scaffold adapter against fresh main + previous draft.

        Owner-only. Returns ``{scaffold_id, refresh_id, base_main_sha,
        changed_paths, changed_count}`` on success. 409
        ``refresh_in_progress`` when a prior refresh for the same
        project is still running. 503 ``github_not_configured`` when
        the deployment's GitHub App secrets are missing.

        Adapter dispatch follows the same tier rule as the initial
        scaffold generation: FRONTIER/ENTERPRISE → Claude pinned to
        ``CLAUDE_CODEGEN_MODEL``; BASE/PRO → OpenAI scaffold adapter.
        """
        _require_owned_project(project_id, user)
        from .agents.refresh_pr import (  # noqa: PLC0415
            refresh_pr_overlay,
        )
        from .connectors.github.oauth import (  # noqa: PLC0415
            load_app_config_from_env,
        )
        from .connectors.github.pr_overlay import (  # noqa: PLC0415
            PrOverlayError,
        )
        from .connectors.github.repo_browse import (  # noqa: PLC0415
            RepoBrowseError,
        )

        configs = load_app_config_from_env()
        if configs is None:
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "github_not_configured",
                    "message": (
                        "GitHub App secrets are not set on the "
                        "deployment. Contact the workspace admin."
                    ),
                },
            )
        app_config, _ = configs
        selected_adapter, model_override = _artifact_resolve_dispatch(
            user["user_id"],
        )
        try:
            return await refresh_pr_overlay(
                _store,
                project_id=project_id,
                user_id=user["user_id"],
                app_config=app_config,
                scaffold_adapter=selected_adapter,
                model_override=model_override,
            )
        except (PrOverlayError, RepoBrowseError) as exc:
            raise HTTPException(
                status_code=exc.status_code, detail=exc.detail,
            ) from exc

    @app.get(
        "/api/v2/projects/{project_id}/refresh-diff",
        tags=["artifact"],
    )
    async def v2_refresh_diff(
        project_id: str,
        refresh_id: str,
        user: dict = Depends(_current_user),
    ) -> dict[str, Any]:
        """Return per-file 3-way diff (or 2-way fallback) for a refresh.

        Owner-only. Each ``files`` entry is ``{path, base,
        partner_edit, ai_redraft, conflict}``:

        * ``base`` = the file's ``original_content`` (captured on
          first partner edit) when non-null, else the previous
          scaffold's ``content`` (AI-original baseline).
        * ``partner_edit`` = the previous scaffold's ``content`` when
          ``original_content`` is non-null (i.e. the file was edited
          since generation); else ``null`` and the FE renders a
          2-way diff.
        * ``ai_redraft`` = the new scaffold's ``content``.
        * ``conflict`` = True iff partner_edit is non-null AND
          partner_edit != ai_redraft AND original_content != ai_redraft.

        Returns 404 if the refresh row or its scaffolds don't exist.
        """
        _require_owned_project(project_id, user)
        refresh_row = _store.get_scaffold_refresh_history(
            refresh_id=refresh_id,
        )
        if refresh_row is None or refresh_row["project_id"] != project_id:
            raise HTTPException(
                status_code=404,
                detail={"error": "refresh_not_found"},
            )

        previous_id = refresh_row.get("previous_scaffold_id")
        new_id = refresh_row.get("new_scaffold_id")
        user_id = user["user_id"]

        previous_files = _refresh_diff_files_for_scaffold(
            previous_id, user_id,
        )
        new_files = _refresh_diff_files_for_scaffold(
            new_id, user_id,
        )

        prev_by_path: dict[str, dict[str, Any]] = {
            e["path"]: e
            for e in previous_files
            if isinstance(e, dict) and isinstance(e.get("path"), str)
        }
        new_by_path: dict[str, dict[str, Any]] = {
            e["path"]: e
            for e in new_files
            if isinstance(e, dict) and isinstance(e.get("path"), str)
        }

        all_paths = sorted(
            set(prev_by_path.keys()) | set(new_by_path.keys()),
        )
        files_payload: list[dict[str, Any]] = []
        for path in all_paths:
            prev = prev_by_path.get(path)
            nxt = new_by_path.get(path)
            prev_content = (
                prev.get("content") if isinstance(prev, dict) else None
            )
            original_content = (
                prev.get("original_content") if isinstance(prev, dict) else None
            )
            new_content = (
                nxt.get("content") if isinstance(nxt, dict) else None
            )
            if isinstance(original_content, str):
                base = original_content
                partner_edit = prev_content if isinstance(prev_content, str) else None
            else:
                base = prev_content if isinstance(prev_content, str) else None
                partner_edit = None
            conflict = (
                partner_edit is not None
                and partner_edit != new_content
                and base != new_content
            )
            files_payload.append({
                "path": path,
                "base": base,
                "partner_edit": partner_edit,
                "ai_redraft": new_content,
                "conflict": bool(conflict),
            })

        return {
            "refresh_id": refresh_id,
            "status": refresh_row["status"],
            "previous_scaffold_id": previous_id,
            "new_scaffold_id": new_id,
            "base_main_sha_before": refresh_row["base_main_sha_before"],
            "base_main_sha_after": refresh_row["base_main_sha_after"],
            "changed_paths": refresh_row["changed_paths"],
            "files": files_payload,
        }

    @app.post(
        "/api/v2/projects/{project_id}/refresh-resolve",
        tags=["artifact"],
    )
    async def v2_refresh_resolve(
        project_id: str,
        body: RefreshResolveBody,
        user: dict = Depends(_current_user),
    ) -> dict[str, Any]:
        """Apply per-file decisions on a refresh's diff.

        Owner-only. For each ``(path, decision)`` entry, patches the
        new scaffold's manifest so it carries the chosen content
        (``accept_redraft`` keeps the redraft as-is, ``keep_partner_edit``
        rewrites it to the previous scaffold's content, ``merged``
        uses the partner-provided ``merged_content``). Returns
        ``{scaffold_id, diff_summary, refresh_id}``.
        """
        _require_owned_project(project_id, user)
        refresh_row = _store.get_scaffold_refresh_history(
            refresh_id=body.refresh_id,
        )
        if refresh_row is None or refresh_row["project_id"] != project_id:
            raise HTTPException(
                status_code=404,
                detail={"error": "refresh_not_found"},
            )
        if refresh_row["status"] not in ("completed", "resolved"):
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "refresh_not_ready",
                    "message": (
                        "The refresh hasn't completed yet. Wait for "
                        "status='completed' before resolving."
                    ),
                },
            )

        new_id = refresh_row.get("new_scaffold_id")
        previous_id = refresh_row.get("previous_scaffold_id")
        if not new_id:
            raise HTTPException(
                status_code=409,
                detail={"error": "refresh_missing_new_scaffold"},
            )

        user_id = user["user_id"]
        previous_files = _refresh_diff_files_for_scaffold(
            previous_id, user_id,
        )
        prev_content_by_path: dict[str, str] = {
            e["path"]: e["content"]
            for e in previous_files
            if isinstance(e, dict) and isinstance(e.get("path"), str)
            and isinstance(e.get("content"), str)
        }

        accepted = 0
        kept = 0
        merged = 0
        for path, decision in body.decisions.items():
            if decision.decision == "accept_redraft":
                accepted += 1
                continue
            if decision.decision == "keep_partner_edit":
                content = prev_content_by_path.get(path)
                if content is None:
                    continue
                _store.update_scaffold_file_content(
                    scaffold_id=new_id, user_id=user_id,
                    path=path, content=content,
                )
                kept += 1
                continue
            if decision.decision == "merged":
                content = decision.merged_content
                if content is None:
                    raise HTTPException(
                        status_code=422,
                        detail={
                            "error": "merged_content_required",
                            "message": (
                                f"decision='merged' for path={path!r} "
                                "but merged_content is null"
                            ),
                        },
                    )
                _store.update_scaffold_file_content(
                    scaffold_id=new_id, user_id=user_id,
                    path=path, content=content,
                )
                merged += 1

        _store.update_scaffold_refresh_history(
            refresh_id=body.refresh_id, status="resolved",
        )
        return {
            "scaffold_id": new_id,
            "refresh_id": body.refresh_id,
            "diff_summary": {
                "accepted": accepted,
                "kept": kept,
                "merged": merged,
            },
        }

    def _refresh_diff_files_for_scaffold(
        scaffold_id: str | None, user_id: str,
    ) -> list[dict[str, Any]]:
        """Helper — load the file list out of one scaffold's manifest."""
        if not scaffold_id:
            return []
        row = _store.get_scaffold(
            scaffold_id=scaffold_id, user_id=user_id,
        )
        if row is None:
            return []
        import json as _json  # noqa: PLC0415
        try:
            manifest = _json.loads(row.get("manifest_json") or "{}")
        except (TypeError, ValueError):
            return []
        files = manifest.get("files") if isinstance(manifest, dict) else None
        return list(files) if isinstance(files, list) else []

    @app.post(
        "/api/v2/projects/{project_id}/artifact/edit/stream",
        tags=["artifact"],
    )
    def v2_artifact_edit_stream(
        project_id: str,
        body: ArtifactEditBody,
        user: dict = Depends(_current_user),
    ):
        """Apply a chat-driven edit to the existing artifact. Streamed."""
        _artifact_assert_software_and_entitled(project_id, user)
        _require_token_budget(user)

        existing_artifact = _store.get_v2_project_artifact(
            project_id=project_id,
        )
        if existing_artifact is None:
            raise HTTPException(
                status_code=409,
                detail={"error": "artifact_not_generated"},
            )
        latest_scaffold_id = existing_artifact.get("latest_scaffold_id")
        if not latest_scaffold_id:
            raise HTTPException(
                status_code=409,
                detail={"error": "artifact_not_generated"},
            )

        scaffold_row = _store.get_scaffold(
            scaffold_id=str(latest_scaffold_id), user_id=user["user_id"],
        )
        if scaffold_row is None:
            raise HTTPException(
                status_code=409,
                detail={"error": "artifact_not_generated"},
            )

        import json as _json  # noqa: PLC0415

        try:
            manifest = _json.loads(scaffold_row["manifest_json"] or "{}")
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=500,
                detail={"error": "corrupt_manifest"},
            )
        current_files = [
            {
                "path": f.get("path", ""),
                "content": f.get("content", ""),
            }
            for f in (manifest.get("files") or [])
            if isinstance(f, dict)
        ]
        project_title = _resolve_project_title(project_id, user)
        validated_locale = _validate_locale(body.locale)

        # Append the user's message BEFORE the LLM call so a refresh
        # mid-stream surfaces the user turn already.
        _store.append_artifact_chat_turn(
            project_id=project_id, role="user", body=body.message.strip(),
        )

        selected_adapter, model_override = _artifact_resolve_dispatch(
            user["user_id"],
        )
        model_used_label = model_override
        progress_script = _artifact_thinking_progress_script()

        async def _generator():
            import asyncio  # noqa: PLC0415

            yield format_sse(
                "heartbeat",
                {
                    "status": "thinking",
                    "message": thinking_message(
                        progress_script[0], validated_locale,
                    ),
                    "elapsed_s": 0,
                },
            )
            try:
                loop = asyncio.get_running_loop()
                edit_task = loop.run_in_executor(
                    None,
                    lambda: selected_adapter.edit(
                        project_title=project_title,
                        current_files=current_files,
                        user_message=body.message.strip(),
                        locale=validated_locale,
                        model_override=model_override,
                    ),
                )
                step = 0
                while not edit_task.done():
                    if step >= _ARTIFACT_HARD_TIMEOUT_TICKS:
                        edit_task.cancel()
                        yield format_sse(
                            "error",
                            {
                                "code": "artifact_edit_timeout",
                                "message": (
                                    "The edit took too long. Try a simpler "
                                    "tweak from the chat."
                                ),
                                "elapsed_s": step * 3,
                            },
                        )
                        return
                    try:
                        await asyncio.wait_for(
                            asyncio.shield(edit_task), timeout=3.0,
                        )
                    except asyncio.TimeoutError:
                        key = progress_script[
                            min(step, len(progress_script) - 1)
                        ]
                        yield format_sse(
                            "heartbeat",
                            {
                                "status": "thinking",
                                "message": thinking_message(
                                    key, validated_locale,
                                ),
                                "elapsed_s": step * 3,
                            },
                        )
                        step += 1
                edited_manifest = edit_task.result()
            except RuntimeError as exc:
                rid_exc = _planner_error_response(exc)
                detail = (
                    rid_exc.detail if isinstance(rid_exc.detail, dict)
                    else {"error": "planner_call_failed"}
                )
                yield format_sse(
                    "error",
                    {
                        "code": "planner_error",
                        "message": "The artifact writer failed to respond.",
                        "detail": detail,
                    },
                )
                return
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "[v2_artifact_edit_stream] adapter.edit failed",
                )
                yield format_sse(
                    "error",
                    {
                        "code": "planner_error",
                        "message": str(exc) or "Unexpected planner failure",
                    },
                )
                return

            try:
                _record_llm_usage(
                    user,
                    prompt_text=body.message,
                    response_text=str(edited_manifest),
                )

                row = _store.create_scaffold(
                    project_id=project_id,
                    user_id=user["user_id"],
                    framework=str(edited_manifest.get("framework") or "other"),
                    language=str(
                        edited_manifest.get("language") or "typescript",
                    ),
                    manifest_json=_json.dumps(edited_manifest),
                )

                files = edited_manifest.get("files") or []
                explanation = (
                    edited_manifest.get("explanation")
                    or "Updated the scaffold."
                )
                # Update the overlay to point at the new scaffold id +
                # append the assistant turn.
                refreshed_artifact = (
                    _store.get_v2_project_artifact(project_id=project_id)
                    or existing_artifact
                )
                refreshed_artifact = dict(refreshed_artifact)
                refreshed_artifact["latest_scaffold_id"] = row["scaffold_id"]
                refreshed_artifact["model_used"] = model_used_label
                _store.set_v2_project_artifact(
                    project_id=project_id, artifact=refreshed_artifact,
                )
                final_artifact = _store.append_artifact_chat_turn(
                    project_id=project_id,
                    role="assistant",
                    body=str(explanation),
                ) or refreshed_artifact

                envelope = {
                    "artifact": {
                        "latest_scaffold_id": row["scaffold_id"],
                        "model_used": model_used_label,
                        "framework": str(
                            edited_manifest.get("framework") or "other",
                        ),
                        "language": str(
                            edited_manifest.get("language") or "typescript",
                        ),
                        "files": [
                            {
                                "path": f["path"],
                                "content": f.get("content", ""),
                            }
                            for f in files
                        ],
                        "messages": final_artifact.get("messages") or [],
                    },
                }
                yield format_sse("complete", envelope)
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "[v2_artifact_edit_stream] persist failed",
                )
                yield format_sse(
                    "error",
                    {
                        "code": "artifact_persist_failed",
                        "message": str(exc) or "Failed to persist edit",
                    },
                )

        return sse_stream(
            _generator(),
            extra_headers={"x-llm-mode": "house"},
        )

    # -----------------------------------------------------------------------
    # Read-only share links
    # -----------------------------------------------------------------------
    # Three authed routes for the project owner, one public route:
    #   POST /api/v2/projects/{project_id}/share        → mint/replace token
    #   GET  /api/v2/projects/{project_id}/share        → fetch active token
    #   POST /api/v2/projects/{project_id}/share/revoke → revoke
    #   GET  /api/v2/shared/{token}                     → public canvas payload
    #
    # The public GET does NOT use Depends(_current_user) — it must work for
    # anonymous callers who have no session cookie.

    from .sharing import (  # noqa: PLC0415
        create_share_token as _create_share_token,
        resolve_share_token as _resolve_share_token,
        revoke_share_token as _revoke_share_token,
    )

    @app.post(
        "/api/v2/projects/{project_id}/share",
        tags=["sharing"],
        status_code=201,
    )
    def v2_create_share_link(
        project_id: str, user: dict = Depends(_current_user),
    ) -> dict[str, Any]:
        """Mint a new read-only share link, revoking any prior live link."""
        _require_owned_project(project_id, user)
        try:
            result = _create_share_token(
                _store, user_id=user["user_id"], project_id=project_id,
            )
        except PermissionError:
            raise HTTPException(status_code=404, detail={"error": "project_not_found"})
        row = _store.get_active_share_link(
            project_id=project_id, user_id=user["user_id"],
        )
        return {"share_link": row, "url": result["url"]}

    @app.get(
        "/api/v2/projects/{project_id}/share",
        tags=["sharing"],
    )
    def v2_get_share_link(
        project_id: str, user: dict = Depends(_current_user),
    ) -> dict[str, Any]:
        """Return the currently-active share link for this project, or null."""
        _require_owned_project(project_id, user)
        row = _store.get_active_share_link(
            project_id=project_id, user_id=user["user_id"],
        )
        return {"share_link": row}

    @app.post(
        "/api/v2/projects/{project_id}/share/revoke",
        tags=["sharing"],
    )
    def v2_revoke_share_link(
        project_id: str, user: dict = Depends(_current_user),
    ) -> dict[str, Any]:
        """Revoke the active share link for this project."""
        _require_owned_project(project_id, user)
        revoked = _revoke_share_token(
            _store, user_id=user["user_id"], project_id=project_id,
        )
        return {"revoked": revoked}

    @app.get(
        "/api/v2/shared/{token}",
        tags=["sharing"],
    )
    def v2_shared_project(token: str) -> dict[str, Any]:
        """Public read-only canvas payload — no auth required.

        Returns topics, relationships, decisions, and turns_by_topic (with
        attachment content stripped and ``private_notes`` scrubbed off every
        topic).  404 on unknown/revoked/deleted.
        """
        info = _resolve_share_token(_store, token=token)
        if info is None:
            raise HTTPException(
                status_code=404,
                detail={"error": "share_link_not_found"},
            )

        project_id = info["project_id"]

        # Best-effort view-count tracking — never blocks the response.
        try:
            _store.touch_share_link(token)
        except Exception:  # noqa: BLE001
            pass

        project = _store._get_v2_project(project_id)  # noqa: SLF001
        if project is None:
            raise HTTPException(
                status_code=404,
                detail={"error": "share_link_not_found"},
            )

        # Fetch owner display name for attribution.
        owner_id = info["owner_user_id"]
        owner_display_name: str = ""
        try:
            owner_user = _store.get_user_by_id(owner_id)
            if owner_user:
                owner_display_name = owner_user.get("display_name") or ""
        except Exception:  # noqa: BLE001
            pass

        raw_topics = _store.list_topics(project_id=project_id)
        # Scrub ``private_notes`` off every topic before the payload leaves
        # this handler. ``list_topics`` includes the column as of the
        # private-notes migration (see store.py), and without this pass any
        # owner who set a private note would have it visible to every
        # anonymous viewer hitting the share URL. Drop the key entirely
        # rather than blanking it out so the wire shape mirrors a topic
        # that never had a note, which the frontend already handles.
        topics: list[dict[str, Any]] = []
        for topic in raw_topics:
            scrubbed = dict(topic)
            scrubbed.pop("private_notes", None)
            topics.append(scrubbed)

        relationships = _store.list_relationships(project_id=project_id)
        decisions = _store.list_decisions(project_id=project_id)

        # Build turns_by_topic with attachment content stripped to avoid leaking
        # any file excerpts through the public surface.
        turns_by_topic: dict[str, list[dict[str, Any]]] = {}
        for topic in topics:
            topic_id = topic["topic_id"]
            raw_turns = _store.list_qna_turns(topic_id=topic_id)
            sanitised: list[dict[str, Any]] = []
            for turn in raw_turns:
                clean = dict(turn)
                clean["attachments"] = []
                sanitised.append(clean)
            turns_by_topic[topic_id] = sanitised

        return {
            "project": {
                "project_id": project["project_id"],
                "title": project["title"],
                "created_at": project["created_at"],
                "updated_at": project["updated_at"],
                "owner_display_name": owner_display_name,
            },
            "topics": topics,
            "relationships": relationships,
            "decisions": decisions,
            "turns_by_topic": turns_by_topic,
        }

    # -----------------------------------------------------------------------
    # Markdown import
    # -----------------------------------------------------------------------
    from .markdown_import import (  # noqa: PLC0415
        MarkdownImportBody,
        parse_markdown,
        instantiate_from_markdown,
    )
    # markdown_import uses `from __future__ import annotations`, so every
    # attribute on MarkdownImportBody is a forward-ref string. Registering
    # the route from inside create_app puts FastAPI's Pydantic walker in a
    # function scope where those strings can't resolve, and the whole
    # /openapi.json schema build errors out with "ForwardRef ... is not
    # fully defined". Rebuilding the model with the now-fully-loaded
    # namespace fixes both the 422 on POST /api/v2/projects/from-markdown
    # and the 500 on GET /openapi.json.
    MarkdownImportBody.model_rebuild()

    @app.post("/api/v2/projects/from-markdown", status_code=201, tags=["v2"])
    def v2_create_project_from_markdown(
        body: MarkdownImportBody,
        request: Request,  # noqa: ARG001 — required for per-route slowapi rate limiting
        user: dict = Depends(_current_user),
    ) -> dict:
        # Note: the old ``is_system`` gate here was removed so anonymous
        # visitors can import their Notion/Obsidian brain dumps directly
        # onto a canvas before signing up. Their rows are scoped to a
        # per-session ``user-anon-<hex>`` id (see auth._create_anon_user)
        # and are transferred to their real account via
        # ``/api/v2/auth/transfer-anonymous-projects`` on signup.
        if not body.markdown.strip():
            raise HTTPException(status_code=400, detail={"error": "empty_markdown"})
        parsed = parse_markdown(body.markdown)
        project = instantiate_from_markdown(
            _store, user_id=user["user_id"], parsed=parsed, title_override=body.title,
        )
        topics = _store.list_topics(project_id=project["project_id"])
        try:
            import markdown as _mdlib  # noqa: PLC0415
            preview_html = _mdlib.markdown(body.markdown[:4000])
        except ImportError:
            preview_html = f"<pre>{body.markdown[:4000]}</pre>"
        return {"project": project, "topics": topics, "preview_html": preview_html}

    # -----------------------------------------------------------------------
    # JSON import — mirror of the client-side exportToJson(). Takes an
    # ``inspira.canvas.v1`` blob and recreates a project with its topics,
    # relationships, and decisions. Q&A turns in the blob are NOT imported
    # (see json_import.py docstring for the rationale).
    # -----------------------------------------------------------------------
    from .json_import import (  # noqa: PLC0415
        JsonImportBody,
        parse_inspira_canvas_v1,
        instantiate_from_json,
    )
    # Same ForwardRef-rebuild dance as MarkdownImportBody above — see the
    # long comment there. Without this rebuild, every POST to
    # /api/v2/projects/from-json comes back 422 and /openapi.json errors
    # at schema build time.
    JsonImportBody.model_rebuild()

    @app.post("/api/v2/projects/from-json", status_code=201, tags=["v2"])
    def v2_create_project_from_json(
        body: JsonImportBody,
        request: Request,  # noqa: ARG001 — required for per-route slowapi rate limiting
        user: dict = Depends(_current_user),
    ) -> dict:
        # Anonymous visitors can import too — same story as from-markdown.
        # Their rows land under the per-session user-anon id and transfer
        # to a real account on signup.
        try:
            parsed = parse_inspira_canvas_v1(body.json_blob)
        except ValueError as exc:
            # Surface the concise validator message so the frontend can
            # show the user what was wrong with their blob (wrong schema
            # tag, missing topics, etc.) without leaking internals.
            raise HTTPException(
                status_code=400,
                detail={"error": "invalid_json_blob", "message": str(exc)},
            ) from exc

        project = instantiate_from_json(
            _store,
            user_id=user["user_id"],
            parsed=parsed,
            title_override=body.title,
        )
        topics = _store.list_topics(project_id=project["project_id"])
        relationships = _store.list_relationships(project_id=project["project_id"])
        decisions = _store.list_decisions(project_id=project["project_id"])
        return {
            "project": project,
            "topics": topics,
            "relationships": relationships,
            "decisions": decisions,
        }

    # -----------------------------------------------------------------------
    # Full-text search
    # -----------------------------------------------------------------------
    from .search import search_all, SearchResults  # noqa: PLC0415

    async def _v2_search(
        request: Request,  # noqa: ARG001
        q: str = "",
        limit: int = 50,
        user: dict = Depends(_current_user),
    ) -> dict:
        _Q_MAX_LENGTH = 500
        if len(q) > _Q_MAX_LENGTH:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "query_too_long",
                    "max_length": _Q_MAX_LENGTH,
                },
            )
        if not q.strip():
            return {"hits": [], "truncated": False}
        # Clamp limit to [1, 100] so callers cannot request zero or negative
        # result sets (which would produce confusing empty responses) or
        # unbounded ones.
        safe_limit = max(1, min(limit, 100))
        results: SearchResults = search_all(
            _store, user_id=user["user_id"], query=q, limit=safe_limit,
        )
        return results.model_dump()

    _search_limiter = getattr(app.state, "limiter", None)
    if _search_limiter is not None:
        _v2_search = _search_limiter.limit("30/minute")(_v2_search)

    app.add_api_route("/api/v2/search", _v2_search, methods=["GET"], tags=["v2"])

    # Extra health probes for the public /status page (GET /api/health/db).
    from . import health_routes as _health_routes  # noqa: PLC0415
    _health_routes.bind(_store)
    app.include_router(_health_routes.router)

    # ---------------------------------------------------------------
    # Real-time collaboration WebSocket (cursors, presence, topic
    # focus locks, follow mode, contradiction push). Auth via the
    # same session cookie as the REST surface; project-membership
    # IDOR-checked in the handler. See realtime.py for the full
    # protocol and cross-machine fanout via Postgres LISTEN/NOTIFY.
    # ---------------------------------------------------------------
    @app.websocket("/ws/projects/{project_id}")
    async def _ws_project(websocket: WebSocket, project_id: str) -> None:
        await realtime.handle_ws(websocket, project_id, _store)

    # ---------------------------------------------------------------
    # OpenAI Custom GPT Actions surface: POST /api/v2/mcp/<tool_name>
    # ---------------------------------------------------------------
    # The same 11 tool handlers that drive the MCP server (consumed by
    # Claude.ai) are exposed here as plain FastAPI POST routes. ChatGPT's
    # Custom GPT builder ingests /openapi.json and calls these routes
    # with Authorization: Bearer inspira_pat_<hex>. Each route delegates
    # into ``inspira_mcp.tool_handlers.HANDLERS[name]`` — a change to a
    # tool's shape lands in one place and both surfaces pick it up.
    #
    # Auth is intentionally NOT the cookie/session dependency the rest
    # of the v2 surface uses: API clients (Claude, ChatGPT) cannot
    # forward cookies and shouldn't have to round-trip through signup.
    # The PAT resolver in ``inspira_mcp.auth`` speaks to the same
    # ``user_access_tokens`` table the token-issue endpoints write.
    from fastapi import Body, Header  # noqa: PLC0415  # local re-import for MCP wiring
    from inspira_mcp import auth as _mcp_auth  # noqa: PLC0415
    from inspira_mcp import tool_handlers as _mcp_handlers  # noqa: PLC0415
    from inspira_mcp.schemas import (  # noqa: PLC0415
        TOOL_SPEC as _MCP_TOOL_SPEC,
    )

    def _resolve_mcp_user_id(authorization: str | None) -> str:
        """PAT -> user_id, mapping AuthError onto FastAPI's 401 shape."""
        try:
            return _mcp_auth.resolve_bearer_token(_store, authorization)
        except _mcp_auth.AuthError as exc:
            raise HTTPException(
                status_code=401,
                detail={"error": exc.reason},
                headers={"WWW-Authenticate": 'Bearer realm="inspira"'},
            ) from exc

    def _register_mcp_route(entry: dict[str, Any]) -> None:
        name = entry["name"]
        handler = _mcp_handlers.HANDLERS[name]
        input_model = entry["input"]
        output_model = entry["output"]

        # Build the view function via closure that takes an already-
        # parsed request. Because ``from __future__ import annotations``
        # is on at module scope, typical FastAPI patterns
        # (``payload: InputModel``) get stringified and the framework
        # guesses "query parameter" for unknown names. Constructing
        # ``_view`` and then assigning ``__annotations__`` directly with
        # the real class objects side-steps that. Using ``Body(..., embed=False)``
        # as the default value unambiguously tells FastAPI to parse
        # ``payload`` from the request body.
        async def _view(payload, authorization):  # type: ignore[no-untyped-def]
            user_id = _resolve_mcp_user_id(authorization)
            try:
                return handler(_store, user_id, payload)
            except _mcp_handlers.ToolError as exc:
                raise HTTPException(
                    status_code=exc.status,
                    detail={"error": exc.reason},
                ) from exc
            except Exception as exc:  # noqa: BLE001
                logger.exception("[mcp_tool_failed tool=%s]", name)
                raise HTTPException(
                    status_code=500, detail={"error": "internal_error"},
                ) from exc

        _view.__annotations__ = {
            "payload": input_model,
            "authorization": str,
            "return": output_model,
        }
        _view.__defaults__ = (Body(..., embed=False), Header(default=None))  # type: ignore[attr-defined]
        _view.__name__ = f"mcp_{name}"
        app.post(
            f"/api/v2/mcp/{name}",
            response_model=output_model,
            tags=["mcp"],
            summary=entry["description"],
            operation_id=name,
        )(_view)

    for _entry in _MCP_TOOL_SPEC:
        _register_mcp_route(_entry)

    # -----------------------------------------------------------------------
    # Audit hardening — per-route rate limits on sensitive v2 endpoints
    # -----------------------------------------------------------------------
    # Extends the slowapi coverage already wired for /api/auth/*, BYOK,
    # and search to the rest of the sensitive surface. Same
    # endpoint-swap + dependant-rebuild pattern as the BYOK block above:
    # we replace _route.endpoint with the slowapi wrapper, then rebuild
    # ``.dependant`` / ``._flat_dependant`` / ``.app`` so FastAPI actually
    # dispatches through the wrapper (assigning .endpoint alone leaves
    # the cached dependant + ASGI handler pointing at the bare callable).
    #
    # Bucketing strategy is inherited from ``bearer_rate_limit_key``
    # (see bearer_auth.py): PAT-authed traffic buckets per user_id so
    # one user's runaway integration cannot starve another's; cookie
    # / anonymous traffic falls back to per-IP. This satisfies the
    # task spec ("for authenticated routes, key by user_id (or fall
    # back to IP if anonymous)") without per-route key overrides.
    #
    # No-op when slowapi is missing — _v2_sensitive_limiter is None so
    # we never enter the rebuild loop. The whole block is gated on
    # ``app.state.limiter`` being set, matching the test-environment
    # contract documented around the limiter setup at the top of
    # create_app (slowapi is optional at import time).
    #
    # NOT covered here (intentional, see task scope):
    # - Stripe webhooks (/api/v2/billing/webhook)   — Stripe retries on
    #   429, so a rate-limit response is the wrong signal and would
    #   stall billing reconciliation.
    # - Health / status / admin probes                — must always
    #   respond; the global slowapi default already protects against
    #   abusive scraping.
    # - Read-only GETs                                — covered by the
    #   global per-IP / per-PAT default (120/min).
    _v2_sensitive_limiter = getattr(app.state, "limiter", None)
    if _v2_sensitive_limiter is not None:
        from fastapi.dependencies.utils import (  # noqa: PLC0415
            get_dependant, get_flat_dependant,
        )
        from fastapi.routing import request_response  # noqa: PLC0415

        # (path, method) → slowapi limit decorator. Method is part of the
        # key so we don't accidentally rate-limit a GET that shares a path
        # with a POST (e.g. /api/v2/auth/tokens has both).
        _v2_sensitive_rates: dict[tuple[str, str], Any] = {
            # Token mint — guards against PAT spam from a stolen session.
            # 5/min/user is well above any human use (the UI shows the
            # token in a copy-once dialog) and tight enough to flag a
            # script grinding through PAT creation.
            ("/api/v2/auth/tokens", "POST"): _v2_sensitive_limiter.limit("5/minute"),
            # Project create — POST creates a row; per-user 30/min keeps
            # bulk-import scripts comfortable while flagging a runaway.
            ("/api/v2/projects", "POST"): _v2_sensitive_limiter.limit("30/minute"),
            # Markdown / JSON import — same project_create budget; an
            # import is one project per call.
            ("/api/v2/projects/from-markdown", "POST"): _v2_sensitive_limiter.limit("30/minute"),
            ("/api/v2/projects/from-json", "POST"): _v2_sensitive_limiter.limit("30/minute"),
            # Topic create — 60/min/user; the canvas can fan out a
            # handful of topics from a single user gesture so this is
            # 2x the project-create cap.
            ("/api/v2/projects/{project_id}/topics", "POST"): _v2_sensitive_limiter.limit("60/minute"),
            # Decision create — same 60/min budget as topic create;
            # decisions can land in bursts when a user is capturing a
            # meeting.
            ("/api/v2/topics/{topic_id}/decisions", "POST"): _v2_sensitive_limiter.limit("60/minute"),
            # Kickoff — LLM-bound, expensive. 5/min/user is the spec
            # ceiling; the per-user daily token budget (M5) is the
            # broader cost cap.
            ("/api/v2/projects/{project_id}/kickoff", "POST"): _v2_sensitive_limiter.limit("5/minute"),
            # Topic turn — LLM-bound, called once per Q&A round. 30/min
            # supports rapid back-and-forth without pinning the model.
            ("/api/v2/topics/{topic_id}/turn", "POST"): _v2_sensitive_limiter.limit("30/minute"),
            # Fetch URL — outbound network + parser; 10/min/user keeps
            # us from being weaponised as a generic SSRF scanner and
            # caps our own bandwidth burn.
            ("/api/v2/fetch-url", "POST"): _v2_sensitive_limiter.limit("10/minute"),
            # Extract themes — LLM-bound and the most expensive public
            # endpoint; without a cap an adversary could loop it and
            # drain LLM spend. 10/min/user matches fetch-url; the
            # per-user daily token budget gate inside the handler is
            # the broader ceiling.
            ("/api/v2/feedback/extract-themes", "POST"): _v2_sensitive_limiter.limit("10/minute"),
        }
        for _route in app.routes:
            _route_path = getattr(_route, "path", None)
            _route_methods = getattr(_route, "methods", None) or set()
            if _route_path is None:
                continue
            for _method in _route_methods:
                _key = (_route_path, _method)
                if _key not in _v2_sensitive_rates:
                    continue
                _wrapped = _v2_sensitive_rates[_key](
                    _route.endpoint,  # type: ignore[union-attr]
                )
                _route.endpoint = _wrapped  # type: ignore[union-attr]
                # Rebuild ``.dependant`` from the wrapped callable so
                # FastAPI injects ``Request`` / body / cookies into the
                # wrapper (slowapi needs ``request`` or it raises).
                _route.dependant = get_dependant(  # type: ignore[union-attr]
                    path=_route.path_format,  # type: ignore[union-attr]
                    call=_wrapped,
                    scope="function",
                )
                _route._flat_dependant = get_flat_dependant(  # type: ignore[attr-defined]
                    _route.dependant,  # type: ignore[union-attr]
                )
                _route.app = request_response(  # type: ignore[union-attr]
                    _route.get_route_handler(),  # type: ignore[union-attr]
                )
                # A route only matches one (path, method) tuple; break
                # so the inner loop doesn't double-wrap when a route
                # somehow exposes multiple methods at the same path.
                break

    return app


# Module-level ASGI app so ``uvicorn planning_studio_service.api:app`` works.
# Built lazily to avoid side-effects at import time during tests.
_app: FastAPI | None = None


def app() -> FastAPI:  # pragma: no cover
    global _app
    if _app is None:
        _app = create_app()
    return _app


# Convenience — uvicorn can point at either the factory or the instance.
asgi_app = app()  # constructed at module import for the default launch path
