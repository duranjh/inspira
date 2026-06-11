"""MCP server for Inspira — Claude.ai / Anthropic client surface.

Architecture:

- Wraps ``FastMCP`` from the upstream ``mcp`` SDK. The SDK owns the
  streamable-HTTP transport, JSON-RPC framing, tool dispatch, and the
  Starlette app wiring.
- The 11 tools registered here all delegate into ``tool_handlers``. The
  handler layer owns every piece of business logic (store I/O, IDOR
  checks, payload shaping); this module is pure plumbing.
- Auth is a Personal Access Token passed as ``Authorization: Bearer
  inspira_pat_<hex>``. We don't use the MCP SDK's built-in OAuth
  ``TokenVerifier`` path — PATs are long-lived bearer credentials, not
  short-lived OAuth access tokens, so we run a small Starlette
  middleware that pulls the token, resolves it via ``auth`` and stashes
  the ``user_id`` into the ASGI scope. The tool wrappers read the
  user_id back off the ``Context.request_context.request.scope``.

The module is runnable via ``python -m inspira_mcp`` (see ``__main__``).
At process start we spin up a PlanningStudioStore, build the MCP server
wired against the same 11 handlers the OpenAPI surface exposes, and
launch the streamable-HTTP server on ``0.0.0.0:4175`` by default.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Awaitable, Callable

from mcp.server.fastmcp import Context, FastMCP
from starlette.types import Receive, Scope, Send

from planning_studio_service.config import load_config
from planning_studio_service.store import PlanningStudioStore

from . import auth
from . import tool_handlers
from .schemas import TOOL_SPEC
from .tool_handlers import ToolError


logger = logging.getLogger("inspira_mcp.server")


# Key we store the resolved user_id under in the ASGI scope. Read from
# the tool wrappers via ``ctx.request_context.request.scope``. Chosen to
# not collide with any MCP SDK key.
SCOPE_USER_ID_KEY = "inspira_user_id"


# ---------------------------------------------------------------------------
# Starlette middleware: resolve Bearer PAT -> user_id -> scope key
# ---------------------------------------------------------------------------


class InspiraPatMiddleware:
    """ASGI middleware that resolves PATs to user_ids on every HTTP request.

    Writes ``scope[SCOPE_USER_ID_KEY] = <user_id>`` on success. On a
    missing / invalid / revoked token the middleware short-circuits with
    a 401 JSON body — the tool dispatch below never runs, which is the
    behaviour we want (the SDK would otherwise call the tool with no
    user context and every handler would have to duplicate the auth
    check).

    Non-HTTP scopes (lifespan, websocket) are passed through untouched so
    the SDK's startup/shutdown hooks still run. We only gate HTTP.
    """

    def __init__(self, app: Any, store: PlanningStudioStore) -> None:
        self._app = app
        self._store = store

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        # Fly.io health check hook. The MCP streamable-HTTP endpoint at
        # /mcp returns 406 on a bare GET (correct per the spec — without
        # SSE accept headers the handler refuses) which Fly treats as
        # unhealthy and tears down the machine. Expose a dedicated
        # /healthz that answers plain 200 JSON so Fly can confirm the
        # process is alive. Unauthenticated by design — this endpoint
        # returns no user-scoped data.
        if scope.get("path") == "/healthz" and scope.get("method") == "GET":
            await _send_healthz(send)
            return

        headers: list[tuple[bytes, bytes]] = scope.get("headers") or []
        authorization: str | None = None
        for key, value in headers:
            if key.lower() == b"authorization":
                try:
                    authorization = value.decode("latin-1")
                except UnicodeDecodeError:
                    authorization = None
                break

        try:
            user_id = auth.resolve_bearer_token(self._store, authorization)
        except auth.AuthError as exc:
            await _send_401(send, reason=exc.reason)
            return

        # Shallow copy so we don't mutate a shared dict across concurrent
        # connections (ASGI allows the server to reuse scopes).
        new_scope = dict(scope)
        new_scope[SCOPE_USER_ID_KEY] = user_id
        await self._app(new_scope, receive, send)


async def _send_401(send: Send, *, reason: str) -> None:
    body = json.dumps({"error": reason}).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
                (b"www-authenticate", b'Bearer realm="inspira"'),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


async def _send_healthz(send: Send) -> None:
    """Respond 200 to Fly's http_check probe at /healthz."""
    body = b'{"service":"inspira-mcp","status":"ok"}'
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
                (b"cache-control", b"no-store"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


def _user_id_from_context(ctx: Context) -> str:
    """Pull the authenticated user_id off the request scope.

    The middleware above is the only writer; if this key is missing the
    request reached us unauthenticated (should be impossible given the
    middleware runs before dispatch, but we belt-and-suspenders and
    raise a ToolError so the client gets a clean error instead of a
    confusing 500).
    """
    try:
        request = ctx.request_context.request
    except Exception as exc:  # noqa: BLE001
        raise ToolError("missing_user_context", status=401) from exc
    if request is None:
        raise ToolError("missing_user_context", status=401)
    scope = getattr(request, "scope", None)
    if not isinstance(scope, dict):
        raise ToolError("missing_user_context", status=401)
    user_id = scope.get(SCOPE_USER_ID_KEY)
    if not user_id:
        raise ToolError("missing_user_context", status=401)
    return str(user_id)


def _tool_wrapper_factory(
    name: str,
    store: PlanningStudioStore,
    handler: Callable[..., Any],
    input_model: type,
) -> Callable[..., Awaitable[Any]]:
    """Build a tool function matching the spec, delegating to the handler.

    Each tool wrapper accepts keyword arguments matching the input
    model's fields. FastMCP inspects the signature to build the tool's
    JSON Schema — since we accept the fields directly rather than a
    ``payload: InputModel`` positional, callers (Claude, ChatGPT, the
    inspector) see the natural flat-shaped tool surface.
    """

    async def _wrapper(ctx: Context, **kwargs: Any) -> dict[str, Any]:
        user_id = _user_id_from_context(ctx)
        try:
            payload = input_model.model_validate(kwargs)
        except Exception as exc:  # pydantic.ValidationError etc.
            raise ToolError(f"invalid_{name}_input", message=str(exc)) from exc
        try:
            result = handler(store, user_id, payload)
        except ToolError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("handler_failed name=%s", name)
            raise ToolError("internal_error", status=500, message=str(exc)) from exc
        # Every handler returns a pydantic model; unpack to a dict so the
        # MCP SDK can serialise it as JSON in the response.
        return result.model_dump()

    _wrapper.__name__ = name
    _wrapper.__doc__ = f"Inspira MCP tool: {name}"
    return _wrapper


def _build_tool_function(
    name: str,
    store: PlanningStudioStore,
    handler: Callable[..., Any],
    input_model: type,
) -> Callable[..., Awaitable[Any]]:
    """Wrap ``_tool_wrapper_factory`` to produce a function whose signature
    matches the input model's fields — FastMCP turns the signature into
    the JSON Schema it advertises to clients."""
    import inspect

    wrapper = _tool_wrapper_factory(name, store, handler, input_model)

    parameters: list[inspect.Parameter] = [
        inspect.Parameter(
            "ctx", inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=Context,
        )
    ]
    for field_name, field in input_model.model_fields.items():
        default = inspect.Parameter.empty if field.is_required() else field.get_default(
            call_default_factory=True,
        )
        annotation = field.annotation if field.annotation is not None else Any
        parameters.append(
            inspect.Parameter(
                field_name,
                inspect.Parameter.KEYWORD_ONLY,
                default=default,
                annotation=annotation,
            )
        )

    wrapper.__signature__ = inspect.Signature(  # type: ignore[attr-defined]
        parameters=parameters,
        return_annotation=dict,
    )
    return wrapper


# ---------------------------------------------------------------------------
# Public builder
# ---------------------------------------------------------------------------


def build_mcp_server(store: PlanningStudioStore | None = None) -> FastMCP:
    """Construct an ``FastMCP`` wired to Inspira's 11 tool handlers.

    ``store`` is optional for tests — if omitted we materialise a real
    ``PlanningStudioStore`` from ``load_config()``. Tests should always
    pass an in-memory store so one suite run doesn't touch disk.
    """
    if store is None:
        store = PlanningStudioStore(load_config())

    host = os.environ.get("INSPIRA_MCP_HOST", "0.0.0.0")
    port = int(os.environ.get("INSPIRA_MCP_PORT", "4175"))
    server = FastMCP(
        name="Inspira",
        instructions=(
            "Create and edit Inspira canvases. The model (Claude/ChatGPT) "
            "formulates questions and the user's answers both pass through "
            "record_answer — Inspira itself does not re-ask. Use list_projects "
            "/ list_topics to see what exists before adding to it."
        ),
        host=host,
        port=port,
        # Stateless transport so scaling out on Fly just works — every
        # request authenticates with its own PAT, no sticky session needed.
        stateless_http=True,
        json_response=True,
    )

    for entry in TOOL_SPEC:
        tool_name = entry["name"]
        handler = tool_handlers.HANDLERS[tool_name]
        input_model = entry["input"]
        fn = _build_tool_function(tool_name, store, handler, input_model)
        server.add_tool(
            fn,
            name=tool_name,
            description=entry["description"],
        )
    return server


def build_streamable_asgi_app(store: PlanningStudioStore | None = None) -> Any:
    """Return the Starlette app wrapping the MCP server + PAT middleware.

    Exposed so a non-default ASGI host (uvicorn, hypercorn, tests) can
    mount the app without going through ``FastMCP.run_*`` directly.
    The returned app answers every MCP transport request on ``/mcp``
    (streamable HTTP) with bearer PAT auth enforced at the edge.
    """
    server = build_mcp_server(store)
    underlying_store = (
        store
        if store is not None
        else _store_from_server(server)
    )
    app = server.streamable_http_app()
    wrapped = InspiraPatMiddleware(app, underlying_store)
    return wrapped


def _store_from_server(_server: FastMCP) -> PlanningStudioStore:
    """Fallback used by ``build_streamable_asgi_app`` when no store was passed.

    FastMCP doesn't expose a hook to read back the store we closed over in
    ``_build_tool_function``, so we mint a fresh one. This path only runs
    in production (where a single store is fine); tests always pass their
    own store explicitly.
    """
    return PlanningStudioStore(load_config())
