// Topic Detail — the Q&A interview inside a topic card.
//
// Three logical sections that stack on narrow screens:
//   - Decisions column (left)     : decisions accumulated for this topic
//   - Q&A thread (center)         : alternating planner / user turns with
//                                    suggested-response chips under each
//                                    planner question
//   - Context column (right)      : conflict flags + related topics
//
// The composer at the bottom is scoped to this topic — typing and hitting
// send posts to /api/v2/topics/{id}/turn with the user_answer. The planner's
// next question returns and appears at the end of the thread.
//
// First-open behavior: if the topic has no turns yet, we auto-kick-off the
// interview by calling topicTurn with no user_answer. That makes the
// planner's opening question the first thing the user sees.

import {
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
} from "react";

import {
  api,
  getLastLlmMode,
  subscribeLlmMode,
  type AttachedSource,
  type Checkpoint,
  type ConflictResolution,
  type Decision,
  type LlmMode,
  type ModelTier,
  type ModelTierCatalog,
  type QnaTurn,
  type Relationship,
  type Topic,
  type TopicColor,
  type TopicDeletionSuggestion,
  type TopicTurn as PlannerTurnResult,
  type UsageView,
  type TopicTurnEnvelope,
  type TopicProvenanceRow,
  type V2Project,
} from "./api";
import { HIDE_UPGRADE } from "../../lib/featureFlags";
import { ComposerShell } from "./ComposerShell";
import { topicToMarkdown } from "./export";
import { useRealtimeContext } from "./RealtimeContext";
import { ContradictionDialog } from "./ContradictionDialog";
import {
  CommentsProvider,
  CommentsLayer,
  CommentTargetWrapper,
  CommentChip,
  DiffBadge,
  useVersionAge,
} from "./comments";
import { fetchUrlAsSource, textAsSource } from "./sources";
import { ModelTierChip } from "./ModelTierChip";
import { TopicColorPicker } from "./TopicColorPicker";
import { ReasoningTrace } from "./ReasoningTrace";
import {
  SubAgentStream,
  type LiveDecisionEvent,
} from "./SubAgentStream";
import { TopicSubAgentPulse } from "./TopicSubAgentPulse";
import {
  SkeletonCard,
  SkeletonColumn,
  SkeletonLine,
} from "../../components/Skeletons";
import { TurnSkeleton } from "../../components/Skeleton";
import { Coachmark, type CoachmarkStep } from "../../components/Coachmark";
import { Dialog } from "../../components/dialogs/Dialog";
import {
  TopicCompletionDialog,
  isCompletionSuppressed,
} from "../../components/dialogs";
import { toast } from "../../components/ToastProvider";
import { useDismissOn } from "../../hooks/useDismissOn";
import { useFocusTrap } from "../../hooks/useFocusTrap";
import { t } from "../../i18n";

export type TopicDetailProps = {
  topic: Topic;
  onClose: () => void;
  // Parent notifies us of sibling topics so we can resolve cross-topic
  // conflict-flag references without making extra round trips.
  allTopics: Topic[];
  // Relationships in the project, used to filter the Related-topics list
  // down to topics the planner actually connected to this one.
  relationships: Relationship[];
  // The viewport rect of the source topic card at click time. When
  // present, the detail morphs open from this rect (FLIP-style transform)
  // and morphs back to it on close. Null = open immediately, no morph.
  originRect: DOMRect | null;
  // W2 η — the parent canvas's project, used by the reasoning expander
  // to (a) read `metadata.theme_id` for SSE event filtering and (b) call
  // the provenance REST endpoint on cold-opens. Optional so legacy
  // callers (kickoff path) keep working — when null, the live stream
  // just stays dormant and the cold-open lazy-load is skipped.
  project?: V2Project | null;
};

// File types we'll read inline as text. Anything else gets attached as a
// "binary file" reference (just the filename + mime kind) so the planner
// at least knows it exists. Real PDF/image extraction is a later pass.
const TEXT_LIKE_MIME_PATTERN =
  /^text\/|^application\/(json|xml|x-yaml|yaml|csv|toml)/;
const MAX_TEXT_EXCERPT_CHARS = 8000;

// Paste-detection threshold: anything longer than this AND containing a
// newline is offered as a standalone AttachedSource rather than being
// dumped into the composer. Short paragraphs and one-liners still paste
// inline as usual.
const PASTE_AS_SOURCE_MIN_CHARS = 200;

// Composer draft autosave. We stash the in-flight composer text in
// localStorage so reloads and accidental tab-closes don't eat mid-sentence
// thoughts. Scope is per topic — the `(project_id, user_id)` axes are
// implicit because topic_ids are globally unique server-side. Attachments
// (File objects, non-serializable) are intentionally NOT persisted.
const DRAFT_STORAGE_PREFIX = "inspira_draft_";
const DRAFT_DEBOUNCE_MS = 400;
const DRAFT_SAVED_INDICATOR_MS = 1500;

function draftKey(topicId: string): string {
  return `${DRAFT_STORAGE_PREFIX}${topicId}`;
}

// Safe localStorage accessors — SSR guard (no window) and swallow quota /
// security errors so a storage hiccup never breaks the composer itself.
function readDraft(topicId: string): string {
  if (typeof window === "undefined") return "";
  try {
    return window.localStorage.getItem(draftKey(topicId)) ?? "";
  } catch {
    return "";
  }
}

function writeDraft(topicId: string, value: string): void {
  if (typeof window === "undefined") return;
  try {
    const key = draftKey(topicId);
    if (value.length === 0) {
      window.localStorage.removeItem(key);
    } else {
      window.localStorage.setItem(key, value);
    }
  } catch {
    // Quota exceeded, Safari private mode, disabled storage, etc. The
    // draft simply won't persist — the composer still works normally.
  }
}

function clearDraft(topicId: string): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.removeItem(draftKey(topicId));
  } catch {
    /* best effort */
  }
}

type LoadingState =
  | { kind: "initializing" }
  | { kind: "ready" }
  // ``thinking_message`` is the latest ``heartbeat.message`` from the
  // SSE turn-stream — backend emits progressive status strings every
  // ~3s ("Reading the thread…" → "Weighing options…" → "Drafting a
  // response…"). Without feeding this into the skeleton's status line
  // the UI shows a static "Planner is thinking…" for 30-40s and feels
  // hung.
  | { kind: "asking_planner"; thinking_message?: string }
  | { kind: "error"; message: string };

// First-topic-open coachmark flow. Fires once per user the first time
// they open any topic detail — the storage key is flipped to "true" by
// the Coachmark component when the user finishes or skips. Selectors
// resolve against live DOM and any step whose target can't be found is
// silently skipped (see components/Coachmark.tsx).
const TOPIC_DETAIL_COACH_STORAGE_KEY = "inspira_onboarded_topic_detail";
const TOPIC_DETAIL_COACH_STEPS: CoachmarkStep[] = [
  {
    id: "topic-detail-first-question",
    // First planner question bubble in the thread. The opening turn is
    // always a planner turn, so the first `.turn--planner` is the right
    // target even after the user has sent replies.
    targetSelector: ".topic-detail__thread .turn--planner",
    title: t("canvas_onboard_topic.1.title"),
    body: t("canvas_onboard_topic.1.body"),
    placement: "left",
  },
  {
    id: "topic-detail-suggestions",
    // Prefer the first suggestion chip; Coachmark falls back to skipping
    // this step silently if the planner's opening turn didn't include
    // suggested responses. In that case the composer below still teaches
    // the "type your own" side on its own.
    targetSelector: ".topic-detail__thread .turn--planner .turn__suggestion",
    title: t("canvas_onboard_topic.2.title"),
    body: t("canvas_onboard_topic.2.body"),
    placement: "top",
  },
  {
    id: "topic-detail-composer",
    // Spotlight the composer last; its copy ("Close the panel and look
    // at the canvas") nudges the user back out to the broader view.
    targetSelector: ".topic-detail__composer",
    title: t("canvas_onboard_topic.3.title"),
    body: t("canvas_onboard_topic.3.body"),
    placement: "top",
  },
];

// Small muted pill shown below the composer after an autosave. Inline
// styles so we don't have to touch App.css; keeps the visual language
// understated ("don't shout about it"). Opacity transitions make the
// fade feel calm rather than twitchy.
function draftSavedPillStyle(visible: boolean): React.CSSProperties {
  return {
    alignSelf: "flex-end",
    marginTop: "0.35rem",
    marginRight: "0.25rem",
    fontSize: "0.72rem",
    fontFamily: "var(--ff-serif)",
    fontStyle: "italic",
    color: "var(--ink-3, #8a7d6c)",
    letterSpacing: "0.01em",
    opacity: visible ? 0.7 : 0,
    transition: "opacity 250ms ease-out",
    pointerEvents: "none" as const,
    // Keep the vertical space reserved so the composer doesn't reflow
    // on fade-in/out. Height tuned to the text metrics.
    minHeight: "1em",
  };
}

export function TopicDetail({
  topic,
  onClose,
  allTopics,
  relationships,
  originRect,
  project = null,
}: TopicDetailProps) {
  // Realtime collab: acquire an exclusive focus lock on this topic
  // for the duration the drawer is open. While we hold the lock:
  //  - Our own UI renders normally.
  //  - Other users see the topic card glow in our color on the canvas
  //    and get a "<displayName> is answering" banner in the drawer.
  //  - If someone else holds the lock when we arrive, we immediately
  //    drop into read-only spectate mode.
  const realtime = useRealtimeContext();
  const lock = realtime.locks[topic.topic_id] ?? null;
  const isLockedByOther =
    lock !== null && lock.ownerSessionId !== realtime.mySessionId;
  const sendFocusTopicRef = useRef(realtime.sendFocusTopic);
  sendFocusTopicRef.current = realtime.sendFocusTopic;
  useEffect(() => {
    const id = topic.topic_id;
    sendFocusTopicRef.current(id);
    return () => {
      sendFocusTopicRef.current(null);
    };
  }, [topic.topic_id]);

  // Drawer is position:fixed with its own internal overflow scroll; the
  // backdrop dims (and intercepts pointer events to) the rest of the
  // viewport, matching DecisionSummaryDrawer's no-body-lock behavior.

  const [turns, setTurns] = useState<QnaTurn[]>([]);
  const [decisions, setDecisions] = useState<Decision[]>([]);
  const [checkpoints, setCheckpoints] = useState<Checkpoint[]>(
    (topic.metadata?.checkpoints as Checkpoint[] | undefined) ?? [],
  );
  // W2 η — reasoning expander state. Lives at the TopicDetail level so
  // collapse-state survives non-mount re-renders (composer typing, etc.).
  const [reasoningOpen, setReasoningOpen] = useState(false);
  const [streamOpen, setStreamOpen] = useState(false);
  const [liveEvents, setLiveEvents] = useState<LiveDecisionEvent[]>([]);
  const [provenanceByDecisionId, setProvenanceByDecisionId] = useState<
    Map<string, TopicProvenanceRow[]>
  >(() => new Map());
  // Reset η state when the user navigates to a different topic in the
  // same drawer — without this, ghost decisions from the previous topic
  // would linger in the live stream.
  useEffect(() => {
    setReasoningOpen(false);
    setStreamOpen(false);
    setLiveEvents([]);
    setProvenanceByDecisionId(new Map());
  }, [topic.topic_id]);

  // W2 η — listen to δ's window-event SSE bridge for per-topic events.
  // The hook itself is mounted up at ProjectCanvas; here we just attach
  // listeners. Filter by:
  //   - decision.drafted: theme_id + topic_index === topic.order_index
  //   - sub_agent.completed: theme_id only (whole canvas is one theme)
  //
  // Caveat: order_index is mutable via drag-to-reorder. If the user
  // reorders mid-LLM-run (vanishingly rare given run wallclock), some
  // events miss. Acceptable; document rather than over-engineer.
  const themeId =
    typeof project?.metadata?.theme_id === "string"
      ? (project.metadata.theme_id as string)
      : null;
  const projectIdForReasoning = project?.project_id ?? null;
  useEffect(() => {
    if (!themeId) return;
    if (typeof window === "undefined") return;

    const onDecisionDrafted = (e: Event) => {
      const detail = (e as CustomEvent<{
        theme_id?: string;
        topic_index?: number;
        decision?: {
          decision_id: string;
          statement: string;
          rationale: string | null;
          subject: string;
        };
        provenance?: Array<{ feedback_item_id: string; weight: number }>;
      }>).detail;
      if (!detail || detail.theme_id !== themeId) return;
      if (detail.topic_index !== topic.order_index) return;
      const dec = detail.decision;
      if (!dec) return;
      const receivedAt = new Date().toISOString();
      setLiveEvents((prev) => {
        // Idempotent on decision_id — guards against dev-mode double
        // dispatch + SSE reconnect replay.
        if (prev.some((p) => p.decision_id === dec.decision_id)) return prev;
        return [
          ...prev,
          {
            decision_id: dec.decision_id,
            statement: dec.statement,
            rationale: dec.rationale,
            subject: dec.subject,
            received_at: receivedAt,
          },
        ];
      });
      // Provenance carries enough to render chips, but the live SSE
      // payload doesn't include the full feedback_item (title/source/
      // body). Render placeholders here; the cold-open REST fallback
      // will fill them in if the user reopens the canvas later.
      if (detail.provenance && detail.provenance.length > 0) {
        const livePlaceholders: TopicProvenanceRow[] = detail.provenance.map(
          (p) => ({
            decision_id: dec.decision_id,
            feedback_item_id: p.feedback_item_id,
            weight: p.weight,
            feedback_item: {
              item_id: p.feedback_item_id,
              title: p.feedback_item_id,
              body: "",
              source: "live",
              received_at: null,
              ingested_at: receivedAt,
            },
          }),
        );
        setProvenanceByDecisionId((prev) => {
          const next = new Map(prev);
          next.set(dec.decision_id, livePlaceholders);
          return next;
        });
      }
    };

    const onSubAgentCompleted = (e: Event) => {
      const detail = (e as CustomEvent<{ theme_id?: string }>).detail;
      if (!detail || detail.theme_id !== themeId) return;
      // Refresh the canonical decisions list so the left column
      // reflects the new rows the orchestrator just persisted.
      api
        .listDecisions(topic.topic_id)
        .then((res) => setDecisions(res.decisions))
        .catch(() => {
          /* swallow — left column already shows what we had pre-event */
        });
    };

    window.addEventListener(
      "inspira:sse:decision.drafted",
      onDecisionDrafted,
    );
    window.addEventListener(
      "inspira:sse:sub_agent.completed",
      onSubAgentCompleted,
    );
    return () => {
      window.removeEventListener(
        "inspira:sse:decision.drafted",
        onDecisionDrafted,
      );
      window.removeEventListener(
        "inspira:sse:sub_agent.completed",
        onSubAgentCompleted,
      );
    };
  }, [themeId, topic.order_index, topic.topic_id]);

  // W2 η — REST fallback for cold-opens. ReasoningTrace fires this once
  // on first expander-open. Wrapped in useCallback so identity is stable
  // across renders (otherwise ReasoningTrace's lazy-load effect would
  // re-fire on every parent render).
  //
  // Merge semantics, not replace: the SSE handler may have already
  // stashed live placeholders for in-flight decisions (title set to the
  // feedback_item_id, source = "live"). For each decision_id REST
  // returns, we replace the placeholder with the full server row. Live-
  // only decisions REST doesn't yet know about (mid-flight, not
  // persisted) are preserved.
  const loadProvenanceFromRest = useCallback(() => {
    if (!projectIdForReasoning) return;
    api
      .listTopicProvenance(projectIdForReasoning, topic.topic_id)
      .then((res) => {
        if (res.provenance.length === 0) return;
        setProvenanceByDecisionId((prev) => {
          const next = new Map(prev);
          const grouped = new Map<string, TopicProvenanceRow[]>();
          for (const row of res.provenance) {
            const list = grouped.get(row.decision_id) ?? [];
            list.push(row);
            grouped.set(row.decision_id, list);
          }
          for (const [decisionId, rows] of grouped) {
            next.set(decisionId, rows);
          }
          return next;
        });
      })
      .catch(() => {
        /* swallow — empty placeholder is the graceful fallback */
      });
  }, [projectIdForReasoning, topic.topic_id]);
  // Local color state — optimistically reflects the user's swatch pick
  // before the server round-trip completes. Seeded from the topic prop
  // and re-seeded when the prop changes (e.g. parent refetches). On API
  // failure we revert to the prop value and surface a toast.
  const [color, setColor] = useState<TopicColor | null>(topic.color ?? null);
  useEffect(() => {
    setColor(topic.color ?? null);
  }, [topic.topic_id, topic.color]);
  const handleColorChange = useCallback(
    async (next: TopicColor | null) => {
      const previous = color;
      setColor(next);
      try {
        await api.updateTopicColor(topic.topic_id, next);
        toast.success(t("topic_color.saved"));
        // Tell the canvas to refetch so the TopicNode's color accent
        // updates without requiring a project reload. Without this the
        // drawer's swatch reflected the new color but the canvas card
        // kept the old one until the next page refresh — founder bug
        // report 2026-04-26.
        if (typeof window !== "undefined") {
          window.dispatchEvent(new CustomEvent("inspira:topics-changed"));
        }
      } catch (err) {
        console.error("[Inspira] failed to update topic color", err);
        setColor(previous);
        toast.error(t("topic_color.save_failed"));
      }
    },
    [color, topic.topic_id],
  );
  const [latestPlannerResult, setLatestPlannerResult] =
    useState<PlannerTurnResult | null>(null);
  const [state, setState] = useState<LoadingState>({ kind: "initializing" });
  // Hydrate the composer from localStorage on first render so a reload or
  // accidental navigation away doesn't lose a mid-sentence draft. The lazy
  // initializer form ensures we only hit localStorage once per mount.
  const [composer, setComposer] = useState<string>(() =>
    readDraft(topic.topic_id),
  );
  // Transient "Draft saved" pill: true for DRAFT_SAVED_INDICATOR_MS after
  // a debounced save fires, then fades out.
  const [draftSavedVisible, setDraftSavedVisible] = useState(false);
  const [pendingAttachments, setPendingAttachments] = useState<
    AttachedSource[]
  >([]);
  // LLM model-tier picker state. ``modelTierCatalog`` is fetched once on
  // mount; ``perTurnTier`` is the per-turn override (null = "use the
  // current default"). Reset to null after each send.
  const [modelTierCatalog, setModelTierCatalog] =
    useState<ModelTierCatalog | null>(null);
  const [perTurnTier, setPerTurnTier] = useState<ModelTier | null>(null);
  // #080 monthly usage view (per-tier and per-business-plan counters).
  // Fetched once on mount + after every turn submission so the
  // ModelTierChip dropdown can show "X% used this month" sub-lines on
  // each tier row. Failure to fetch is non-fatal: usage stays null and
  // the chip simply doesn't render the sub-line (catalog/picker logic
  // is unaffected).
  const [usageView, setUsageView] = useState<UsageView | null>(null);
  // Tracks the in-flight PATCH for "Set {tier} as default" so the menu
  // button can render a disabled/spinner state while the request is
  // outstanding. Cleared on both success and failure.
  const [settingDefaultTier, setSettingDefaultTier] = useState(false);
  const [upgradeDialogOpen, setUpgradeDialogOpen] = useState(false);
  // BYOK composer badge. Updated on every LLM-backed response via the
  // ``X-Inspira-Llm-Mode`` header — see ``api.ts::subscribeLlmMode``.
  // ``null`` until the first turn response arrives; the badge only
  // renders once we've heard from the server at least once.
  const [llmMode, setLlmMode] = useState<LlmMode | null>(() =>
    getLastLlmMode(),
  );
  // Transient feedback for the "Copy as Markdown" button. "idle" shows the
  // default label; "copied" swaps to a confirmation for ~1.5s; "error"
  // surfaces a clipboard failure (rare — usually only happens in non-
  // secure contexts where navigator.clipboard is unavailable).
  const [copyState, setCopyState] = useState<"idle" | "copied" | "error">(
    "idle",
  );
  // Session-only dismissal of the completion banner. When the user taps
  // "Keep asking" we don't pester them again for this open session; the
  // banner will re-appear next time they open the topic.
  const [completionBannerDismissed, setCompletionBannerDismissed] =
    useState(false);

  // Drawer is single-column at every viewport width. Mobile-only sheet +
  // pill states (progressSheetOpen, decisionsCollapsed, contextCollapsed)
  // were dropped in the v5 drawer rewrite — the new 540px right drawer
  // collapses to viewport width on narrow screens via `max-width: 100%`.
  const copyResetRef = useRef<number | null>(null);
  const threadRef = useRef<HTMLDivElement | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  // Draft auto-save plumbing. One setTimeout ref per: the debounced write
  // itself, and the fade-out for the "Draft saved" pill. setTimeout-based
  // debounce keeps dependencies zero and avoids pulling in a library.
  const draftWriteTimerRef = useRef<number | null>(null);
  const draftPillTimerRef = useRef<number | null>(null);
  // Skip persisting the very first value because it's the hydrated draft —
  // writing it back immediately would flash the "Draft saved" pill on
  // open even though the user hasn't typed anything.
  const draftHydratedRef = useRef(false);

  const titleByTopicId = useMemo(
    () =>
      Object.fromEntries(
        allTopics.map((t) => [t.topic_id, t.title] as const),
      ),
    [allTopics],
  );

  // Topics directly connected to this one via a relationship — either
  // direction. The Context panel shows these instead of every topic in
  // the project, since most projects have a dozen+ topics and only a
  // handful are actually relevant context for any given one.
  const relatedTopics = useMemo(() => {
    const connectedIds = new Set<string>();
    for (const r of relationships) {
      if (r.source_topic_id === topic.topic_id) {
        connectedIds.add(r.target_topic_id);
      } else if (r.target_topic_id === topic.topic_id) {
        connectedIds.add(r.source_topic_id);
      }
    }
    return allTopics.filter((t) => connectedIds.has(t.topic_id));
  }, [allTopics, relationships, topic.topic_id]);

  // ---- Drawer plumbing -------------------------------------------------
  //
  // 540px right-fixed drawer (v5 design pivot). Mirrors DecisionSummary-
  // Drawer's pattern: backdrop click-to-close, ESC via useDismissOn, Tab-
  // cycling via useFocusTrap, conditional initial focus that skips when
  // the user is mid-typing in an editable. The `originRect` prop is now
  // unused (kept on the type for parent-API compatibility); entrance is
  // a CSS slideInRight matching DSD.
  const drawerRef = useRef<HTMLElement | null>(null);
  // `requestClose` keeps the same name so all downstream call sites
  // (completion flow, send-success path, suggest_close branch, completion
  // banner handler, etc.) stay unchanged.
  const requestClose = useCallback(() => {
    onClose();
  }, [onClose]);

  useDismissOn({ enabled: true, onDismiss: requestClose });
  const { onKeyDown: drawerKeyDown } = useFocusTrap(drawerRef, {
    enabled: true,
    autoFocus: false,
    restoreFocus: true,
  });

  // Initial focus moves to the drawer container on open so screen readers
  // announce the dialog. Guard: don't hijack focus if the user is already
  // mid-typing in an editable — preserves the canvas-side composer flow.
  useEffect(() => {
    const active = document.activeElement as HTMLElement | null;
    const isEditable =
      !!active &&
      (active.isContentEditable ||
        active.tagName === "INPUT" ||
        active.tagName === "TEXTAREA" ||
        active.tagName === "SELECT");
    if (!isEditable) drawerRef.current?.focus();
    // Mount-only; topic prop changes do not re-focus.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Handle planner-proposed decisions returned with a turn.
  //
  // The backend now saves ALL proposed decisions during the turn (routing
  // each to the correct topic). For decisions that stayed on the current
  // topic we refetch the decision list below. For rerouted decisions we
  // show a toast and fire `inspira:decisions-changed` so the canvas can
  // merge the new decision into the correct topic's state.
  const handleProposedDecisions = useCallback(
    (envelope: TopicTurnEnvelope) => {
      const rerouted = envelope.rerouted_decisions ?? [];

      // Show a toast for each rerouted decision and fire a canvas-refresh event.
      for (const r of rerouted) {
        // Find the statement from turn_result for display purposes.
        const proposal = envelope.turn_result.proposed_decisions.find(
          (p) => p.target_topic_title === r.actual_topic_title,
        );
        const preview = proposal
          ? `"${proposal.statement.slice(0, 60)}${proposal.statement.length > 60 ? "…" : ""}"`
          : "A decision";
        toast.info(`${preview} saved to ${r.actual_topic_title}`);
      }

      // Auto-created topic: notify the canvas to refetch so the new card appears.
      if (envelope.created_topic) {
        const newTitle = envelope.created_topic.topic.title;
        toast.success(t("topic_detail.new_topic_created", { title: newTitle }));
        window.dispatchEvent(new CustomEvent("inspira:topics-changed"));
      }

      // Deletion suggestion: forward to the canvas via event so TopicNode can render the banner.
      if (envelope.topic_deletion_suggestion) {
        window.dispatchEvent(
          new CustomEvent("inspira:topic-deletion-suggested", {
            detail: envelope.topic_deletion_suggestion as TopicDeletionSuggestion,
          }),
        );
      }

      if (rerouted.length > 0) {
        // Signal the canvas to refetch decisions for affected topics.
        try {
          window.dispatchEvent(
            new CustomEvent("inspira:decisions-changed", {
              detail: {
                rerouted_decisions: rerouted,
              },
            }),
          );
        } catch {
          /* best effort — older browsers without CustomEvent ctor */
        }
      }
    },
    [],
  );

  // First-open: fetch existing turns + decisions, and if there are no
  // planner turns yet, trigger the opening question.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [turnsRes, decisionsRes] = await Promise.all([
          api.listTurns(topic.topic_id),
          api.listDecisions(topic.topic_id),
        ]);
        if (cancelled) return;
        setTurns(turnsRes.turns);
        setDecisions(decisionsRes.decisions);

        // Empty topic → auto-kick-off the interview.
        if (turnsRes.turns.length === 0) {
          setState({ kind: "asking_planner" });
          // Phase 1 SSE streaming for the auto-kick interview turn —
          // forward backend heartbeat messages into the loading state
          // so the skeleton status line animates through the progress
          // script ("Reading the thread…" → "Weighing options…" → ...)
          // instead of showing one static line for the full LLM wait.
          let envelope;
          try {
            envelope = await api.topicTurnStream(
              topic.topic_id,
              undefined,
              undefined,
              null,
              {
                onHeartbeat: (data) => {
                  if (cancelled) return;
                  const msg = data?.message?.trim();
                  setState({
                    kind: "asking_planner",
                    thinking_message: msg || undefined,
                  });
                },
              },
            );
          } catch (err) {
            if (
              err instanceof Error
              && /streaming_disabled|503/.test(err.message)
            ) {
              envelope = await api.topicTurn(topic.topic_id);
            } else {
              throw err;
            }
          }
          if (cancelled) return;
          setLatestPlannerResult(envelope.turn_result);
          handleProposedDecisions(envelope);
          if (envelope.planner_turn) {
            setTurns((prev) => [...prev, envelope.planner_turn!]);
          }
          if (envelope.checkpoints) {
            setCheckpoints(envelope.checkpoints);
          }
          setState({ kind: "ready" });
        } else {
          setState({ kind: "ready" });
        }
      } catch (err) {
        if (cancelled) return;
        console.error("[Inspira] topic detail load failed", err);
        setState({
          kind: "error",
          message: t("errors.turn_failed"),
        });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [topic.topic_id, handleProposedDecisions]);

  // Auto-scroll the thread to the newest turn.
  useEffect(() => {
    threadRef.current?.scrollTo({
      top: threadRef.current.scrollHeight,
      behavior: "smooth",
    });
  }, [turns.length]);

  // Fetch the model-tier catalog once on mount. The chip renders as null
  // until this lands; a failure just hides the chip silently — turns
  // still work with the backend defaults.
  //
  // On success we also seed ``perTurnTier`` from ``catalog.persisted_default``
  // so the chip reflects the user's saved preference (set in Account Settings)
  // on first open, rather than relying purely on the chip's fallback display.
  // ``persisted_default`` is null when the user has never set a preference;
  // in that case we leave ``perTurnTier`` as null so the backend resolves the
  // plan default as usual.
  useEffect(() => {
    let cancelled = false;
    void api
      .listModelTiers()
      .then((catalog) => {
        if (!cancelled) {
          setModelTierCatalog(catalog);
          // Seed from the persisted preference so the per-turn chip opens
          // showing exactly what the user chose in Account Settings.
          if (catalog.persisted_default !== null) {
            setPerTurnTier(catalog.persisted_default);
          }
        }
      })
      .catch((err) => {
        console.warn("[Inspira] failed to load model-tier catalog", err);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // #080 — fetch monthly usage on mount. Re-fetched after every turn
  // submission below (in the topic_turn submit handler's success branch)
  // so the dropdown indicator stays current. Failures are silent — the
  // chip just renders without the sub-line.
  useEffect(() => {
    let cancelled = false;
    void api
      .getUsage()
      .then((view) => {
        if (!cancelled) setUsageView(view);
      })
      .catch((err) => {
        console.warn("[Inspira] failed to load usage", err);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Subscribe to the LlmMode banner from api.ts. The backend sets
  // X-Inspira-Llm-Mode: house|byok on every LLM-backed response; we keep
  // the latest value in a module-level state and re-render the pill
  // whenever it changes. Unsubscribing on unmount prevents leaks when
  // the detail drawer closes.
  useEffect(() => {
    return subscribeLlmMode((mode) => setLlmMode(mode));
  }, []);

  // First-topic-open coachmark: fire a 3-step Coachmark flow the very
  // first time a user lands inside any topic detail. Gated on (a) the
  // localStorage seen flag, and (b) the initial turn load landing so the
  // first planner question bubble exists in the DOM for the spotlight to
  // measure. The morph-in gate was dropped with the v5 drawer rewrite —
  // the drawer's CSS slideInRight settles in 220ms, well before the
  // 400ms post-ready delay below.
  const [topicDetailCoachActive, setTopicDetailCoachActive] = useState(false);
  useEffect(() => {
    if (typeof window === "undefined") return;
    if (state.kind !== "ready") return;
    if (turns.length === 0) return;
    let seen = false;
    try {
      seen =
        window.localStorage.getItem(TOPIC_DETAIL_COACH_STORAGE_KEY) === "true";
    } catch {
      // storage disabled — treat as unseen so the tour still fires once;
      // Coachmark itself guards against re-render loops.
    }
    if (seen) return;
    // Small delay so the thread's smooth-scroll and any in-flight layout
    // settle before we measure the first planner bubble's rect.
    const id = window.setTimeout(() => setTopicDetailCoachActive(true), 400);
    return () => window.clearTimeout(id);
  }, [state.kind, turns.length]);

  // Submit the composer content as the user's next answer. Optionally
  // takes a body string AND an attachment list — both default to the
  // current composer / pendingAttachments state, but callers (e.g. the
  // suggested-response auto-send path) can override.
  const submitAnswer = useCallback(
    async (rawText?: string, attachments?: AttachedSource[]) => {
      const text = (rawText ?? composer).trim();
      const attached = attachments ?? pendingAttachments;
      if (!text && attached.length === 0) return;
      // Capture the per-turn tier at submit time so the reset below doesn't
      // race the in-flight network call.
      const tierForThisTurn = perTurnTier;
      setComposer("");
      setPendingAttachments([]);
      // Reset the override now — the chip returns to showing the user's
      // default for subsequent sends.
      setPerTurnTier(null);
      setState({ kind: "asking_planner" });

      // Build a body that includes inlined attachment names so the user
      // can see what they sent in the thread, even if the actual excerpt
      // is only sent to the planner via attached_sources.
      const bodyForUI =
        attached.length > 0
          ? `${text}${text ? "\n\n" : ""}+ ${attached
              .map((a) => a.display_name)
              .join(", ")}`
          : text;

      const tempId = `tmp-user-${Math.random().toString(36).slice(2, 10)}`;
      const optimistic: QnaTurn = {
        turn_id: tempId,
        topic_id: topic.topic_id,
        project_id: topic.project_id,
        role: "user",
        order_index: turns.length,
        body: bodyForUI,
        why_this_matters: null,
        action: null,
        suggested_responses: [],
        status: "answered",
        created_at: new Date().toISOString(),
      };
      setTurns((prev) => [...prev, optimistic]);

      try {
        // Phase 1 SSE streaming for user-submitted turns — same skeleton
        // UI as the auto-turn, with progressive heartbeats threaded into
        // the skeleton's status line so a 30-40s frontier-tier turn
        // visibly animates instead of looking hung.
        let envelope;
        try {
          envelope = await api.topicTurnStream(
            topic.topic_id,
            bodyForUI,
            attached,
            tierForThisTurn,
            {
              onHeartbeat: (data) => {
                const msg = data?.message?.trim();
                setState({
                  kind: "asking_planner",
                  thinking_message: msg || undefined,
                });
              },
            },
          );
        } catch (err) {
          if (
            err instanceof Error
            && /streaming_disabled|503/.test(err.message)
          ) {
            envelope = await api.topicTurn(
              topic.topic_id,
              bodyForUI,
              attached,
              tierForThisTurn,
            );
          } else {
            throw err;
          }
        }
        // Send succeeded — clear the persisted draft immediately so we
        // don't race the debounced write (which is already scheduled from
        // the setComposer("") above, but would only fire 400ms later).
        clearDraft(topic.topic_id);
        setDraftSavedVisible(false);
        setLatestPlannerResult(envelope.turn_result);
        // Backend now saves all proposed decisions during the turn and routes
        // rerouted ones to their correct topic. We only need to refetch the
        // local decision list (for non-rerouted ones) and fire toasts for
        // rerouted ones; we do NOT call api.createDecision here.
        handleProposedDecisions(envelope);
        if (envelope.checkpoints) {
          setCheckpoints(envelope.checkpoints);
        }
        // Handle suggest_close: the user picked "Close the topic →"
        if (
          envelope.turn_result.action === "suggest_close" &&
          bodyForUI.startsWith("Close the topic")
        ) {
          try {
            await api.closeTopic(topic.topic_id);
            toast.info(t("checkpoints.closed_toast"));
            window.dispatchEvent(new CustomEvent("inspira:topics-changed"));
            onClose();
            return;
          } catch {
            // Non-fatal — fall through and stay open
          }
        }
        const [freshTurns, freshDecisions] = await Promise.all([
          api.listTurns(topic.topic_id),
          api.listDecisions(topic.topic_id),
        ]);
        setTurns(freshTurns.turns);
        // Only update local decisions with non-rerouted ones (rerouted live on
        // other topics and the canvas will pick them up via the event).
        setDecisions(freshDecisions.decisions);
        setState({ kind: "ready" });
        // #080: re-fetch monthly usage so the dropdown indicator
        // reflects the new counter state after each successful turn.
        // Fire-and-forget; failures are non-fatal (chip just keeps
        // showing the previous percent).
        void api
          .getUsage()
          .then((view) => setUsageView(view))
          .catch(() => {
            /* non-fatal */
          });
      } catch (err) {
        console.error("[Inspira] topic turn submission failed", err);
        // #080: detect the monthly_cap_reached 429 and surface a
        // tier-specific message instead of the generic turn_failed
        // toast. The backend's ``api.v2_topic_turn`` returns a JSON
        // body ``{"error": "monthly_cap_reached", "tier": ...}``;
        // the error message string thrown by ``api.ts`` includes
        // that JSON, so a substring match is enough.
        const errStr = err instanceof Error ? err.message : String(err);
        const isCapReached = errStr.includes("monthly_cap_reached");
        setState({
          kind: "error",
          message: isCapReached
            ? t("errors.monthly_cap_reached")
            : t("errors.turn_failed"),
        });
        setTurns((prev) => prev.filter((t) => t.turn_id !== tempId));
        // Send failed — restore the text into the composer so the user
        // doesn't have to retype. The debounce effect will re-persist it
        // to localStorage on its own. (Attachments are intentionally not
        // restored: they'd have been consumed and may not round-trip.)
        if (text) {
          setComposer(text);
        }
        // Re-fetch usage so the dropdown indicator reflects the
        // exhausted-tier state after the user sees the message.
        if (isCapReached) {
          void api
            .getUsage()
            .then((view) => setUsageView(view))
            .catch(() => {
              /* non-fatal */
            });
        }
      }
    },
    [
      handleProposedDecisions,
      composer,
      pendingAttachments,
      perTurnTier,
      topic.project_id,
      topic.topic_id,
      turns.length,
      onClose,
    ],
  );

  // Delete a saved decision (soft delete on the backend).
  const deleteDecision = useCallback(async (decisionId: string) => {
    // Optimistic removal — re-add on failure.
    let removed: Decision | null = null;
    setDecisions((prev) => {
      const found = prev.find((d) => d.decision_id === decisionId);
      removed = found ?? null;
      return prev.filter((d) => d.decision_id !== decisionId);
    });
    try {
      await api.deleteDecision(decisionId);
    } catch (err) {
      console.error("[Inspira] failed to delete decision", err);
      if (removed) {
        const r = removed;
        setDecisions((prev) => [...prev, r]);
      }
    }
  }, []);

  // Completion-banner handlers — shown when all checkpoints are answered
  // AND the latest planner turn is a suggest_close. The user chooses:
  // "Next topic →" closes this topic and fires a parent event to open the
  // next unfinished sibling; "Keep asking" dismisses the banner for this
  // session and lets the Q&A continue.
  const handleCompletionNextTopic = useCallback(async () => {
    try {
      await api.closeTopic(topic.topic_id);
      toast.info(t("checkpoints.closed_toast"));
      window.dispatchEvent(new CustomEvent("inspira:topics-changed"));
      // Ask the parent to open the next unfinished sibling. Passes the
      // current topic's project_id so the parent can scope its sibling
      // lookup to the right project envelope.
      window.dispatchEvent(
        new CustomEvent("inspira:open-next-topic", {
          detail: {
            from_topic_id: topic.topic_id,
            project_id: topic.project_id,
          },
        }),
      );
      onClose();
    } catch (err) {
      console.error("[Inspira] failed to close topic from completion banner", err);
    }
  }, [onClose, topic.project_id, topic.topic_id]);

  const handleCompletionKeepAsking = useCallback(() => {
    setCompletionBannerDismissed(true);
  }, []);

  // Reset the session-dismiss flag when the user opens a different topic.
  useEffect(() => {
    setCompletionBannerDismissed(false);
  }, [topic.topic_id]);

  // File picker handler — read text-like files inline, attach binary
  // files by name only. Multiple files OK.
  const handleFilesPicked = useCallback(
    async (files: FileList | null) => {
      if (!files || files.length === 0) return;
      const next: AttachedSource[] = [];
      for (const f of Array.from(files)) {
        if (TEXT_LIKE_MIME_PATTERN.test(f.type)) {
          try {
            const text = await f.text();
            next.push({
              display_name: f.name,
              kind: f.type || "file:text",
              excerpt: text.slice(0, MAX_TEXT_EXCERPT_CHARS),
            });
          } catch (err) {
            console.warn("[Inspira] failed to read file", f.name, err);
          }
        } else {
          next.push({
            display_name: f.name,
            kind: f.type || "file:binary",
            excerpt: `(binary file, ${Math.round(f.size / 1024)} KB — content not inlined)`,
          });
        }
      }
      setPendingAttachments((prev) => [...prev, ...next]);
    },
    [],
  );

  // "Add link" — prompt for a URL, fetch it, and attach the result.
  // `fetchingUrl` disables the pill while the fetch is in flight. v1
  // uses window.prompt; a nicer inline popover is a follow-up.
  const [fetchingUrl, setFetchingUrl] = useState(false);
  const handleAddLink = useCallback(async () => {
    if (fetchingUrl) return;
    const raw = window.prompt(t("topic_detail.link_prompt"));
    if (raw === null) return;
    const trimmed = raw.trim();
    if (!trimmed) return;
    // Quick shape check so users get immediate feedback on typos.
    if (!/^https?:\/\//i.test(trimmed)) {
      window.alert(t("topic_detail.link_bad_url"));
      return;
    }
    setFetchingUrl(true);
    try {
      const source = await fetchUrlAsSource(trimmed);
      setPendingAttachments((prev) => [...prev, source]);
    } catch (err) {
      console.error("[Inspira] URL attachment failed", err);
    } finally {
      setFetchingUrl(false);
    }
  }, [fetchingUrl]);

  // Paste interceptor: big document-shaped pastes are offered as an
  // attachment instead of flooding the composer. Short pastes / single-
  // liners bypass the prompt entirely.
  const handleComposerPaste = useCallback(
    (e: React.ClipboardEvent<HTMLTextAreaElement>) => {
      const pasted = e.clipboardData.getData("text");
      if (!pasted) return;
      if (
        pasted.length < PASTE_AS_SOURCE_MIN_CHARS ||
        !pasted.includes("\n")
      ) {
        return; // normal paste into the composer
      }
      e.preventDefault();
      const ok = window.confirm(
        t("topic_detail.paste_confirm", { chars: String(pasted.length) }),
      );
      if (ok) {
        setPendingAttachments((prev) => [...prev, textAsSource(pasted)]);
        return;
      }
      // User declined — re-insert the pasted text manually at the caret.
      setComposer((prev) => prev + pasted);
    },
    [],
  );

  const removeAttachment = useCallback((idx: number) => {
    setPendingAttachments((prev) => prev.filter((_, i) => i !== idx));
  }, []);

  // Copy a Markdown rendering of this topic to the clipboard. Success and
  // failure both surface through an inline label swap on the button; no
  // modal or toast system yet. Resets after 1.5s so the button doesn't
  // stay stuck in a confirmation state.
  const copyMarkdown = useCallback(async () => {
    const md = topicToMarkdown(topic, turns, decisions);
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(md);
      } else {
        throw new Error("Clipboard API not available in this context.");
      }
      setCopyState("copied");
    } catch (err) {
      console.error("[Inspira] failed to copy markdown", err);
      setCopyState("error");
    }
    if (copyResetRef.current !== null) {
      window.clearTimeout(copyResetRef.current);
    }
    copyResetRef.current = window.setTimeout(() => {
      setCopyState("idle");
      copyResetRef.current = null;
    }, 1500);
  }, [decisions, topic, turns]);

  // Clear any pending copy-state reset on unmount — otherwise a late
  // timer fires into an unmounted component and React warns.
  useEffect(() => {
    return () => {
      if (copyResetRef.current !== null) {
        window.clearTimeout(copyResetRef.current);
        copyResetRef.current = null;
      }
    };
  }, []);

  // Debounced composer-draft persistence. Every change to `composer`
  // schedules a 400ms-delayed localStorage write; subsequent changes cancel
  // the pending write and restart the timer. When the write finally fires,
  // we also flash a small "Draft saved" pill for 1.5s.
  //
  // The first run on mount is a no-op — the state was just hydrated FROM
  // localStorage, so re-writing it would be both redundant and cause the
  // pill to appear on open for no reason.
  useEffect(() => {
    if (!draftHydratedRef.current) {
      draftHydratedRef.current = true;
      return;
    }
    if (draftWriteTimerRef.current !== null) {
      window.clearTimeout(draftWriteTimerRef.current);
    }
    draftWriteTimerRef.current = window.setTimeout(() => {
      writeDraft(topic.topic_id, composer);
      draftWriteTimerRef.current = null;
      // Only show the pill when there's meaningful content to save — not
      // when the user has just backspaced everything away.
      if (composer.trim().length > 0) {
        setDraftSavedVisible(true);
        if (draftPillTimerRef.current !== null) {
          window.clearTimeout(draftPillTimerRef.current);
        }
        draftPillTimerRef.current = window.setTimeout(() => {
          setDraftSavedVisible(false);
          draftPillTimerRef.current = null;
        }, DRAFT_SAVED_INDICATOR_MS);
      }
    }, DRAFT_DEBOUNCE_MS);

    return () => {
      if (draftWriteTimerRef.current !== null) {
        window.clearTimeout(draftWriteTimerRef.current);
        draftWriteTimerRef.current = null;
      }
    };
  }, [composer, topic.topic_id]);

  // Clean up the pill fade-out timer on unmount.
  useEffect(() => {
    return () => {
      if (draftPillTimerRef.current !== null) {
        window.clearTimeout(draftPillTimerRef.current);
        draftPillTimerRef.current = null;
      }
    };
  }, []);

  const titleId = useId();
  const statusText =
    topic.status === "empty"
      ? t("topic_detail.status.empty")
      : topic.status === "in_progress"
        ? t("topic_detail.status.in_progress")
        : t("topic_detail.status.fleshed_out");

  return (
    <CommentsProvider projectId={topic.project_id}>
      <div className="td-backdrop" onClick={requestClose} aria-hidden />
      <aside
        ref={drawerRef}
        className="td-drawer"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        onKeyDown={drawerKeyDown}
      >
        <CommentsLayer />
        <header className="topic-detail__header">
        <div className="topic-detail__title-row">
          <span className="topic-detail__icon" aria-hidden="true">
            {iconGlyph(topic.icon)}
          </span>
          <h1 id={titleId} className="topic-detail__title">
            {topic.title}
          </h1>
          <span
            className="topic-detail__status"
            data-status={topic.status}
            title={statusText}
            aria-label={t("topic_node.status_prefix", { status: statusText })}
            role="img"
          />
          <TopicSubAgentPulse themeId={themeId} />
        </div>
        {topic.origin === "planner_initial" ? (
          <p
            className="topic-detail__provenance"
            style={{
              margin: "4px 0 0",
              fontFamily: "var(--ff-sans)",
              fontSize: 11,
              color: "var(--ink-3)",
              letterSpacing: "0.04em",
              textTransform: "uppercase",
            }}
          >
            {t("topic_detail.provenance_ai", {
              date: new Date(topic.created_at).toLocaleDateString(undefined, {
                year: "numeric",
                month: "short",
                day: "numeric",
              }),
            })}
          </p>
        ) : null}
        <div className="topic-detail__header-actions">
          <TopicColorPicker value={color} onChange={handleColorChange} />
          <button
            type="button"
            className="topic-detail__copy"
            onClick={() => {
              void copyMarkdown();
            }}
            aria-label={t("topic_detail.copy_aria")}
            title={t("topic_detail.copy_aria")}
            data-state={copyState}
          >
            {copyState === "copied"
              ? t("topic_detail.copied")
              : copyState === "error"
                ? t("topic_detail.copy_failed")
                : t("topic_detail.copy_markdown")}
          </button>
          <button
            type="button"
            className="topic-detail__close"
            onClick={requestClose}
            aria-label={t("topic_detail.close_aria")}
          >
            <span aria-hidden="true">×</span>
          </button>
        </div>
      </header>

      {/* Spectate banner: shown when another user holds the Q&A focus
          lock on this topic. The composer + suggestion chips will be
          disabled further down based on the same flag so all answer
          actions are gated, not just this visual cue. */}
      {isLockedByOther && lock ? (
        <div
          className="topic-detail__spectate-banner"
          style={{
            padding: "10px 16px",
            margin: "0 0 8px",
            borderLeft: `3px solid ${lock.ownerColor || "var(--sage)"}`,
            background: "color-mix(in srgb, var(--paper-2) 70%, transparent)",
            color: "var(--ink-2)",
            fontFamily: "var(--ff-serif)",
            fontStyle: "italic",
            fontSize: 13,
            display: "flex",
            alignItems: "center",
            gap: 10,
          }}
          role="status"
        >
          <span
            aria-hidden="true"
            style={{
              display: "inline-block",
              width: 10,
              height: 10,
              borderRadius: "50%",
              background: lock.ownerColor || "var(--sage)",
            }}
          />
          <span>
            <strong style={{ fontStyle: "normal" }}>
              {lock.ownerDisplayName || "Someone"}
            </strong>{" "}
            is answering — you're spectating. Replies + decisions are
            disabled until they close this topic.
          </span>
        </div>
      ) : null}

      {/* Contradiction modal: fires once per LLM-detected clash.
          Dismissing (either resolution OR close) clears the event
          from the realtime store. */}
      {realtime.contradictionEvent ? (
        <ContradictionDialog
          event={realtime.contradictionEvent}
          onResolved={realtime.clearContradiction}
        />
      ) : null}

        {/* Drawer body — single scroll container per v5 design. Thread,
            decisions strip, and conflict flags all live inside this body
            in one continuous flow; the composer below is pinned to the
            drawer floor outside the scroll region. */}
        <div className="td-body" ref={threadRef}>
          {state.kind === "initializing" ? (
            <div
              className="topic-detail__thread-skeleton"
              aria-label={t("topic_detail.thread_opening_aria")}
              aria-busy="true"
            >
              {/* Planner question (tall), user reply (short, right-aligned),
                  planner follow-up (medium) — mirrors the rhythm of a real
                  thread so the viewport doesn't jump when turns arrive. */}
              <SkeletonCard height={140} />
              <SkeletonCard
                height={80}
                className="topic-detail__thread-skeleton-user"
              />
              <SkeletonCard height={110} />
            </div>
          ) : state.kind === "error" ? (
            <div className="topic-detail__error">
              <strong>{t("errors.generic")}</strong>
              <p>{state.message}</p>
            </div>
          ) : (
            <div className="topic-detail__thread">
              {/* W2 η — additive layer above the existing Q&A thread.
                  Reasoning expander is always rendered (collapsed by
                  default); the live sub-agent stream is only rendered
                  while we have live decisions accumulating for this
                  topic. Both nest INSIDE the thread column without
                  touching the surrounding 3-column grid. */}
              <ReasoningTrace
                topic={topic}
                decisions={decisions}
                provenanceByDecisionId={provenanceByDecisionId}
                isOpen={reasoningOpen}
                onToggle={() => setReasoningOpen((v) => !v)}
                onLoadProvenance={
                  projectIdForReasoning ? loadProvenanceFromRest : null
                }
              />
              <SubAgentStream
                events={liveEvents}
                isActive={liveEvents.length > 0}
                isOpen={streamOpen}
                onToggle={() => setStreamOpen((v) => !v)}
              />
              {/* P1.9 — Completion is now a centered modal dialog that
                  fires on the same trigger (all checkpoints answered +
                  last planner turn is suggest_close). The dialog mount
                  is at the bottom of this component's JSX so the modal
                  layers above the drawer. The inline banner that used
                  to live here scrolled out of view in long threads;
                  the modal fixes that and matches the warm Dialog
                  language used everywhere else. */}
              {turns.length === 0 ? (
                <div>
                  <p className="topic-detail__empty">{t("topic_detail.thread_start")}</p>
                  <p className="topic-detail__empty-hint">
                    {t("topic_detail.thread_start_hint")}
                  </p>
                </div>
              ) : (
                turns.map((turn, idx) => {
                  // For each planner turn, look at the next turn — if
                  // it's a user turn whose body exactly matches one of
                  // the suggestions, that's the selection. We use this
                  // to lock the suggestions and highlight the chosen one
                  // so the user can't accidentally double-answer.
                  let selectedSuggestion: string | null = null;
                  if (turn.role === "planner") {
                    const next = turns[idx + 1];
                    if (next && next.role === "user") {
                      const match = turn.suggested_responses.find(
                        (s) => s.label === next.body,
                      );
                      if (match) selectedSuggestion = match.label;
                    }
                  }
                  // Lock if there's already a follow-up user turn after
                  // this planner turn — even if the user typed a custom
                  // answer rather than tapping a suggestion. The
                  // question has been answered; suggestions become
                  // archival.
                  const locked =
                    turn.role === "planner" &&
                    !!turns[idx + 1] &&
                    turns[idx + 1].role === "user";
                  // Pass conflict_resolution only to the last planner turn
                  // when action === "resolve_conflict", so the UI treatment
                  // shows exactly once (on the current open question).
                  const isLastPlannerTurn =
                    turn.role === "planner" &&
                    idx === turns.map((t) => t.role).lastIndexOf("planner");
                  const conflictResolution =
                    isLastPlannerTurn &&
                    latestPlannerResult?.action === "resolve_conflict"
                      ? (latestPlannerResult.conflict_resolution ?? null)
                      : null;
                  return (
                    <TurnCard
                      key={turn.turn_id}
                      turn={turn}
                      locked={locked}
                      selectedSuggestion={selectedSuggestion}
                      conflictResolution={conflictResolution}
                      onSuggestionTap={(label) => {
                        // Auto-send: tapping a suggestion fires it as the
                        // user's answer immediately, no separate Send
                        // click. Pass the label explicitly so we don't
                        // race the composer state update.
                        void submitAnswer(label, []);
                      }}
                    />
                  );
                })
              )}

              {/* Cross-topic conflict flags (from the most recent turn).
                  Proposed decisions are auto-saved into the Decisions
                  column; the user can delete them from there if wrong. */}
              {latestPlannerResult
                ? latestPlannerResult.consistency_flags.map((f) => (
                    <div
                      key={`flag-${f.other_decision_id}-${f.other_topic_title}`}
                      className="topic-detail__flag"
                      role="alert"
                    >
                      <div className="topic-detail__flag-eyebrow">
                        <span aria-hidden="true">⚑ </span>
                        {t("topic_detail.conflict_eyebrow")}
                      </div>
                      <div className="topic-detail__flag-body">
                        {t("topic_detail.conflict_body", { topic: f.other_topic_title, description: f.description })}
                      </div>
                    </div>
                  ))
                : null}

              {state.kind === "asking_planner" ? (
                // Content-shape placeholder for the planner's pending
                // turn. The status line under the bubble uses the live
                // heartbeat message from the SSE stream when available
                // (e.g. "Weighing options…", "Drafting a response…") and
                // falls back to the static "Planner is thinking…" until
                // the first heartbeat arrives. Announced via
                // role="status" on the wrapper.
                <TurnSkeleton
                  align="planner"
                  status={
                    state.thinking_message || t("topic_detail.thinking")
                  }
                  className="topic-detail__thinking-skeleton"
                />
              ) : null}
            </div>
          )}

          {/* Decisions strip — lifted from former col--decisions, pinned
              to the bottom of the drawer body per v5 design. Skeleton
              during init, hidden empty-state copy when no decisions
              exist yet, otherwise the running list. */}
          <div className="td-decisions">
            <div className="td-decisions__label">
              {t("topic_detail.decisions_heading")}
              {state.kind !== "initializing" && decisions.length > 0
                ? ` · ${decisions.length}`
                : ""}
            </div>
            {state.kind === "initializing" ? (
              <SkeletonColumn lines={["80%", "90%", "60%"]} />
            ) : decisions.length === 0 ? (
              <p className="topic-detail__empty">
                {t("topic_detail.decisions_empty")}
              </p>
            ) : (
              <ul
                id="topic-detail-decisions-list"
                className="topic-detail__decisions"
              >
                {decisions.map((d) => (
                  <DecisionRow
                    key={d.decision_id}
                    decision={d}
                    onDelete={() => deleteDecision(d.decision_id)}
                  />
                ))}
              </ul>
            )}
          </div>
        </div>

        {/* Composer — pinned to the drawer floor outside the scrolling
            body (flex-shrink:0 below the flex:1 .td-body above). */}
        <form
          className="topic-detail__composer"
          onSubmit={(e) => {
            e.preventDefault();
            void submitAnswer();
          }}
        >
            {pendingAttachments.length > 0 ? (
              <div className="topic-detail__composer-attachments">
                {pendingAttachments.map((a, i) => (
                  <span
                    key={`${a.display_name}-${i}`}
                    className="topic-detail__attachment-chip"
                  >
                    <span aria-hidden="true">+ </span>
                    {a.display_name}
                    <button
                      type="button"
                      onClick={() => removeAttachment(i)}
                      className="topic-detail__attachment-remove"
                      aria-label={t("topic_detail.remove_attachment_aria", { name: a.display_name })}
                    >
                      <span aria-hidden="true">×</span>
                    </button>
                  </span>
                ))}
              </div>
            ) : null}
            <label
              htmlFor="topic-detail-composer-input"
              className="visually-hidden"
            >
              {t("topic_detail.composer_label")}
            </label>
            <input
              ref={fileInputRef}
              type="file"
              multiple
              hidden
              onChange={(e) => {
                void handleFilesPicked(e.target.files);
                // Reset so picking the same file twice still triggers.
                e.currentTarget.value = "";
              }}
              aria-label={t("topic_detail.attach_files_aria")}
            />
            <ComposerShell
              variant="topic-detail"
              value={composer}
              disabled={state.kind === "asking_planner"}
              addLinkDisabled={fetchingUrl}
              addLinkLabel={
                fetchingUrl
                  ? t("topic_detail.fetching_link")
                  : t("topic_detail.add_link_label")
              }
              attachAriaLabel={t("topic_detail.add_file_aria")}
              linkAriaLabel={t("topic_detail.add_link_aria")}
              onOpenFilePicker={() => fileInputRef.current?.click()}
              onAddLink={() => {
                void handleAddLink();
              }}
              inputSlot={
                <textarea
                  id="topic-detail-composer-input"
                  className="topic-detail__composer-input composer-shell__input"
                  value={composer}
                  onChange={(e) => setComposer(e.target.value)}
                  onPaste={handleComposerPaste}
                  // B2 (#076) — Enter (without Shift) submits; Shift+Enter
                  // inserts a newline. The textarea conversion (from
                  // <input>) lets long replies actually WRAP to multiple
                  // lines so the pill grows downward via
                  // `field-sizing: content` instead of overflowing
                  // horizontally past the pill's right edge.
                  onKeyDown={(e) => {
                    if (
                      e.key === "Enter" &&
                      !e.shiftKey &&
                      !e.nativeEvent.isComposing
                    ) {
                      e.preventDefault();
                      // Find the enclosing form + dispatch submit
                      // (mirrors the canvas composer's belt-and-braces
                      // pattern). Disabled state is checked inside the
                      // form's onSubmit, so a no-op call is safe.
                      const form = e.currentTarget.closest("form");
                      form?.requestSubmit();
                    }
                  }}
                  rows={1}
                  placeholder={t("topic_detail.composer_placeholder")}
                  disabled={state.kind === "asking_planner"}
                  aria-label={t("topic_detail.composer_label")}
                />
              }
              trailingSlot={
                <>
                  <ModelTierChip
                    catalog={modelTierCatalog}
                    value={perTurnTier}
                    onChange={setPerTurnTier}
                    onRequestUpgrade={() => {
                      // HIDE_UPGRADE: belt-and-suspenders. ModelTierChip's
                      // disabled-row click handler also short-circuits, but
                      // gating here prevents any future caller from opening
                      // the upgrade Dialog on a Stripe-dark deploy.
                      if (HIDE_UPGRADE) return;
                      setUpgradeDialogOpen(true);
                    }}
                    disabled={state.kind === "asking_planner"}
                    usage={usageView}
                    // Persist the current pick as the user's default so
                    // they don't re-pick before every turn. PATCHes
                    // /auth/me/preferred-model-tier; the reload of
                    // current_default happens optimistically on success.
                    onSetDefault={(tier) => {
                      if (settingDefaultTier) return;
                      setSettingDefaultTier(true);
                      void api
                        .setPreferredModelTier(tier)
                        .then(() => {
                          setModelTierCatalog((prev) =>
                            prev
                              ? { ...prev, current_default: tier }
                              : prev,
                          );
                          // Now that the default IS this tier, clearing
                          // the per-turn override produces the same
                          // effective selection — keeps the chip label
                          // accurate without a refetch.
                          setPerTurnTier(null);
                        })
                        .catch((err) => {
                          console.warn(
                            "[Inspira] setPreferredModelTier failed",
                            err,
                          );
                        })
                        .finally(() => {
                          setSettingDefaultTier(false);
                        });
                    }}
                    settingDefault={settingDefaultTier}
                  />
                  {/* BYOK / house-key pill. Muted ink, no emphasis.
                      Shown when the backend has resolved which key this
                      turn would use — `llmMode` is "byok" when the
                      current user has an active BYOK config for the
                      active tier's provider, "house" otherwise. */}
                  {llmMode ? (
                    <span
                      className="topic-detail__llm-mode"
                      aria-label={
                        llmMode === "byok"
                          ? t("byok.mode_badge_your_key_aria")
                          : t("byok.mode_badge_house_key_aria")
                      }
                      title={
                        llmMode === "byok"
                          ? t("byok.mode_badge_your_key_title")
                          : t("byok.mode_badge_house_key_title")
                      }
                    >
                      {llmMode === "byok"
                        ? t("byok.mode_badge_your_key")
                        : t("byok.mode_badge_house_key")}
                    </span>
                  ) : null}
                </>
              }
              sendSlot={
                <button
                  type="submit"
                  className="topic-detail__composer-send composer-shell__send"
                  disabled={
                    (!composer.trim() && pendingAttachments.length === 0) ||
                    state.kind === "asking_planner"
                  }
                  aria-label={t("topic_detail.send_aria")}
                >
                  {/* T1.5: spinner glyph while the planner is replying so
                      the user sees the button working, not just disabled. */}
                  <span aria-hidden="true">
                    {state.kind === "asking_planner" ? "⋯" : "↑"}
                  </span>
                  {state.kind === "asking_planner" ? (
                    <span className="visually-hidden">
                      {t("composer.sending_status")}
                    </span>
                  ) : null}
                </button>
              }
            />
            {/* Transient "Draft saved" pill — visible for 1.5s after a
                debounced autosave fires, then fades. Uses aria-live="polite"
                so screen readers announce it once without interrupting. */}
            <div
              className="topic-detail__draft-saved"
              role="status"
              aria-live="polite"
              style={draftSavedPillStyle(draftSavedVisible)}
            >
              {t("topic_detail.draft_saved")}
            </div>
          </form>

          {/* B3 (#077) — Private notes panel removed by founder request.
              Backend column on `topics` is preserved (prior user data
              still on disk). The FE Topic type still includes the
              field but nothing renders it. */}
      </aside>

      {!HIDE_UPGRADE && (
        <Dialog
          open={upgradeDialogOpen}
          onClose={() => setUpgradeDialogOpen(false)}
          title={t("model_tier.upgrade_dialog_title")}
          width={420}
          primaryAction={{
            label: t("model_tier.upgrade_dialog_close"),
            onClick: () => setUpgradeDialogOpen(false),
          }}
        >
          <p
            style={{
              fontFamily: "var(--ff-serif)",
              fontSize: 14,
              lineHeight: 1.6,
              color: "var(--ink-2, #423a2d)",
              margin: 0,
            }}
          >
            {t("model_tier.upgrade_dialog_body")}
          </p>
        </Dialog>
      )}

      {/* First-topic-open coachmark — fires once per user (see the
          TOPIC_DETAIL_COACH_STORAGE_KEY flag + activation gate above).
          Coachmark itself portals into document.body, so rendering it
          here keeps lifecycle scoped to this component without creating
          a visual-nesting issue. */}
      <Coachmark
        active={topicDetailCoachActive}
        storageKey={TOPIC_DETAIL_COACH_STORAGE_KEY}
        steps={TOPIC_DETAIL_COACH_STEPS}
        onDone={() => setTopicDetailCoachActive(false)}
      />
      {/* P1.9 — Topic-completion dialog. Fires on the same trigger as
          the old inline banner (all checkpoints answered + last planner
          turn is suggest_close), but as a centered modal. Suppression
          rules: the session-level `completionBannerDismissed` flag
          covers "Keep asking" (re-fires next time the topic re-saturates),
          and `isCompletionSuppressed(topic.topic_id)` is the persistent
          opt-out for "Don't show again on this topic" (survives reload). */}
      <TopicCompletionDialog
        open={
          checkpoints.length > 0 &&
          checkpoints.every((c) => c.status === "answered") &&
          latestPlannerResult?.action === "suggest_close" &&
          !completionBannerDismissed &&
          !isCompletionSuppressed(topic.topic_id)
        }
        topicTitle={topic.title}
        topicId={topic.topic_id}
        onNextTopic={() => {
          void handleCompletionNextTopic();
        }}
        onKeepAsking={handleCompletionKeepAsking}
        onClose={handleCompletionKeepAsking}
      />
    </CommentsProvider>
  );
}

// W2-θ — single decision row inside the topic-detail decisions column.
// Extracted so we can call ``useVersionAge`` (a hook) per row.
function DecisionRow({
  decision,
  onDelete,
}: {
  decision: Decision;
  onDelete: () => void;
}): React.JSX.Element {
  const age = useVersionAge(decision.decision_id);
  return (
    <li className="topic-detail__decision">
      <button
        type="button"
        className="topic-detail__decision-delete"
        onClick={onDelete}
        aria-label={t("topic_detail.delete_decision_aria", { statement: decision.statement })}
        title={t("topic_detail.delete_decision_title")}
      >
        <span aria-hidden="true">×</span>
      </button>
      <CommentChip target={{ kind: "decision", id: decision.decision_id }} />
      <CommentTargetWrapper
        kind="decision"
        id={decision.decision_id}
        as="div"
        className="topic-detail__decision-body"
      >
        {decision.statement}
      </CommentTargetWrapper>
      {decision.rationale ? (
        <div className="topic-detail__decision-rationale">
          {decision.rationale}
        </div>
      ) : null}
      {age ? (
        <DiffBadge age={age.age} lastChangedAt={age.lastChangedAt} />
      ) : null}
    </li>
  );
}

// ----- Checkpoint progress panel -----

// Checkpoint progress styles. Colors route through CSS variables so the
// panel flips with the theme (light cream ↔ dark espresso). The sage
// accent for "answered" is pulled from --sage; dim/partial states use the
// mid-ink ramp. Hardcoded hex fallbacks keep the first paint sensible
// even before vars resolve.
const cpStyles = {
  panel: {
    marginBottom: "1.25rem",
    padding: "0.85rem 0.9rem",
    border: "1px solid var(--paper-edge)",
    borderRadius: "6px",
    background: "var(--paper-lifted, #fbf7ee)",
  } as React.CSSProperties,
  headerRow: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    marginBottom: "0.55rem",
  } as React.CSSProperties,
  heading: {
    fontSize: "0.8rem",
    fontWeight: 600,
    color: "var(--sage, #6A9A7A)",
    letterSpacing: "0.02em",
    textTransform: "uppercase" as const,
  } as React.CSSProperties,
  closerChip: {
    fontSize: "0.7rem",
    fontWeight: 600,
    padding: "0.1rem 0.5rem",
    borderRadius: "999px",
    background: "color-mix(in srgb, var(--sage, #6A9A7A) 22%, transparent)",
    color: "var(--sage, #6A9A7A)",
  } as React.CSSProperties,
  barTrack: {
    height: "3px",
    background: "color-mix(in srgb, var(--sage, #6A9A7A) 22%, transparent)",
    borderRadius: "3px",
    marginBottom: "0.65rem",
    overflow: "hidden" as const,
  } as React.CSSProperties,
  barFill: (pct: number): React.CSSProperties => ({
    height: "100%",
    width: `${pct}%`,
    background: "var(--sage, #6A9A7A)",
    borderRadius: "3px",
    transition: "width 400ms ease",
  }),
  list: {
    listStyle: "none",
    margin: 0,
    padding: 0,
    display: "flex",
    flexDirection: "column" as const,
    gap: "0.35rem",
  } as React.CSSProperties,
  item: {
    display: "flex",
    alignItems: "center",
    gap: "0.45rem",
    fontSize: "0.8rem",
    color: "var(--ink-2, #4A413A)",
  } as React.CSSProperties,
  dotBase: {
    width: "10px",
    height: "10px",
    borderRadius: "50%",
    flexShrink: 0,
  } as React.CSSProperties,
} as const;

function cpDotStyle(status: Checkpoint["status"]): React.CSSProperties {
  if (status === "answered") {
    return { ...cpStyles.dotBase, background: "var(--sage, #6A9A7A)", border: "2px solid var(--sage, #6A9A7A)" };
  }
  if (status === "partial") {
    return {
      ...cpStyles.dotBase,
      background: "linear-gradient(to right, var(--sage, #6A9A7A) 50%, transparent 50%)",
      border: "2px solid var(--sage, #6A9A7A)",
    };
  }
  return { ...cpStyles.dotBase, background: "transparent", border: "2px solid var(--ink-4, #9c8f82)" };
}

function CheckpointProgress({ checkpoints }: { checkpoints: Checkpoint[] }) {
  const answeredCount = checkpoints.filter((c) => c.status === "answered").length;
  const total = checkpoints.length;
  const pct = total > 0 ? Math.round((answeredCount / total) * 100) : 0;
  const showCloserChip = answeredCount >= 2;

  const headingText = t("checkpoints.heading", {
    answered: String(answeredCount),
    total: String(total),
  });

  return (
    <div
      className="cp-progress-panel"
      style={cpStyles.panel}
      role="region"
      aria-label={headingText}
    >
      <div style={cpStyles.headerRow}>
        <span style={cpStyles.heading}>{headingText}</span>
        {showCloserChip ? (
          <span style={cpStyles.closerChip}>{t("checkpoints.closer_chip")}</span>
        ) : null}
      </div>
      <div style={cpStyles.barTrack} aria-hidden="true">
        <div style={cpStyles.barFill(pct)} />
      </div>
      <ul style={cpStyles.list}>
        {checkpoints.map((cp) => (
          <li key={cp.id} style={cpStyles.item} title={`${cp.id} — ${t(`checkpoints.status_${cp.status}`)}`}>
            <span
              style={cpDotStyle(cp.status)}
              role="img"
              aria-label={t(`checkpoints.status_${cp.status}`)}
            />
            {cp.status === "answered" ? (
              <span style={{ color: "var(--sage, #6A9A7A)", fontWeight: 500 }}>{cp.question}</span>
            ) : (
              <span style={{ color: cp.status === "partial" ? "var(--ink-2, #4A413A)" : "var(--ink-4, #9c8f82)" }}>{cp.question}</span>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

// (P1.9) — The inline `CompletionBanner` + its `completionStyles` were
// removed when the saturation surface moved into the warm modal
// `<TopicCompletionDialog>` (see app/src/components/dialogs/). The
// trigger conditions and the two-CTA shape (Next topic / Keep asking)
// carry over; the dialog adds a tertiary "Don't show again on this
// topic" text-link backed by a localStorage flag. Old i18n keys
// (`topic_detail.completion.banner_eyebrow`, `.banner_lede`,
// `.next_topic_cta`, `.keep_asking_cta`) are now unused but kept in
// the locale JSON files for back-compat — drop them in a follow-up
// once we've confirmed nothing else references them.

// ----- Turn card -----

// Inline styles for the conflict-resolution treatment. These are kept here
// (not in App.css) to avoid edit collisions with the theme-picker agent
// that may currently be modifying App.css.
const conflictStyles = {
  eyebrowPill: {
    display: "inline-block",
    marginBottom: "0.5rem",
    padding: "0.15rem 0.55rem",
    borderRadius: "999px",
    fontSize: "0.72rem",
    fontWeight: 600,
    letterSpacing: "0.03em",
    background: "#fdf0e8",
    color: "#b5451b",
    border: "1px solid #e8b49a",
  } as React.CSSProperties,
  quoteBlock: {
    marginTop: "0.75rem",
    display: "flex",
    flexDirection: "column" as const,
    gap: "0.4rem",
  } as React.CSSProperties,
  quoteRow: {
    display: "flex",
    gap: "0.5rem",
    alignItems: "baseline",
    fontSize: "0.85rem",
  } as React.CSSProperties,
  quoteLabel: {
    flexShrink: 0,
    fontWeight: 600,
    color: "#888",
    minWidth: "3.5rem",
    fontSize: "0.78rem",
    textTransform: "uppercase" as const,
    letterSpacing: "0.04em",
  } as React.CSSProperties,
  quoteText: {
    fontStyle: "italic",
    color: "#444",
  } as React.CSSProperties,
  resolutionSuggestion: {
    outline: "1.5px solid #7aad8f",
    outlineOffset: "1px",
  } as React.CSSProperties,
} as const;

function TurnCard({
  turn,
  onSuggestionTap,
  locked,
  selectedSuggestion,
  conflictResolution,
}: {
  turn: QnaTurn;
  onSuggestionTap: (label: string) => void;
  locked: boolean;
  selectedSuggestion: string | null;
  conflictResolution: ConflictResolution | null;
}) {
  if (turn.role === "user") {
    return (
      <article className="turn turn--user">
        <div className="turn__author">{t("topic_detail.turn_author_user")}</div>
        <div className="turn__body">{turn.body}</div>
      </article>
    );
  }

  const isConflict = !!conflictResolution;

  return (
    <article className="turn turn--planner">
      <div className="turn__author">{t("topic_detail.turn_author_planner")}</div>

      {isConflict ? (
        <div style={conflictStyles.eyebrowPill} role="img" aria-label={t("topic_detail.conflict_eyebrow")}>
          {t("topic_detail.conflict_eyebrow")}
        </div>
      ) : null}

      <h3 className="turn__question">{turn.body}</h3>

      {isConflict && conflictResolution ? (
        <div style={conflictStyles.quoteBlock}>
          <div style={conflictStyles.quoteRow}>
            <span style={conflictStyles.quoteLabel}>
              {t("topic_detail.conflict_earlier_label")}:
            </span>
            <span style={conflictStyles.quoteText}>
              &ldquo;{conflictResolution.previous_statement_summary}&rdquo;
            </span>
          </div>
          <div style={conflictStyles.quoteRow}>
            <span style={conflictStyles.quoteLabel}>
              {t("topic_detail.conflict_now_label")}:
            </span>
            <span style={conflictStyles.quoteText}>
              &ldquo;{conflictResolution.current_statement_summary}&rdquo;
            </span>
          </div>
        </div>
      ) : null}

      {!isConflict && turn.why_this_matters ? (
        <div className="turn__why">
          <span className="turn__why-label">{t("topic_detail.turn_why_label")}</span>
          <span>{turn.why_this_matters}</span>
        </div>
      ) : null}

      {turn.suggested_responses.length > 0 ? (
        <div className="turn__suggestions">
          {turn.suggested_responses.slice(0, 3).map((s, i) => {
            const isSelected = s.label === selectedSuggestion;
            return (
              <button
                key={i}
                type="button"
                className={
                  "turn__suggestion" +
                  (isSelected ? " turn__suggestion--selected" : "") +
                  (locked && !isSelected ? " turn__suggestion--dimmed" : "")
                }
                style={isConflict && !locked ? conflictStyles.resolutionSuggestion : undefined}
                onClick={() => onSuggestionTap(s.label)}
                data-n={String(i + 1).padStart(2, "0")}
                disabled={locked}
                aria-pressed={isSelected}
              >
                {s.label}
              </button>
            );
          })}
          {!locked ? (
            <div className="turn__suggestion-hint">
              {t("topic_detail.suggestion_hint")}
            </div>
          ) : null}
        </div>
      ) : null}
    </article>
  );
}

// (B3 / #077) The PrivateNotesPanel component + its `privateStyles`
// object + the `SaveState` type + the AUTOSAVE_DEBOUNCE_MS /
// SAVED_FLASH_MS constants were removed at the founder's request.
// The backend `private_notes` column on `topics` is preserved so
// prior user data stays on disk; the FE Topic type still includes
// the field but nothing renders it. See `api.ts` for the (also
// removed) `updateTopicPrivateNotes` method. If the panel is
// restored later, the original implementation lives in git history.

function iconGlyph(name: string): string {
  const map: Record<string, string> = {
    lightbulb: "○",
    feather: "✎",
    book: "□",
    compass: "⟐",
    "map-pin": "◉",
    clock: "◐",
    flag: "⚐",
    heart: "♥",
    chart: "▦",
    megaphone: "⏵",
    camera: "◇",
    leaf: "✿",
  };
  return map[name] ?? "•";
}
