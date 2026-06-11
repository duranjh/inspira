"""Provider-agnostic interface for the ``planning_interviewer`` role.

Every provider (OpenAI, Claude, local model, ...) implements this contract.
The app layer never talks to a provider SDK directly — it instantiates an
adapter and calls these methods. The dict shapes match the tool schemas
in ``schemas.py`` so callers can treat them as typed contracts.

Only ``kickoff`` is implemented in the first adapter iteration. The other
four methods are declared here so the interface is complete and mypy can
catch missing implementations as new modes come online.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class PlanningInterviewer(ABC):
    """Abstract base class for planner adapters."""

    # ---------- Mode A: kickoff ----------
    @abstractmethod
    def kickoff(
        self,
        *,
        user_idea: str,
        attached_sources: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Map a kickoff idea into 5–10 topic cards + relationships.

        Returns a dict matching ``KICKOFF_RESPONSE_SCHEMA``.

        Args:
            user_idea: free-text description from the user's kickoff textarea.
            attached_sources: optional list of source summaries (each with at
                minimum ``display_name``, ``kind``, and ``excerpt`` keys).
        """

    # ---------- Mode B: topic_interview ----------
    def topic_turn(
        self,
        *,
        current_topic: dict[str, Any],
        other_topics: list[dict[str, Any]],
        sources: list[dict[str, Any]] | None = None,
        reasoning_effort: str | None = None,
        timeout_s: float | None = None,
    ) -> dict[str, Any]:
        """One planner turn inside a topic's Q&A. Returns ``TOPIC_TURN_SCHEMA`` shape.

        Not yet implemented in v0.1. Raise ``NotImplementedError`` until wired.
        """
        raise NotImplementedError("topic_turn is not yet implemented in this adapter.")

    # ---------- Mode C: composer_route ----------
    def composer_route(
        self,
        *,
        user_text: str,
        existing_topics: list[dict[str, Any]],
        active_topic_id: str | None = None,
    ) -> dict[str, Any]:
        """Route a composer input. Returns ``COMPOSER_ROUTING_SCHEMA`` shape."""
        raise NotImplementedError("composer_route is not yet implemented in this adapter.")

    # ---------- Mode D: summary_synthesis ----------
    def summary_synthesis(
        self,
        *,
        project: dict[str, Any],
        topics: list[dict[str, Any]],
        prior_summary: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Regenerate / update the Plan Summary. Returns ``SUMMARY_SYNTHESIS_SCHEMA`` shape."""
        raise NotImplementedError("summary_synthesis is not yet implemented in this adapter.")

    # ---------- Mode E: propagation_preview ----------
    def propagation_preview(
        self,
        *,
        edited_decision: dict[str, Any],
        other_topics: list[dict[str, Any]],
        current_summary: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Preview effects of a decision edit. Returns ``PROPAGATION_PREVIEW_SCHEMA`` shape."""
        raise NotImplementedError("propagation_preview is not yet implemented in this adapter.")
