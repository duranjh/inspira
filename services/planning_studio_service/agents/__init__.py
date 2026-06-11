"""Planner agent — provider-agnostic contract + adapters.

The ``planning_interviewer`` role is the LLM-facing contract for Inspira's
topic interview, cross-topic consistency, and plan summary synthesis. The
prompt definitions in ``prompts.py`` are the source of truth.

This package:

- ``prompts``   — system-prompt string constants (base + per-mode).
- ``schemas``   — JSON Schema dicts for the tool-call outputs.
- ``base``      — abstract interface every provider adapter implements.
- ``openai_adapter`` — OpenAI Responses / Chat Completions implementation.

Claude and other providers get their own adapter files alongside
``openai_adapter``; the app layer picks one at runtime by config. The
product contract is the shape of the returned dict, not the model that
produced it.

Auxiliary artifact-writer modes (plan summary, outline, topic deduper)
ship as their own adapters in this package — same OpenAI plumbing
(circuit breaker + transient retry) via ``_call_with_toolcall_retry``,
distinct prompts and schemas in ``prompts_extra.py`` / ``schemas_extra.py``.
"""

from .base import PlanningInterviewer
from .claude_adapter import ClaudeConfig, ClaudePlanningInterviewer
from .code_scaffold import (
    ClaudeCodeScaffoldAdapter,
    ClaudeCodeScaffoldConfig,
    CodeScaffoldAdapter,
    CodeScaffoldConfig,
)
from .deduper import DeduperAdapter, DeduperConfig
from .openai_adapter import OpenAIConfig, OpenAIPlanningInterviewer
from .outline import OutlineAdapter, OutlineConfig
from .plan_summary import PlanSummaryAdapter, PlanSummaryConfig
from .tiers import (
    ALLOWED_TIERS_BY_PLAN,
    CLAUDE_FRONTIER_MODEL,
    CREDIT_MULTIPLIER_BY_TIER,
    DEFAULT_TIER_BY_PLAN,
    ModelTier,
    resolve_tier_for_user,
    tier_catalog_for_plan,
    tier_to_adapter,
    tier_to_claude_model,
    tier_to_openai_model,
)

__all__ = [
    "PlanningInterviewer",
    "OpenAIConfig",
    "OpenAIPlanningInterviewer",
    "ClaudeConfig",
    "ClaudePlanningInterviewer",
    "PlanSummaryAdapter",
    "PlanSummaryConfig",
    "OutlineAdapter",
    "OutlineConfig",
    "DeduperAdapter",
    "DeduperConfig",
    "CodeScaffoldAdapter",
    "CodeScaffoldConfig",
    "ClaudeCodeScaffoldAdapter",
    "ClaudeCodeScaffoldConfig",
    "ModelTier",
    "ALLOWED_TIERS_BY_PLAN",
    "DEFAULT_TIER_BY_PLAN",
    "CREDIT_MULTIPLIER_BY_TIER",
    "CLAUDE_FRONTIER_MODEL",
    "resolve_tier_for_user",
    "tier_to_openai_model",
    "tier_to_claude_model",
    "tier_to_adapter",
    "tier_catalog_for_plan",
]
