from __future__ import annotations

import argparse
import json
import re
import traceback
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from .config import load_config
from .store import PlanningStudioStore


# ---------------------------------------------------------------------------
# Route patterns — support {param} placeholders alongside literal paths.
# ---------------------------------------------------------------------------

_PATH_PARAM_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


def _match_route(pattern: str, actual_path: str) -> dict[str, str] | None:
    """Match ``actual_path`` against ``pattern``.

    Pattern supports ``{name}`` placeholders. Returns the captured dict or
    None if the pattern doesn't match. Exact literal paths still work.
    """
    pattern_parts = pattern.strip("/").split("/")
    actual_parts = actual_path.strip("/").split("/")
    if len(pattern_parts) != len(actual_parts):
        return None
    captures: dict[str, str] = {}
    for p, a in zip(pattern_parts, actual_parts):
        m = _PATH_PARAM_RE.fullmatch(p)
        if m:
            if not a:
                return None
            captures[m.group(1)] = a
            continue
        if p != a:
            return None
    return captures


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

Route = tuple[str, str, Callable[..., tuple[int, dict[str, Any]]]]


class PlanningStudioApplication:
    def __init__(self, store: PlanningStudioStore, adapter: Any = None) -> None:
        """Create the application.

        Args:
            store: backing storage.
            adapter: optional PlanningInterviewer adapter. When None, the app
                lazy-initializes the OpenAI adapter the first time an endpoint
                needs the planner. This lets non-LLM endpoints (health, list)
                work even when OPENAI_API_KEY isn't set.
        """
        self.store = store
        self._adapter = adapter

    # -- Adapter lazy init --------------------------------------------------

    def _require_adapter(self) -> Any:
        """Get the planner adapter. Raise-with-hint if not configured."""
        if self._adapter is None:
            from .agents import OpenAIPlanningInterviewer  # lazy import
            self._adapter = OpenAIPlanningInterviewer()
        return self._adapter

    # -- Route table --------------------------------------------------------

    def routes(self) -> list[Route]:
        return [
            # v1 (deprecated — kept for existing tests & backward compat)
            ("GET", "/api/health", self.handle_health),
            ("GET", "/api/projects", self.handle_projects),
            ("GET", "/api/sessions", self.handle_sessions),
            ("POST", "/api/sessions", self.handle_create_session),
            ("GET", "/api/artifacts", self.handle_artifacts),
            # v2 — Inspira canvas-first
            ("POST", "/api/v2/projects/{project_id}/kickoff", self.handle_v2_kickoff),
            ("GET", "/api/v2/projects/{project_id}/topics", self.handle_v2_list_topics),
            ("POST", "/api/v2/projects/{project_id}/topics", self.handle_v2_create_topic),
            ("POST", "/api/v2/topics/{topic_id}/turn", self.handle_v2_topic_turn),
            ("GET", "/api/v2/topics/{topic_id}/turns", self.handle_v2_list_turns),
            ("POST", "/api/v2/topics/{topic_id}/update", self.handle_v2_update_topic),
            ("POST", "/api/v2/topics/{topic_id}/delete", self.handle_v2_delete_topic),
            ("GET", "/api/v2/topics/{topic_id}/decisions", self.handle_v2_list_decisions),
            ("POST", "/api/v2/topics/{topic_id}/decisions", self.handle_v2_create_decision),
            ("POST", "/api/v2/decisions/{decision_id}/delete", self.handle_v2_delete_decision),
            ("GET", "/api/v2/projects/{project_id}/decisions", self.handle_v2_list_project_decisions),
            ("GET", "/api/v2/projects/{project_id}/relationships", self.handle_v2_list_relationships),
            ("POST", "/api/v2/projects/{project_id}/relationships", self.handle_v2_create_relationship),
            ("POST", "/api/v2/relationships/{relationship_id}/delete", self.handle_v2_delete_relationship),
        ]

    # -- v1 handlers (unchanged) -------------------------------------------

    def handle_health(self, _request: BaseHTTPRequestHandler, _params: dict[str, list[str]]) -> tuple[int, dict[str, Any]]:
        return HTTPStatus.OK, {"service": "planning-studio", **self.store.health()}

    def handle_projects(self, _request: BaseHTTPRequestHandler, _params: dict[str, list[str]]) -> tuple[int, dict[str, Any]]:
        return HTTPStatus.OK, {"projects": self.store.list_projects()}

    def handle_sessions(self, _request: BaseHTTPRequestHandler, params: dict[str, list[str]]) -> tuple[int, dict[str, Any]]:
        project_id = params.get("project_id", [None])[0]
        return HTTPStatus.OK, {"sessions": self.store.list_sessions(project_id=project_id)}

    def handle_create_session(self, request: BaseHTTPRequestHandler, _params: dict[str, list[str]]) -> tuple[int, dict[str, Any]]:
        payload = _read_json_body(request)
        for field in ("project_id", "title", "objective"):
            if not str(payload.get(field, "")).strip():
                return HTTPStatus.BAD_REQUEST, {"error": f"{field} is required"}
        session = self.store.create_session(
            project_id=str(payload["project_id"]).strip(),
            title=str(payload["title"]).strip(),
            objective=str(payload["objective"]).strip(),
            mode=str(payload.get("mode", "interview")).strip() or "interview",
        )
        return HTTPStatus.CREATED, {"session": session}

    def handle_artifacts(self, _request: BaseHTTPRequestHandler, params: dict[str, list[str]]) -> tuple[int, dict[str, Any]]:
        project_id = params.get("project_id", [None])[0]
        return HTTPStatus.OK, {"artifacts": self.store.list_artifacts(project_id=project_id)}

    # -- v2 handlers --------------------------------------------------------

    def handle_v2_kickoff(
        self,
        request: BaseHTTPRequestHandler,
        _params: dict[str, list[str]],
        *,
        project_id: str,
    ) -> tuple[int, dict[str, Any]]:
        """POST /api/v2/projects/{project_id}/kickoff

        Body: {"user_idea": str, "attached_sources"?: list[{display_name, kind, excerpt}]}

        Calls the planner's kickoff mode, persists the returned topics and
        relationships into the v2 store, and returns both the raw kickoff
        response and the persisted rows (with assigned IDs).
        """
        payload = _read_json_body(request)
        user_idea = str(payload.get("user_idea", "")).strip()
        if not user_idea:
            return HTTPStatus.BAD_REQUEST, {"error": "user_idea is required"}
        attached_sources = payload.get("attached_sources") or []

        try:
            adapter = self._require_adapter()
            kickoff_result = adapter.kickoff(
                user_idea=user_idea, attached_sources=attached_sources,
            )
        except RuntimeError as exc:
            return HTTPStatus.INTERNAL_SERVER_ERROR, {
                "error": "planner_call_failed",
                "detail": str(exc),
            }

        # Persist topics in order. Spatial layout: a loose two-row grid
        # that leaves real breathing room between cards so the user can drag
        # them without fighting edge crowding. Cards are ~280px wide and
        # ~180px tall; step by 440 × 320 gives a comfortable 160×140 gap.
        topics_raw = kickoff_result.get("topics") or []
        persisted_topics: list[dict[str, Any]] = []
        title_to_topic_id: dict[str, str] = {}
        x_step = 440
        y_rows: list[int] = [0, 320]  # two rows, 320px apart vertically
        for idx, topic in enumerate(topics_raw):
            persisted = self.store.create_topic(
                project_id=project_id,
                title=topic["title"],
                icon=topic["icon"],
                position_x=float((idx // len(y_rows)) * x_step),
                position_y=float(y_rows[idx % len(y_rows)]),
                origin="planner_initial",
                order_index=idx,
                metadata={"why_this_topic": topic.get("why_this_topic")},
            )
            persisted_topics.append(persisted)
            title_to_topic_id[topic["title"]] = persisted["topic_id"]

        # Persist relationships. Skip ones referencing titles that don't
        # resolve (should already be filtered by sanitize, but double check).
        relationships_raw = kickoff_result.get("relationships") or []
        persisted_relationships: list[dict[str, Any]] = []
        for rel in relationships_raw:
            src_id = title_to_topic_id.get(rel.get("from_topic_title", ""))
            tgt_id = title_to_topic_id.get(rel.get("to_topic_title", ""))
            if not src_id or not tgt_id:
                continue
            persisted_rel = self.store.create_relationship(
                project_id=project_id,
                source_topic_id=src_id,
                target_topic_id=tgt_id,
                label=rel.get("label"),
                origin="planner_inferred",
            )
            persisted_relationships.append(persisted_rel)

        return HTTPStatus.CREATED, {
            "kickoff": kickoff_result,
            "topics": persisted_topics,
            "relationships": persisted_relationships,
        }

    def handle_v2_list_topics(
        self,
        _request: BaseHTTPRequestHandler,
        _params: dict[str, list[str]],
        *,
        project_id: str,
    ) -> tuple[int, dict[str, Any]]:
        return HTTPStatus.OK, {"topics": self.store.list_topics(project_id=project_id)}

    def handle_v2_create_topic(
        self,
        request: BaseHTTPRequestHandler,
        _params: dict[str, list[str]],
        *,
        project_id: str,
    ) -> tuple[int, dict[str, Any]]:
        """POST /api/v2/projects/{project_id}/topics
        Body: {title, icon, position_x?, position_y?}
        User-created topic (origin='user_manual').
        """
        payload = _read_json_body(request)
        title = str(payload.get("title", "")).strip()
        icon = str(payload.get("icon", "flag")).strip() or "flag"
        if not title:
            return HTTPStatus.BAD_REQUEST, {"error": "title is required"}
        topic = self.store.create_topic(
            project_id=project_id,
            title=title,
            icon=icon,
            position_x=float(payload.get("position_x", 0)),
            position_y=float(payload.get("position_y", 0)),
            origin="user_manual",
        )
        return HTTPStatus.CREATED, {"topic": topic}

    def handle_v2_update_topic(
        self,
        request: BaseHTTPRequestHandler,
        _params: dict[str, list[str]],
        *,
        topic_id: str,
    ) -> tuple[int, dict[str, Any]]:
        """POST /api/v2/topics/{topic_id}/update
        Body: any subset of {title, icon, position_x, position_y, status}.
        Using POST (not PATCH) because the stdlib HTTP server only dispatches
        GET/POST. Semantics are "partial update."
        """
        existing = self.store.get_topic(topic_id)
        if existing is None:
            return HTTPStatus.NOT_FOUND, {"error": "topic_not_found", "topic_id": topic_id}

        payload = _read_json_body(request)
        allowed = {"title", "icon", "position_x", "position_y", "status"}
        updates: dict[str, Any] = {}
        for key in allowed:
            if key in payload:
                val = payload[key]
                if key in {"position_x", "position_y"}:
                    val = float(val)
                updates[key] = val
        if not updates:
            return HTTPStatus.BAD_REQUEST, {"error": "no valid fields to update"}
        topic = self.store.update_topic(topic_id, **updates)
        return HTTPStatus.OK, {"topic": topic}

    def handle_v2_delete_topic(
        self,
        _request: BaseHTTPRequestHandler,
        _params: dict[str, list[str]],
        *,
        topic_id: str,
    ) -> tuple[int, dict[str, Any]]:
        """POST /api/v2/topics/{topic_id}/delete
        Soft-deletes the topic and cascades to its relationships.
        """
        ok = self.store.delete_topic(topic_id)
        if not ok:
            return HTTPStatus.NOT_FOUND, {
                "error": "topic_not_found",
                "topic_id": topic_id,
            }
        return HTTPStatus.OK, {"deleted": True, "topic_id": topic_id}

    def handle_v2_list_decisions(
        self,
        _request: BaseHTTPRequestHandler,
        _params: dict[str, list[str]],
        *,
        topic_id: str,
    ) -> tuple[int, dict[str, Any]]:
        topic = self.store.get_topic(topic_id)
        if topic is None:
            return HTTPStatus.NOT_FOUND, {"error": "topic_not_found"}
        decisions = self.store.list_decisions(
            project_id=topic["project_id"], topic_id=topic_id,
        )
        return HTTPStatus.OK, {"decisions": decisions}

    def handle_v2_create_decision(
        self,
        request: BaseHTTPRequestHandler,
        _params: dict[str, list[str]],
        *,
        topic_id: str,
    ) -> tuple[int, dict[str, Any]]:
        """POST /api/v2/topics/{topic_id}/decisions
        Body: {statement, rationale?, source_turn_id?, proposed_by?}
        Creates a decision in confirmed state by default (since this is
        the "user accepted the proposed decision" path).
        """
        topic = self.store.get_topic(topic_id)
        if topic is None:
            return HTTPStatus.NOT_FOUND, {"error": "topic_not_found"}
        payload = _read_json_body(request)
        statement = str(payload.get("statement", "")).strip()
        if not statement:
            return HTTPStatus.BAD_REQUEST, {"error": "statement is required"}
        decision = self.store.create_decision(
            topic_id=topic_id,
            project_id=topic["project_id"],
            statement=statement,
            proposed_by=str(payload.get("proposed_by", "planner")),
            rationale=(str(payload["rationale"]).strip() if payload.get("rationale") else None),
            source_turn_id=(str(payload["source_turn_id"]) if payload.get("source_turn_id") else None),
            status=str(payload.get("status", "confirmed")),
        )
        return HTTPStatus.CREATED, {"decision": decision}

    def handle_v2_list_project_decisions(
        self,
        _request: BaseHTTPRequestHandler,
        _params: dict[str, list[str]],
        *,
        project_id: str,
    ) -> tuple[int, dict[str, Any]]:
        """GET /api/v2/projects/{project_id}/decisions
        Returns every active decision in the project, grouped client-side
        by topic_id. Used by the canvas so each card can show its own
        accepted decisions without fanning out one request per topic.
        """
        return HTTPStatus.OK, {
            "decisions": self.store.list_decisions(project_id=project_id),
        }

    def handle_v2_delete_decision(
        self,
        _request: BaseHTTPRequestHandler,
        _params: dict[str, list[str]],
        *,
        decision_id: str,
    ) -> tuple[int, dict[str, Any]]:
        """POST /api/v2/decisions/{decision_id}/delete
        Soft-deletes the decision (status -> retracted). Hidden from
        list_decisions thereafter; row stays for audit.
        """
        ok = self.store.delete_decision(decision_id)
        if not ok:
            return HTTPStatus.NOT_FOUND, {
                "error": "decision_not_found_or_already_retracted",
                "decision_id": decision_id,
            }
        return HTTPStatus.OK, {"deleted": True, "decision_id": decision_id}

    def handle_v2_list_relationships(
        self,
        _request: BaseHTTPRequestHandler,
        _params: dict[str, list[str]],
        *,
        project_id: str,
    ) -> tuple[int, dict[str, Any]]:
        return HTTPStatus.OK, {"relationships": self.store.list_relationships(project_id=project_id)}

    def handle_v2_create_relationship(
        self,
        request: BaseHTTPRequestHandler,
        _params: dict[str, list[str]],
        *,
        project_id: str,
    ) -> tuple[int, dict[str, Any]]:
        """POST /api/v2/projects/{project_id}/relationships
        Body: {source_topic_id, target_topic_id, label?}
        User-drawn relationship (origin='user_drawn').
        """
        payload = _read_json_body(request)
        source_id = str(payload.get("source_topic_id", "")).strip()
        target_id = str(payload.get("target_topic_id", "")).strip()
        if not source_id or not target_id:
            return HTTPStatus.BAD_REQUEST, {
                "error": "source_topic_id and target_topic_id are required"
            }
        if source_id == target_id:
            return HTTPStatus.BAD_REQUEST, {
                "error": "a relationship cannot connect a topic to itself"
            }
        label_raw = payload.get("label")
        label = str(label_raw).strip() if label_raw else None
        rel = self.store.create_relationship(
            project_id=project_id,
            source_topic_id=source_id,
            target_topic_id=target_id,
            label=label,
            origin="user_drawn",
        )
        return HTTPStatus.CREATED, {"relationship": rel}

    def handle_v2_delete_relationship(
        self,
        _request: BaseHTTPRequestHandler,
        _params: dict[str, list[str]],
        *,
        relationship_id: str,
    ) -> tuple[int, dict[str, Any]]:
        """POST /api/v2/relationships/{relationship_id}/delete
        (Using POST not DELETE because the stdlib HTTP server only
        dispatches GET/POST.)
        """
        ok = self.store.delete_relationship(relationship_id)
        if not ok:
            return HTTPStatus.NOT_FOUND, {
                "error": "relationship_not_found",
                "relationship_id": relationship_id,
            }
        return HTTPStatus.OK, {"deleted": True, "relationship_id": relationship_id}

    def handle_v2_topic_turn(
        self,
        request: BaseHTTPRequestHandler,
        _params: dict[str, list[str]],
        *,
        topic_id: str,
    ) -> tuple[int, dict[str, Any]]:
        """POST /api/v2/topics/{topic_id}/turn

        Body: {
          "user_answer"?: str,
          "attached_sources"?: list[{display_name, kind, excerpt}],
        }

        If user_answer is provided, it's persisted as a user turn FIRST, then
        the planner is called to produce the next turn. attached_sources are
        passed through to the planner as additional context (uploaded files,
        pasted excerpts, etc.) but are not persisted on the user turn — the
        excerpts already appear in the user_answer body if the UI inlines
        them, and storing the full file content here would bloat the DB.
        Returns the planner turn (persisted) plus the raw adapter response
        (for proposed decisions + consistency flags the UI needs to render).
        """
        payload = _read_json_body(request)
        user_answer = str(payload.get("user_answer", "")).strip()
        attached_sources = payload.get("attached_sources") or []

        topic = self.store.get_topic(topic_id)
        if topic is None:
            return HTTPStatus.NOT_FOUND, {"error": "topic_not_found", "topic_id": topic_id}

        project_id = topic["project_id"]

        # Persist the user's answer first so it shows up in the Q&A context
        # we hand to the planner.
        if user_answer:
            self.store.append_qna_turn(
                topic_id=topic_id,
                project_id=project_id,
                role="user",
                body=user_answer,
                status="answered",
            )

        # Assemble the current-topic view.
        turns = self.store.list_qna_turns(topic_id=topic_id)
        decisions = self.store.list_decisions(project_id=project_id, topic_id=topic_id)
        current_topic_view = {
            "title": topic["title"],
            "icon": topic["icon"],
            "decisions": decisions,
            "turns": turns,
            "open_questions": [],     # TODO: wire open_questions store list
            "risks_assumptions": [],  # TODO: wire risks_assumptions store list
        }

        # Other topics with their decisions — for cross-topic consistency.
        all_topics = self.store.list_topics(project_id=project_id)
        other_topics_view: list[dict[str, Any]] = []
        for ot in all_topics:
            if ot["topic_id"] == topic_id:
                continue
            other_topics_view.append({
                "title": ot["title"],
                "decisions": self.store.list_decisions(
                    project_id=project_id, topic_id=ot["topic_id"],
                ),
            })

        try:
            adapter = self._require_adapter()
            turn_result = adapter.topic_turn(
                current_topic=current_topic_view,
                other_topics=other_topics_view,
                sources=attached_sources or None,
            )
        except RuntimeError as exc:
            return HTTPStatus.INTERNAL_SERVER_ERROR, {
                "error": "planner_call_failed",
                "detail": str(exc),
            }

        # Persist the planner's new turn.
        planner_turn = None
        if turn_result.get("action") != "suggest_close":
            planner_turn = self.store.append_qna_turn(
                topic_id=topic_id,
                project_id=project_id,
                role="planner",
                body=turn_result.get("question") or "",
                status="open",
                why_this_matters=turn_result.get("why_this_matters"),
                action=turn_result.get("action"),
                suggested_responses=turn_result.get("suggested_responses") or [],
            )

        return HTTPStatus.CREATED, {
            "turn_result": turn_result,
            "planner_turn": planner_turn,
        }

    def handle_v2_list_turns(
        self,
        _request: BaseHTTPRequestHandler,
        _params: dict[str, list[str]],
        *,
        topic_id: str,
    ) -> tuple[int, dict[str, Any]]:
        return HTTPStatus.OK, {"turns": self.store.list_qna_turns(topic_id=topic_id)}


# ---------------------------------------------------------------------------
# HTTP plumbing
# ---------------------------------------------------------------------------

def _read_json_body(request: BaseHTTPRequestHandler) -> dict[str, Any]:
    content_length = int(request.headers.get("Content-Length", "0"))
    if content_length <= 0:
        return {}
    raw = request.rfile.read(content_length) or b"{}"
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


class RequestHandler(BaseHTTPRequestHandler):
    app: PlanningStudioApplication

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def _dispatch(self, method: str) -> None:
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)

        for route_method, pattern, handler in self.app.routes():
            if route_method != method:
                continue
            captures = _match_route(pattern, parsed.path)
            if captures is None:
                continue
            try:
                status, payload = handler(self, query, **captures)
            except Exception:  # noqa: BLE001 — unknown handler error path
                # Server-side error. Log the full traceback internally
                # so operators can diagnose; return only a correlation id
                # to the client — stack traces are reconnaissance. The
                # legacy path is gated behind --legacy and shouldn't
                # be reachable in production, but we scrub defensively.
                import uuid as _uuid

                rid = _uuid.uuid4().hex[:12]
                full_tb = traceback.format_exc(limit=10)
                print(
                    f"[legacy handler error rid={rid}]\n{full_tb}",
                    flush=True,
                )
                self._respond(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {"error": "internal_server_error", "request_id": rid},
                )
                return
            self._respond(status, payload)
            return

        self._respond(HTTPStatus.NOT_FOUND, {"error": "Not found"})

    def log_message(self, format: str, *args: object) -> None:
        return

    def _respond(self, status: int, payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        # Permissive CORS for local dev — the Tauri/Vite app runs on a
        # different origin. Tighten when we have a real auth layer.
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(encoded)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()


def create_app() -> PlanningStudioApplication:
    return PlanningStudioApplication(PlanningStudioStore(load_config()))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Inspira backend service.")
    parser.parse_args(argv)
    config = load_config()
    app = PlanningStudioApplication(PlanningStudioStore(config))
    RequestHandler.app = app
    server = ThreadingHTTPServer((config.host, config.port), RequestHandler)
    try:
        print(f"Inspira service listening on http://{config.host}:{config.port}")
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()
    return 0
