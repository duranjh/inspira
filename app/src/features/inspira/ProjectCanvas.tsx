// The populated canvas — topic cards laid out spatially, relationships as
// dotted lines. Powered by React Flow; styled to match the warm editorial
// aesthetic from the HTML mocks.
//
// Interaction:
// - Drag topic cards to rearrange. Positions persist to the backend on drop.
// - Hover a card to see connection handles on left/right edges.
// - Drag from one handle to another to create a new relationship.
// - Click an edge to select, press Delete/Backspace to remove.
// - Double-click an edge to edit its label.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { CanvasErrorBoundary } from "../../components/CanvasErrorBoundary";
import { Coachmark, type CoachmarkStep } from "../../components/Coachmark";
import ReactFlow, {
  addEdge,
  Background,
  BackgroundVariant,
  type Connection,
  Controls,
  type Edge,
  type EdgeChange,
  MarkerType,
  MiniMap,
  type Node,
  type NodeChange,
  type NodePositionChange,
  type OnSelectionChangeParams,
  useEdgesState,
  useNodesState,
  useReactFlow,
  ReactFlowProvider,
} from "reactflow";
import type { NodeRemoveChange } from "reactflow";

import "reactflow/dist/style.css";
import "./chrome/chrome.css";

import { RelationshipLabelDialog } from "../../components/dialogs";
import { toast } from "../../components/ToastProvider";
import { t } from "../../i18n";
import { safeStorage } from "../../lib/safeStorage";
import { useSSE } from "../../hooks/useSSE";
import { ActivityTimeline } from "./ActivityTimeline";
import { ConflictBanner } from "./chrome/ConflictBanner";
import { DraftingBanner } from "./chrome/DraftingBanner";
import { TopicCardSkeleton } from "./chrome/TopicCardSkeleton";
import { ComposerShell } from "./ComposerShell";
import {
  RelationshipEdge,
  type RelationshipEdgeData,
} from "./RelationshipEdge";
import { TopicNode, type TopicNodeData } from "./TopicNode";
import { CommentsProvider, CommentsLayer } from "./comments";
import type {
  Decision,
  KickoffRawResponse,
  Relationship,
  Topic,
  TopicDeletionSuggestion,
} from "./api";
import { api, DEFAULT_BASE_URL } from "./api";
import { useRealtime } from "./realtime";
import { RealtimeProvider, useRealtimeContext } from "./RealtimeContext";
import { RemoteCursors } from "./RemoteCursors";
import { PresenceAvatars } from "./PresenceAvatars";
import {
  computeTopicLayout,
  ensureNoOverlaps,
  resolveOverlap,
} from "./layout";

// Estimated topic card dimensions — must match TopicNode's typical render.
// Used by collision resolution to know how much space a dropped card claims.
const TOPIC_CARD_WIDTH = 280;
const TOPIC_CARD_HEIGHT = 180;

const nodeTypes = { topic: TopicNode };
// L5d (#037) — the dotted relationship edges run through a custom
// component so the Edit/Delete toolbar can render at the edge midpoint
// when an edge is selected. The component preserves the existing
// smoothstep + dotted look — non-selected edges are visually identical.
const edgeTypes = { relationship: RelationshipEdge };

// ---- Canvas coachmark steps ------------------------------------------------
// Selectors reference real DOM class names used in the app. Any step whose
// selector doesn't resolve on a given render is silently skipped by Coachmark.

// One-step shortcut-discovery flow fires on a separate storage key so
// users who've already dismissed CANVAS_STEPS still see this fresh.
// Targets the Projects pill (always visible in the top bar) and teaches
// the Cmd+K palette — the single highest-leverage shortcut.
const SHORTCUTS_STEPS: CoachmarkStep[] = [
  {
    id: "shortcuts-cmd-k",
    targetSelector: ".top-bar__projects-pill",
    title: t("shortcuts_coach.1.title"),
    body: t("shortcuts_coach.1.body"),
    placement: "bottom",
  },
];

const CANVAS_STEPS: CoachmarkStep[] = [
  {
    id: "canvas-topic",
    targetSelector: ".topic-node",
    title: t("canvas_onboard.1.title"),
    body: t("canvas_onboard.1.body"),
    placement: "right",
  },
  {
    id: "canvas-composer",
    targetSelector: ".canvas-composer",
    title: t("canvas_onboard.2.title"),
    body: t("canvas_onboard.2.body"),
    placement: "top",
  },
  {
    id: "canvas-actions-summary",
    // Summary button — fourth in the row now (Tidy, Fit, Activity, Summary).
    // When Activity landed we shifted the index by one; keep the selector
    // in sync so the spotlight still lands on Summary.
    targetSelector: ".canvas-actions__btn:nth-child(4)",
    title: t("canvas_onboard.3.title"),
    body: t("canvas_onboard.3.body"),
    placement: "bottom",
  },
  {
    id: "canvas-actions-tidy",
    // First .canvas-actions__btn is the Tidy button.
    targetSelector: ".canvas-actions__btn:first-child",
    title: t("canvas_onboard.4.title"),
    body: t("canvas_onboard.4.body"),
    placement: "bottom",
  },
  {
    id: "canvas-topbar-projects",
    targetSelector: ".top-bar__projects-pill",
    title: t("canvas_onboard.5.title"),
    body: t("canvas_onboard.5.body"),
    placement: "bottom",
  },
];

export type ProjectCanvasProps = {
  projectId: string;
  topics: Topic[];
  relationships: Relationship[];
  // Optional — when provided, each topic's accepted decisions render as
  // bullets on its card. The map is keyed by topic_id.
  decisionsByTopicId?: Map<string, Decision[]>;
  kickoff?: KickoffRawResponse | null;
  // Receives the card's current viewport rect so the detail view can
  // morph open from this exact position.
  onOpenTopic?: (topicId: string, originRect: DOMRect) => void;
  // When set, the matching topic node is rendered invisibly so it doesn't
  // double-render behind the morphing detail view. Cleared after close.
  hiddenTopicId?: string | null;
  // Called when the parent should re-fetch authoritative state (e.g. after
  // a new relationship was persisted). Optional — component is otherwise
  // self-healing via optimistic updates.
  onRefetch?: () => void;
  // Planner-suggested deletions keyed by topic_id. When set, the matching
  // TopicNode renders a banner asking the user to confirm or dismiss.
  pendingDeletionSuggestions?: Record<string, TopicDeletionSuggestion>;
  onDismissDeletionSuggestion?: (topicId: string) => void;
  onConfirmDeletion?: (topicId: string) => void;
};

export function ProjectCanvas(props: ProjectCanvasProps) {
  // Fire the canvas coachmark the first time a user visits a canvas.
  // Wait for BOTH (a) a real ``.topic-node`` to exist in the DOM so the
  // first coach step has a spotlight target, AND (b) at least 300ms of
  // settling so the node finishes its reveal animation. Without the
  // target-presence check, the coachmark used to render behind an
  // un-laid-out ReactFlow viewport on fresh kickoffs and auto-skip
  // every step (the user's 2026-04-23 "highlights didn't go off"
  // report). We poll lightly instead of relying on a static delay.
  const [canvasCoachActive, setCanvasCoachActive] = useState(false);
  // Shortcuts coach is separate: fires once per user, regardless of
  // whether they've seen the canvas coach. Lets us reach existing users
  // who already dismissed CANVAS_STEPS before Cmd+K was taught.
  const [shortcutsCoachActive, setShortcutsCoachActive] = useState(false);
  useEffect(() => {
    const seen = safeStorage.getItem("inspira_onboarded_canvas");
    if (seen === "true") return;
    let cancelled = false;
    let rafId: number | null = null;
    const start = performance.now();
    const tick = () => {
      if (cancelled) return;
      const hasNode = document.querySelector(".topic-node") !== null;
      const elapsed = performance.now() - start;
      if (hasNode && elapsed >= 300) {
        setCanvasCoachActive(true);
        return;
      }
      // Give up after 10s so a permanently-empty canvas doesn't poll forever.
      if (elapsed > 10_000) return;
      rafId = window.requestAnimationFrame(tick);
    };
    rafId = window.requestAnimationFrame(tick);
    return () => {
      cancelled = true;
      if (rafId !== null) window.cancelAnimationFrame(rafId);
    };
  }, []);

  // Defer the shortcuts coach until the primary canvas coach has been
  // dismissed — otherwise two coach bubbles would fight for attention
  // on a fresh signup. For returning users who dismissed the canvas
  // coach long ago, this fires on the next canvas mount.
  useEffect(() => {
    const canvasSeen = safeStorage.getItem("inspira_onboarded_canvas");
    const shortcutsSeen = safeStorage.getItem("inspira_onboarded_shortcuts");
    if (canvasSeen !== "true" || shortcutsSeen === "true") return;
    // Small delay so the pill is definitely in the DOM.
    const id = window.setTimeout(() => setShortcutsCoachActive(true), 600);
    return () => window.clearTimeout(id);
  }, [canvasCoachActive]);

  // Realtime collab hook — opens a WS per project, tracks presence.
  // Lifted here so both ProjectCanvasInner (for cursor/viewport send)
  // and the TopicDetail drawer (for lock + contradiction events) can
  // read the same state via RealtimeContext. Passing `null` disables
  // the hook gracefully before a project is loaded.
  const realtime = useRealtime(props.projectId, DEFAULT_BASE_URL);

  // Wrap in a provider so useReactFlow() works inside the canvas.
  return (
    <ReactFlowProvider>
      <RealtimeProvider value={realtime}>
        <CommentsProvider projectId={props.projectId}>
          <ProjectCanvasInner {...props} />
          <CommentsLayer onCascadeComplete={props.onRefetch} />
        </CommentsProvider>
        <Coachmark
          active={canvasCoachActive}
          storageKey="inspira_onboarded_canvas"
          steps={CANVAS_STEPS}
          onDone={() => setCanvasCoachActive(false)}
        />
        <Coachmark
          active={shortcutsCoachActive}
          storageKey="inspira_onboarded_shortcuts"
          steps={SHORTCUTS_STEPS}
          onDone={() => setShortcutsCoachActive(false)}
        />
      </RealtimeProvider>
    </ReactFlowProvider>
  );
}

function ProjectCanvasInner({
  projectId,
  topics,
  relationships,
  decisionsByTopicId,
  kickoff,
  onOpenTopic,
  hiddenTopicId,
  onRefetch,
  pendingDeletionSuggestions,
  onDismissDeletionSuggestion,
  onConfirmDeletion,
}: ProjectCanvasProps) {
  // B1.2 — subscribe to the orchestrator's SSE stream for this project. Re-emits
  // sub_agent.* and conflict.* events as window CustomEvents that the
  // chrome consumers (MultiAgentDots in TopicNode, ConflictBanner)
  // listen for. No-op when the orchestrator isn't yet streaming (EventSource opens but
  // never receives data).
  useSSE(projectId);
  const navigate = useNavigate();

  // Auto-spawn the orchestrator when the canvas opens and no topics
  // exist yet, then poll every 4s for the first topic to land. The
  // canvas surface itself stays unblocked — partner can pan + zoom
  // an empty canvas while the orchestrator works, and topics pop in
  // live. The OrchestratorChip on the top-bar is the user-facing
  // "Inspira is working" signal. (Product decision:
  // "shouldn't block me from viewing the canvas just because they're
  // not completed.")
  useEffect(() => {
    if (topics.length > 0) return;
    let cancelled = false;
    void api.startProjectCanvas(projectId).catch((err) => {
      console.warn("[Canvas] auto-start failed; partner can retry", err);
    });
    const poll = window.setInterval(async () => {
      try {
        const res = await api.listTopics(projectId);
        if (cancelled) return;
        if (res.topics && res.topics.length > 0) {
          window.clearInterval(poll);
          onRefetch?.();
        }
      } catch {
        // Network blip — keep polling.
      }
    }, 4000);
    return () => {
      cancelled = true;
      window.clearInterval(poll);
    };
  }, [projectId, topics.length, onRefetch]);

  // Brief "Tidied" flash on the Tidy button after a tidy action fires.
  // Without this, when the layout is already optimal there's no visible
  // response and the user wonders whether the click even registered.
  const [tidyFlash, setTidyFlash] = useState(false);
  // On mobile the canvas-actions rail has too many pills; a
  // mobile-device report showed TE avatar + Tidy + Fit + Activity +
  // Summary + Export = 6 buttons vertically stacked, covering half the
  // right edge. Collapse Activity/Summary/Export into a single "⋯"
  // disclosure that expands on tap. Desktop still shows all five.
  const [moreOpen, setMoreOpen] = useState(false);
  // Safety net: ensure no two topics arrive at the canvas with overlapping
  // positions, regardless of where they came from (kickoff, refetch, drag
  // mid-flight, stale persisted state from older sessions). Corrections
  // are persisted back so the backend gets cleaned up too. This makes the
  // canvas self-healing — if a position EVER becomes overlapping, it
  // gets fixed on the next render pass.
  const safeTopics = useMemo(() => ensureNoOverlaps(topics), [topics]);
  useEffect(() => {
    for (const cleaned of safeTopics) {
      const original = topics.find((o) => o.topic_id === cleaned.topic_id);
      if (
        original &&
        (original.position_x !== cleaned.position_x ||
          original.position_y !== cleaned.position_y)
      ) {
        api
          .updateTopic(cleaned.topic_id, {
            position_x: cleaned.position_x,
            position_y: cleaned.position_y,
          })
          .catch((err) =>
            console.warn("[Inspira] failed to persist overlap fix", err),
          );
      }
    }
  }, [safeTopics, topics]);

  // The parent runs dagre before passing topics in, so first paint is
  // already tidy. This component just renders what it's given and hands
  // edits back through the callbacks.
  const initialNodes = useMemo(
    () => topicsToNodes(
      safeTopics, kickoff, onOpenTopic, decisionsByTopicId,
      pendingDeletionSuggestions, onDismissDeletionSuggestion, onConfirmDeletion,
    ),
    [safeTopics, kickoff, onOpenTopic, decisionsByTopicId,
      pendingDeletionSuggestions, onDismissDeletionSuggestion, onConfirmDeletion],
  );
  const initialPositionById = useMemo(
    () =>
      new Map(
        safeTopics.map((tp) => [tp.topic_id, { x: tp.position_x, y: tp.position_y }]),
      ),
    [safeTopics],
  );
  // The toolbar callbacks are stable (declared via useCallback below),
  // but we can't reference them up here in the initial-render useMemo
  // because they're declared later in this component. We seed the
  // initial edges WITHOUT toolbar callbacks; the post-mount effect
  // below (line ~462) re-sets edges via the same builder once the
  // callbacks exist, and the toolbar shows up on the first selection.
  const initialEdges = useMemo(
    () => relationshipsToEdges(relationships, initialPositionById),
    [relationships, initialPositionById],
  );

  const [nodes, setNodes, onNodesChange] = useNodesState(initialNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(initialEdges);
  const {
    fitView,
    setCenter,
    getViewport,
    getNodes,
    screenToFlowPosition,
    setViewport,
  } = useReactFlow();

  // Realtime hook state — our cursor/viewport senders, peers, locks,
  // and follow target. The Inner component is the natural home for
  // the DOM event wiring: it owns the ReactFlow container + has
  // direct access to the ReactFlow coordinate transform helpers.
  const realtime = useRealtimeContext();
  const sendCursorRef = useRef(realtime.sendCursor);
  const sendViewportRef = useRef(realtime.sendViewport);
  sendCursorRef.current = realtime.sendCursor;
  sendViewportRef.current = realtime.sendViewport;

  // Follow-mode: when the user clicks an avatar, their viewport
  // mirrors the targeted peer. We tween to the peer's current
  // viewport on each update. `setViewport` smoothly interpolates so
  // the camera glides rather than snapping — matches Figma's feel.
  // The effect auto-releases when the user clears the target (Esc
  // / "Exit follow" / peer disconnects).
  useEffect(() => {
    const target = realtime.followingSessionId;
    if (!target) return;
    const peer = realtime.peers.find((p) => p.sessionId === target);
    if (!peer || !peer.viewport) return;
    try {
      setViewport(
        {
          x: peer.viewport.x,
          y: peer.viewport.y,
          zoom: peer.viewport.zoom,
        },
        { duration: 220 },
      );
    } catch {
      /* ReactFlow not ready yet — next update will catch us up */
    }
  }, [realtime.followingSessionId, realtime.peers, setViewport]);

  // Esc clears follow mode so the user can escape without hunting for
  // the button. Only binds when actually following.
  useEffect(() => {
    if (!realtime.followingSessionId) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") realtime.setFollowing(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [realtime.followingSessionId, realtime]);

  // The currently "focused" topic — the node arrow-key navigation pivots
  // around. Kept in sync with React Flow's own selection state so clicking
  // a card and then pressing an arrow moves from that card. When nothing
  // has ever been selected, the focus-move handler falls back to whichever
  // node is closest to the viewport center.
  const [focusedNodeId, setFocusedNodeId] = useState<string | null>(null);
  // Transient set of node ids that just received keyboard focus. We stamp
  // `inspira-focus-pulse` on the React Flow node wrapper className for
  // 300ms so the CSS keyframe fires once, then strip it. Tracked as a
  // Set so overlapping pulses (user mashes arrow keys) behave sensibly.
  const [pulsingNodeIds, setPulsingNodeIds] = useState<Set<string>>(new Set());

  // Re-sync React Flow state when the parent-supplied data changes
  // (e.g. after a refetch adds a new topic). Use safeTopics so the
  // overlap correction propagates into React Flow state.
  useEffect(() => {
    setNodes(topicsToNodes(
      safeTopics, kickoff, onOpenTopic, decisionsByTopicId,
      pendingDeletionSuggestions, onDismissDeletionSuggestion, onConfirmDeletion,
    ));
  }, [safeTopics, kickoff, onOpenTopic, decisionsByTopicId, setNodes,
    pendingDeletionSuggestions, onDismissDeletionSuggestion, onConfirmDeletion]);

  // Hide the source card during the open-detail morph — otherwise the
  // unmorphed card sits on the canvas behind the morphing detail view,
  // creating a duplicate. Targeted style update so we don't recompute
  // every node on each open/close.
  useEffect(() => {
    setNodes((prev) =>
      prev.map((n) =>
        n.id === hiddenTopicId
          ? {
              ...n,
              style: { ...(n.style ?? {}), opacity: 0, pointerEvents: "none" },
            }
          : // Only strip the morph-hide style; preserve any pre-existing style.
            n.style && (n.style.opacity === 0 || n.style.opacity === "0")
            ? { ...n, style: { ...n.style, opacity: undefined, pointerEvents: undefined } }
            : n,
      ),
    );
  }, [hiddenTopicId, setNodes]);

  // Re-apply arrow-key focus selection after the nodes array is rebuilt
  // (e.g. parent refetched after a duplicate, a tidy, or an LLM add).
  // Without this, `focusedNodeId` would point at a node that's no longer
  // marked selected in React Flow state, so the next arrow press would
  // still work logically but the visual selection hint would be missing.
  //
  // Includes `safeTopics` in the dep list — the prior version read a
  // stale `safeTopics` closure captured at the first render and therefore
  // never re-fired when the topic list changed. Depending on safeTopics
  // ensures we replay the selection hint after every refetch.
  useEffect(() => {
    if (focusedNodeId === null) return;
    setNodes((prev) =>
      prev.map((n) =>
        n.id === focusedNodeId && !n.selected
          ? { ...n, selected: true }
          : n.id !== focusedNodeId && n.selected
            ? { ...n, selected: false }
            : n,
      ),
    );
  }, [focusedNodeId, safeTopics, setNodes]);
  // Derive a stable position-hash string from node ids + coordinates.
  // Changes only when a node actually moves, not on every React Flow render
  // that returns the same array reference. Used as a dep for both edge effects
  // so neither needs an eslint-disable on exhaustive-deps.
  const posHash = useMemo(
    () =>
      nodes
        .map((n) => `${n.id}:${n.position.x},${n.position.y}`)
        .join("|"),
    [nodes],
  );

  const posById = useMemo(
    () =>
      new Map(nodes.map((n) => [n.id, { x: n.position.x, y: n.position.y }])),
    // eslint is fine here: posHash changing is the signal; nodes identity
    // is secondary and covered by posHash.
    [posHash], // eslint-disable-line react-hooks/exhaustive-deps
  );

  // L5d — callback refs are populated by a sync effect later in the
  // component (after the actual handler implementations are declared).
  // Using refs here avoids the chicken-and-egg between this useEffect
  // and the handler useCallbacks that depend on state declared even
  // further down (editingEdge, etc.). The thunks below are stable
  // identities so this useEffect doesn't churn on every render.
  const handleEditEdgeFromToolbarRef = useRef<
    ((edgeId: string) => void) | null
  >(null);
  const handleDeleteEdgeFromToolbarRef = useRef<
    ((edgeId: string) => void) | null
  >(null);

  useEffect(() => {
    setEdges(
      relationshipsToEdges(relationships, posById, {
        onEditEdge: (edgeId: string) =>
          handleEditEdgeFromToolbarRef.current?.(edgeId),
        onDeleteEdge: (edgeId: string) =>
          handleDeleteEdgeFromToolbarRef.current?.(edgeId),
      }),
    );
  }, [relationships, posById, setEdges]);

  // Re-route every edge whenever node positions change, so the line
  // always exits whichever SIDE of each card is closest to the other.
  // Cheap: O(nodes + edges) and only fires when position identity changes.
  useEffect(() => {
    setEdges((currentEdges) =>
      currentEdges.map((edge) => {
        const src = posById.get(edge.source);
        const tgt = posById.get(edge.target);
        const srcX = src !== undefined ? src.x + TOPIC_CARD_WIDTH / 2 : null;
        const tgtX = tgt !== undefined ? tgt.x + TOPIC_CARD_WIDTH / 2 : null;
        const { sourceHandle, targetHandle } = pickClosestHandles(srcX, tgtX);
        if (
          edge.sourceHandle === sourceHandle &&
          edge.targetHandle === targetHandle
        ) {
          return edge;
        }
        return { ...edge, sourceHandle, targetHandle };
      }),
    );
  }, [posById, setEdges]);

  // Tidy button: re-run dagre on the current graph, update positions in
  // React Flow, and persist to the backend. Fits the view afterward.
  const handleTidy = useCallback(() => {
    // Reconstruct lightweight topic/relationship view from React Flow state
    // so we respect any pending-but-unflushed in-memory positions.
    const topicView: Topic[] = nodes.map((n) => ({
      topic_id: n.id,
      project_id: projectId,
      title: (n.data as TopicNodeData).title,
      icon: (n.data as TopicNodeData).icon,
      position_x: n.position.x,
      position_y: n.position.y,
      status: (n.data as TopicNodeData).status,
      order_index: 0,
      origin: "user_manual",
      created_at: "",
      updated_at: "",
    }));
    const relView: Relationship[] = edges.map((e) => ({
      relationship_id: e.id,
      project_id: projectId,
      source_topic_id: e.source,
      target_topic_id: e.target,
      label: (typeof e.label === "string" ? e.label : null) ?? null,
      origin: "user_drawn",
      strength: null,
      created_at: "",
    }));
    const layout = computeTopicLayout(topicView, relView);
    setNodes((ns) =>
      ns.map((n) => {
        const pos = layout[n.id];
        if (!pos) return n;
        return { ...n, position: pos };
      }),
    );
    for (const [topicId, pos] of Object.entries(layout)) {
      api
        .updateTopic(topicId, { position_x: pos.x, position_y: pos.y })
        .catch((err) => console.warn("[Inspira] tidy persist failed", err));
    }
    // Fit after layout changes so everything's in view.
    setTimeout(() => fitView({ padding: 0.25, duration: 300 }), 50);
    // Flash the Tidy button so a no-op tidy still confirms the click.
    setTidyFlash(true);
    setTimeout(() => setTidyFlash(false), 900);
  }, [nodes, edges, projectId, setNodes, fitView]);

  // The global keyboard-shortcut layer fires an `inspira:canvas-tidy`
  // window event when the user presses `T`. Loose coupling via an event
  // keeps the shortcut wiring outside this component without needing a
  // shared ref or prop.
  useEffect(() => {
    const onTidyEvent = () => handleTidy();
    window.addEventListener("inspira:canvas-tidy", onTidyEvent);
    return () => window.removeEventListener("inspira:canvas-tidy", onTidyEvent);
  }, [handleTidy]);

  // Zoom-to-fit: animate the viewport so every topic is visible with a
  // little breathing room. Triggered by the Fit button or pressing `F`
  // (dispatched as `inspira:canvas-fit-view` by the global shortcut layer).
  const handleFitView = useCallback(() => {
    fitView({ padding: 0.1, duration: 260 });
  }, [fitView]);

  useEffect(() => {
    const onFitEvent = () => handleFitView();
    window.addEventListener("inspira:canvas-fit-view", onFitEvent);
    return () =>
      window.removeEventListener("inspira:canvas-fit-view", onFitEvent);
  }, [handleFitView]);

  // ---- Arrow-key canvas navigation --------------------------------------
  //
  // ShortcutsProvider fires `inspira:canvas-focus-move` with a direction
  // payload on each of the four arrow keys. We pick the "nearest" topic
  // in that direction from the currently-focused node — or, when no node
  // has been focused yet, from whichever topic is closest to the viewport
  // center — then select it, pan the viewport to it, and pulse a brief
  // border so the user can track the jump.
  //
  // Direction semantics: we filter to candidates whose CARD CENTER is in
  // the half-plane matching the arrow (right → target.x > anchor.x, etc.),
  // then score each by a directional distance — primary axis gets triple
  // weight so "move right" never picks a node that's merely slightly to
  // the right but mostly down. Ties on Euclidean distance are broken by
  // the primary axis closest first. If no candidate exists (user is at
  // the edge of the graph), we no-op rather than wrap — wrapping makes
  // keyboard navigation feel unpredictable on non-grid layouts.
  // Track every in-flight pulse timeout in a map so we can clear ALL of
  // them on unmount (or when a new pulse preempts one on the same node).
  // Previously the setTimeout id was dropped on the floor; an unmount
  // mid-pulse would still fire the setState on a gone component and
  // React warned about "state update on unmounted component".
  const pulseTimersRef = useRef<Map<string, number>>(new Map());
  const pulseNode = useCallback((nodeId: string) => {
    setPulsingNodeIds((prev) => {
      const next = new Set(prev);
      next.add(nodeId);
      return next;
    });
    // If a prior pulse on the same node is still pending, cancel it so
    // we don't double-clear and the new animation gets a full 300ms.
    const existing = pulseTimersRef.current.get(nodeId);
    if (existing !== undefined) {
      window.clearTimeout(existing);
    }
    const id = window.setTimeout(() => {
      pulseTimersRef.current.delete(nodeId);
      setPulsingNodeIds((prev) => {
        if (!prev.has(nodeId)) return prev;
        const next = new Set(prev);
        next.delete(nodeId);
        return next;
      });
    }, 300);
    pulseTimersRef.current.set(nodeId, id);
  }, []);
  // Clear any still-pending pulse timers on unmount — see pulseNode above.
  useEffect(() => {
    const timers = pulseTimersRef.current;
    return () => {
      for (const id of timers.values()) {
        window.clearTimeout(id);
      }
      timers.clear();
    };
  }, []);

  const focusNode = useCallback(
    (nodeId: string, { pulse = true }: { pulse?: boolean } = {}) => {
      // Update React Flow selection so handleSelectionChange keeps focus
      // in sync on the visual highlight and aria-selected affordances.
      setNodes((prev) =>
        prev.map((n) => ({ ...n, selected: n.id === nodeId })),
      );
      setFocusedNodeId(nodeId);
      // Pan (don't zoom) the viewport to the card's center so it's
      // visible. We use the current zoom level rather than forcing a
      // zoom reset — keeps the user's sense of canvas scale intact.
      const node = getNodes().find((n) => n.id === nodeId);
      if (node) {
        const centerX = node.position.x + TOPIC_CARD_WIDTH / 2;
        const centerY = node.position.y + TOPIC_CARD_HEIGHT / 2;
        const { zoom } = getViewport();
        setCenter(centerX, centerY, { zoom, duration: 220 });
      }
      if (pulse) pulseNode(nodeId);
    },
    [getNodes, getViewport, pulseNode, setCenter, setNodes],
  );

  const pickNearestInDirection = useCallback(
    (
      fromId: string | null,
      direction: "up" | "down" | "left" | "right",
    ): string | null => {
      const allNodes = getNodes();
      if (allNodes.length === 0) return null;

      // Determine the anchor point: the focused node's center, or — if
      // no node is focused yet — the topic closest to the viewport
      // center. Using nodes rather than the raw topic list keeps us
      // consistent with any in-flight drag positions.
      let anchor: { x: number; y: number };
      if (fromId !== null) {
        const current = allNodes.find((n) => n.id === fromId);
        if (!current) return null;
        anchor = {
          x: current.position.x + TOPIC_CARD_WIDTH / 2,
          y: current.position.y + TOPIC_CARD_HEIGHT / 2,
        };
      } else {
        // Viewport center in canvas coordinates.
        const { x: vpX, y: vpY, zoom } = getViewport();
        const cx = (window.innerWidth / 2 - vpX) / zoom;
        const cy = (window.innerHeight / 2 - vpY) / zoom;
        // Nearest-by-Euclidean, not direction-filtered — the first
        // arrow press from "nothing selected" should land on whatever's
        // closest to where the user is looking, regardless of arrow.
        let best: { id: string; d2: number } | null = null;
        for (const n of allNodes) {
          const nx = n.position.x + TOPIC_CARD_WIDTH / 2;
          const ny = n.position.y + TOPIC_CARD_HEIGHT / 2;
          const d2 = (nx - cx) ** 2 + (ny - cy) ** 2;
          if (best === null || d2 < best.d2) best = { id: n.id, d2 };
        }
        return best ? best.id : null;
      }

      // Filter candidates by half-plane on the direction axis.
      const candidates = allNodes.filter((n) => {
        if (n.id === fromId) return false;
        const cx = n.position.x + TOPIC_CARD_WIDTH / 2;
        const cy = n.position.y + TOPIC_CARD_HEIGHT / 2;
        const dx = cx - anchor.x;
        const dy = cy - anchor.y;
        // Use a small slack so tiny sub-pixel differences don't drop a
        // co-linear card out of the candidate list.
        const slack = 1;
        if (direction === "right") return dx > slack;
        if (direction === "left") return dx < -slack;
        if (direction === "down") return dy > slack;
        return dy < -slack; // "up"
      });
      if (candidates.length === 0) return null;

      // Score: triple-weight primary axis so cards pushed far on the
      // cross-axis don't beat a closer on-axis pick. Tie-break by
      // Euclidean distance (d2), which the weighted score already
      // incorporates — two candidates with the same d2 AND the same
      // primary offset would be visually indistinguishable anyway.
      let best: { id: string; score: number; d2: number } | null = null;
      for (const n of candidates) {
        const cx = n.position.x + TOPIC_CARD_WIDTH / 2;
        const cy = n.position.y + TOPIC_CARD_HEIGHT / 2;
        const dx = cx - anchor.x;
        const dy = cy - anchor.y;
        const primary =
          direction === "left" || direction === "right"
            ? Math.abs(dx)
            : Math.abs(dy);
        const cross =
          direction === "left" || direction === "right"
            ? Math.abs(dy)
            : Math.abs(dx);
        const score = primary * primary + cross * cross * 3;
        const d2 = dx * dx + dy * dy;
        if (
          best === null ||
          score < best.score ||
          (score === best.score && d2 < best.d2)
        ) {
          best = { id: n.id, score, d2 };
        }
      }
      return best ? best.id : null;
    },
    [getNodes, getViewport],
  );

  useEffect(() => {
    const onFocusMove = (ev: Event) => {
      const detail = (ev as CustomEvent).detail as
        | { direction?: "up" | "down" | "left" | "right" }
        | undefined;
      const direction = detail?.direction;
      if (!direction) return;
      const nextId = pickNearestInDirection(focusedNodeId, direction);
      if (!nextId) return;
      focusNode(nextId);
    };
    window.addEventListener(
      "inspira:canvas-focus-move",
      onFocusMove as EventListener,
    );
    return () =>
      window.removeEventListener(
        "inspira:canvas-focus-move",
        onFocusMove as EventListener,
      );
  }, [focusedNodeId, focusNode, pickNearestInDirection]);

  // ---- Duplicate the currently-selected topic ---------------------------
  //
  // ShortcutsProvider dispatches `inspira:topic-duplicate-selected` on
  // Cmd/Ctrl+D. We look up the currently-focused topic and POST to the
  // backend's /duplicate endpoint; on success we refetch so the new
  // sibling appears on the canvas. If nothing is selected we show a
  // gentle reminder toast rather than silently no-oping.
  useEffect(() => {
    const onDuplicateSelected = () => {
      const targetId = focusedNodeId;
      if (!targetId) {
        toast.info(t("canvas.duplicate.toast_none_selected"));
        return;
      }
      const current = getNodes().find((n) => n.id === targetId);
      const title =
        current && typeof (current.data as TopicNodeData).title === "string"
          ? (current.data as TopicNodeData).title
          : "this topic";
      api
        .duplicateTopic(targetId)
        .then((newTopic) => {
          toast.success(t("canvas.duplicate.toast_ok", { title }));
          onRefetch?.();
          // After the parent refetches, the new topic will appear in the
          // next render pass. Move focus onto it so the user can chain
          // another Cmd/Ctrl+D without re-clicking.
          setFocusedNodeId(newTopic.topic_id);
        })
        .catch((err) => {
          if (err instanceof Error && err.name === "ProjectNotFoundError") {
            throw err;
          }
          console.error("[Inspira] failed to duplicate topic", err);
          toast.error(t("canvas.duplicate.toast_failed"));
        });
    };
    window.addEventListener(
      "inspira:topic-duplicate-selected",
      onDuplicateSelected,
    );
    return () =>
      window.removeEventListener(
        "inspira:topic-duplicate-selected",
        onDuplicateSelected,
      );
  }, [focusedNodeId, getNodes, onRefetch]);

  // MiniMap expand/collapse — remembered per-device. We treat the absence
  // of the storage key as "never seen it" and start collapsed; only an
  // explicit user choice to expand sets the flag.
  const [minimapExpanded, setMinimapExpanded] = useState<boolean>(() => {
    if (typeof window === "undefined") return false;
    try {
      return window.localStorage.getItem("inspira_minimap_expanded") === "true";
    } catch {
      return false;
    }
  });

  const toggleMinimap = useCallback(() => {
    setMinimapExpanded((prev) => {
      const next = !prev;
      try {
        window.localStorage.setItem(
          "inspira_minimap_expanded",
          next ? "true" : "false",
        );
      } catch {
        // Ignore — private mode / quota. Toggle still works in-memory.
      }
      return next;
    });
  }, []);

  // Activity timeline visibility. Local to ProjectCanvas so the panel
  // state resets whenever the canvas unmounts — that's the right default
  // since the data it shows is project-scoped.
  const [activityOpen, setActivityOpen] = useState(false);
  const openActivity = useCallback(() => setActivityOpen(true), []);
  const closeActivity = useCallback(() => setActivityOpen(false), []);

  // Colors for each node on the minimap. Defaults to sage; rust when the
  // planner is suggesting deletion; gold when the topic is fleshed out.
  // Everything falls back to literal hex so the minimap still paints in
  // environments where CSS vars resolve later than the first draw.
  const minimapNodeColor = useCallback(
    (n: Node): string => {
      if (pendingDeletionSuggestions?.[n.id]) {
        return "var(--rust, #9a4e38)";
      }
      const data = n.data as TopicNodeData | undefined;
      if (data?.status === "fleshed_out") {
        return "var(--gold, #8d6a23)";
      }
      return "var(--sage, #568868)";
    },
    [pendingDeletionSuggestions],
  );

  // ---- Export is now owned by InspiraApp ------------------------------
  //
  // We used to run an inline PDF export here. That moved up to InspiraApp
  // where the ExportOptionsDialog lets the user choose format (markdown /
  // pdf / html / json). The canvas-actions button now just dispatches
  // a `inspira:export-request` window event; the parent handles the rest.

  // ---- Node delete (press Delete/Backspace with a topic selected) ----
  //
  // React Flow emits a 'remove' change for the node. Rather than doing the
  // actual delete here, we raise a `inspira:topic-delete-request` custom
  // event; InspiraApp listens for it and shows the DeleteConfirmDialog.
  // On confirm, the parent calls api.deleteTopic and then refetches.
  // We always call onRefetch afterward so the node comes back if the user
  // cancels — React Flow has already optimistically stripped it from
  // internal state.
  const handleNodeDelete = useCallback(
    (changes: NodeChange[]) => {
      for (const c of changes) {
        if (c.type !== "remove") continue;
        const removed = c as NodeRemoveChange;
        const node = nodes.find((n) => n.id === removed.id);
        const title =
          node && typeof (node.data as TopicNodeData).title === "string"
            ? (node.data as TopicNodeData).title
            : "this topic";
        window.dispatchEvent(
          new CustomEvent("inspira:topic-delete-request", {
            detail: { topicId: removed.id, title },
          }),
        );
        // Regardless of user response, ask the parent to refetch so the
        // canvas is rebuilt from authoritative state after the dialog
        // resolves. If the user confirmed, the topic is gone; if they
        // cancelled, it reappears.
        onRefetch?.();
      }
    },
    [nodes, onRefetch],
  );

  // ---- Drag-to-reposition with collision avoidance ----
  //
  // When a drag ends, check whether the dropped position overlaps any
  // other card. If it does, resolve to the nearest non-overlapping
  // position via ring search and REWRITE the position change before
  // committing so React Flow only sees the corrected coordinates. The
  // old flow applied the raw change first and then patched via a second
  // setNodes, which caused a visible single-frame flicker where the
  // card snapped onto another card and then jumped away. Resolving
  // first means there's only one commit per drag end.
  const handleNodesChange = useCallback(
    (changes: NodeChange[]) => {
      // Handle node deletes first so we can intercept the confirm flow
      // before React Flow strips them from state.
      const hasRemoves = changes.some((c) => c.type === "remove");
      if (hasRemoves) {
        handleNodeDelete(changes);
      }

      // Pre-compute corrected positions for every drag-end change BEFORE
      // we commit to React Flow state. This way the raw drop position is
      // never visible to the renderer.
      const corrections = new Map<
        string,
        { x: number; y: number }
      >();
      for (const c of changes) {
        if (!isPositionEnd(c)) continue;
        const { id, position } = c;
        if (!position) continue;
        // Obstacle list uses the PRE-drop node positions (nodes in state)
        // with the dragged node's candidate position swapped in.
        const others = nodes.map((n) => ({
          id: n.id,
          position: n.id === id ? position : n.position,
          width: TOPIC_CARD_WIDTH,
          height: TOPIC_CARD_HEIGHT,
        }));
        const resolved = resolveOverlap(id, position, others);
        if (resolved.x !== position.x || resolved.y !== position.y) {
          corrections.set(id, resolved);
        }
      }

      // Rewrite the position on any change whose landing spot needs
      // nudging. Non-drag-end changes (selection, dimensions, etc.) pass
      // through untouched.
      const patched: NodeChange[] = corrections.size
        ? changes.map((c) => {
            if (!isPositionEnd(c)) return c;
            const fix = corrections.get(c.id);
            if (!fix || !c.position) return c;
            return { ...c, position: { x: fix.x, y: fix.y } };
          })
        : changes;

      onNodesChange(patched);

      // Persist whatever we actually committed — original position for
      // non-corrected drops, the nudged position for corrected ones.
      for (const c of patched) {
        if (!isPositionEnd(c)) continue;
        const { id, position } = c;
        if (!position) continue;
        api
          .updateTopic(id, {
            position_x: position.x,
            position_y: position.y,
          })
          .catch((err) => {
            console.error("[Inspira] failed to persist topic position", err);
          });
      }
    },
    [handleNodeDelete, nodes, onNodesChange],
  );

  // ---- Edge create via drag-to-connect ----
  const handleConnect = useCallback(
    (params: Connection) => {
      if (!params.source || !params.target) return;
      if (params.source === params.target) return;
      // Optimistic add — give it a temp ID, replace once server responds.
      const tempId = `tmp-${Math.random().toString(36).slice(2, 10)}`;
      setEdges((eds) =>
        addEdge({ ...params, id: tempId, ...dottedEdgeStyle() }, eds),
      );
      api
        .createRelationship(projectId, {
          source_topic_id: params.source,
          target_topic_id: params.target,
          label: null,
        })
        .then(({ relationship }) => {
          // Replace temp edge with the persisted one (real ID).
          setEdges((eds) =>
            eds.map((e) =>
              e.id === tempId
                ? {
                    ...e,
                    id: relationship.relationship_id,
                  }
                : e,
            ),
          );
          onRefetch?.();
        })
        .catch((err) => {
          console.error("[Inspira] failed to create relationship", err);
          // Roll back optimistic add.
          setEdges((eds) => eds.filter((e) => e.id !== tempId));
        });
    },
    [projectId, setEdges, onRefetch],
  );

  // ---- Edge delete: React Flow emits remove changes on Delete/Backspace ----
  const handleEdgesChange = useCallback(
    (changes: EdgeChange[]) => {
      onEdgesChange(changes);
      for (const c of changes) {
        if (c.type === "remove") {
          // Temp IDs never reached the server; skip.
          if (c.id.startsWith("tmp-")) continue;
          api.deleteRelationship(c.id).catch((err) => {
            console.error("[Inspira] failed to delete relationship", err);
          });
        }
      }
    },
    [onEdgesChange],
  );

  // ---- Edge label edit via dialog (#036, L5c) --------------------------
  // Double-click on an edge opens the warm-editorial RelationshipLabelDialog,
  // which lets the user edit the label, clear it (empty submit), or delete
  // the relationship from the same surface. Replaces the native
  // `window.prompt` that used to live here AND the silent local-only update
  // (the TODO acknowledged the missing PATCH endpoint at the time). Backend
  // PATCH /api/v2/relationships/{id} landed in commit f24d214, so edits
  // now persist across reloads.
  const [editingEdge, setEditingEdge] = useState<Edge | null>(null);

  // Look up topic titles by id so the dialog can render its
  // "From → To" subline. Not the same as the local `titleById` map
  // inside `neighborsById` — that one's scoped to a useMemo for
  // computing per-node neighbor labels.
  const topicById = useMemo(
    () => new Map(safeTopics.map((tp) => [tp.topic_id, tp])),
    [safeTopics],
  );

  const handleEdgeDoubleClick = useCallback(
    (_event: React.MouseEvent, edge: Edge) => {
      setEditingEdge(edge);
    },
    [],
  );

  const handleEditingEdgeClose = useCallback(() => {
    setEditingEdge(null);
  }, []);

  const handleLabelSubmit = useCallback(
    async (newLabel: string | null) => {
      if (!editingEdge) return;
      const previous =
        typeof editingEdge.label === "string" ? editingEdge.label : "";
      const targetId = editingEdge.id;
      // Optimistic local update — paint the new label immediately so the
      // canvas feels instant, then persist. If the persist fails we revert
      // and re-throw so the dialog stays open with an inline error.
      setEdges((eds) =>
        eds.map((e) =>
          e.id === targetId ? { ...e, label: newLabel ?? undefined } : e,
        ),
      );
      try {
        await api.updateRelationshipLabel(targetId, newLabel);
        setEditingEdge(null);
      } catch (err) {
        console.error(
          "[Inspira] failed to persist relationship label",
          err,
        );
        // Revert the optimistic paint so the canvas matches the server
        // again. The dialog catches the rethrow and renders inline.
        setEdges((eds) =>
          eds.map((e) =>
            e.id === targetId ? { ...e, label: previous || undefined } : e,
          ),
        );
        throw err;
      }
    },
    [editingEdge, setEdges],
  );

  const handleDeleteFromDialog = useCallback(async () => {
    if (!editingEdge) return;
    const removed = editingEdge;
    // Optimistic removal — drop the edge immediately so the canvas
    // reflects the user's intent. Revert on failure with a toast.
    setEdges((eds) => eds.filter((e) => e.id !== removed.id));
    setEditingEdge(null);
    try {
      await api.deleteRelationship(removed.id);
      toast.success(t("relationship_dialog.deleted"));
    } catch (err) {
      console.error("[Inspira] failed to delete relationship", err);
      setEdges((eds) => [...eds, removed]);
      toast.error(t("relationship_dialog.delete_failed"));
    }
  }, [editingEdge, setEdges]);

  // L5d (#037) — Edge toolbar callbacks. The toolbar appears on edge
  // selection and exposes Edit + Delete inline. We use ref-style stable
  // callbacks (no edge state captured by closure) so the relationship
  // edges don't re-mount every time selection changes — the callbacks
  // look up fresh edge state at call time via setEdges' updater fn.
  const handleEditEdgeFromToolbar = useCallback(
    (edgeId: string) => {
      // Find the edge in the latest state and open the dialog.
      // Same as a double-click — same dialog, same persistence path.
      setEdges((eds) => {
        const target = eds.find((e) => e.id === edgeId);
        if (target) setEditingEdge(target);
        return eds;
      });
    },
    [setEdges],
  );

  const handleDeleteEdgeFromToolbar = useCallback(
    (edgeId: string) => {
      // Mirror handleDeleteFromDialog but operate on an explicit
      // edgeId — the toolbar fires this without first opening the
      // dialog, so we don't have `editingEdge` state to lean on.
      let removed: Edge | undefined;
      setEdges((eds) => {
        removed = eds.find((e) => e.id === edgeId);
        return eds.filter((e) => e.id !== edgeId);
      });
      if (!removed) return;
      const captured = removed;
      void api
        .deleteRelationship(edgeId)
        .then(() => toast.success(t("relationship_dialog.deleted")))
        .catch((err) => {
          console.error("[Inspira] failed to delete relationship", err);
          setEdges((eds) => [...eds, captured]);
          toast.error(t("relationship_dialog.delete_failed"));
        });
    },
    [setEdges],
  );

  // L5d — sync the actual handler implementations into the refs the
  // earlier useEffect relies on. Identity is stable thanks to the
  // useCallbacks above, so this effect runs only on first mount + on
  // genuine handler-identity changes (rare, only when setEdges
  // identity changes).
  useEffect(() => {
    handleEditEdgeFromToolbarRef.current = handleEditEdgeFromToolbar;
  }, [handleEditEdgeFromToolbar]);
  useEffect(() => {
    handleDeleteEdgeFromToolbarRef.current = handleDeleteEdgeFromToolbar;
  }, [handleDeleteEdgeFromToolbar]);

  // Keep a selected-edge visual hint: bold + accent when selected.
  const [selectedEdgeIds, setSelectedEdgeIds] = useState<Set<string>>(new Set());
  const handleSelectionChange = useCallback(
    ({ nodes: selNodes, edges: selEdges }: OnSelectionChangeParams) => {
      setSelectedEdgeIds(new Set(selEdges.map((e) => e.id)));
      // Only topic nodes are selectable in this canvas; the first entry in
      // the selection wins when there are multiple (rare — React Flow emits
      // this on marquee selects, too). A cleared selection does NOT reset
      // focusedNodeId — the arrow-key navigator remembers where it was so
      // a quick Esc + arrow still moves from the last known position.
      if (selNodes.length > 0) {
        setFocusedNodeId(selNodes[0].id);
      }
    },
    [],
  );

  const styledEdges = useMemo(
    () =>
      edges.map((e) =>
        selectedEdgeIds.has(e.id)
          ? { ...e, style: { ...e.style, stroke: "var(--sage, #6A9A7A)", strokeWidth: 2 } }
          : e,
      ),
    [edges, selectedEdgeIds],
  );

  // Stamp `inspira-focus-pulse` on the React Flow node wrapper for any
  // node whose id is in pulsingNodeIds. The CSS animation is a one-shot
  // keyframe that fades out in 300ms; we strip the class right after so
  // a subsequent pulse on the same node re-triggers it cleanly.
  const styledNodes = useMemo(
    () =>
      pulsingNodeIds.size === 0
        ? nodes
        : nodes.map((n) =>
            pulsingNodeIds.has(n.id)
              ? {
                  ...n,
                  className: [n.className, "inspira-focus-pulse"]
                    .filter(Boolean)
                    .join(" "),
                }
              : n,
          ),
    [nodes, pulsingNodeIds],
  );

  // Handler bridge so keyboard users on the topic-list fallback can open a
  // topic without needing to know its viewport rect — we pass a synthesized
  // rect anchored at the viewport center so any morph-in animation still
  // starts from somewhere sensible.
  const openTopicFromList = useCallback(
    (topicId: string) => {
      if (!onOpenTopic) return;
      // Use the shared card-size constants rather than literals so a
      // future bump to TOPIC_CARD_WIDTH / TOPIC_CARD_HEIGHT propagates
      // to the synthesized rect too. Center the rect around the viewport
      // midpoint — the caller uses this origin to FLIP-morph the detail
      // view open from a sensible position.
      const rect = new DOMRect(
        Math.max(0, window.innerWidth / 2 - TOPIC_CARD_WIDTH / 2),
        Math.max(0, window.innerHeight / 2 - TOPIC_CARD_HEIGHT / 2),
        TOPIC_CARD_WIDTH,
        TOPIC_CARD_HEIGHT,
      );
      onOpenTopic(topicId, rect);
    },
    [onOpenTopic],
  );

  return (
    <main
      id="main-content"
      tabIndex={-1}
      className="canvas-wrap"
      role="main"
      aria-label={t("canvas.aria")}
    >
      {/* Top-bar now renders an `<h1>` with the actual project title
          (see InspiraApp's `.top-bar__project-title`), which serves as
          the page-level heading for SR landmark navigation. The old
          inline `<h1 className="sr-only">Canvas</h1>` here was both
          redundant AND broken (sr-only wasn't a defined CSS class so
          the h1 actually rendered visibly at 28px, pushing the page
          past 100vh and forcing a body scrollbar). Removed entirely. */}
      {/* Item #173: top-of-canvas signal that the orchestrator is
          actively drafting topics. DraftingBanner self-gates on
          `inspira:sse:sub_agent.*` window events; skeletons fill the
          empty-canvas window before the first real topic streams in. */}
      <DraftingBanner />
      {topics.length === 0 ? <TopicCardSkeleton count={3} /> : null}
      {kickoff?.opening_card?.body ? (
        <PlannerOpeningCard body={kickoff.opening_card.body} />
      ) : null}
      <div
        className="canvas-actions"
        data-more-open={moreOpen ? "true" : "false"}
      >
        {/* Presence avatars sit to the left of the action buttons so
            users see who's on the canvas at a glance. Clickable for
            follow-mode. Renders nothing when the WS hasn't connected
            yet or there are no peers. */}
        <PresenceAvatars />
        <button
          type="button"
          className={
            "canvas-actions__btn" +
            (tidyFlash ? " canvas-actions__btn--flash" : "")
          }
          onClick={handleTidy}
          title={t("canvas.actions.tidy_title")}
        >
          {tidyFlash ? t("canvas.actions.tidy_done") : t("canvas.actions.tidy")}
        </button>
        <button
          type="button"
          className="canvas-actions__btn"
          onClick={handleFitView}
          title={t("canvas.actions.fit_aria")}
          aria-label={t("canvas.actions.fit_aria")}
        >
          {t("canvas.actions.fit")}
        </button>
        {/* "More" disclosure — visible only on mobile via CSS. Toggles
            a data-attribute that reveals the Activity / Summary / Export
            siblings below. Desktop renders it hidden so the button row
            stays unchanged. */}
        <button
          type="button"
          className="canvas-actions__btn canvas-actions__btn--more"
          onClick={() => setMoreOpen((v) => !v)}
          aria-expanded={moreOpen}
          aria-controls="canvas-actions-more-group"
          aria-label={t("canvas.actions.more_aria")}
          title={t("canvas.actions.more_aria")}
        >
          ⋯
        </button>
        <button
          type="button"
          className="canvas-actions__btn canvas-actions__btn--activity canvas-actions__btn--in-more"
          onClick={() => { setMoreOpen(false); openActivity(); }}
          title={t("canvas.actions.activity_title")}
          aria-label={t("canvas.actions.activity_title")}
          aria-pressed={activityOpen}
        >
          <svg
            aria-hidden="true"
            width="13"
            height="13"
            viewBox="0 0 16 16"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.6"
            strokeLinecap="round"
            strokeLinejoin="round"
            style={{ marginRight: 6, verticalAlign: "-1px" }}
          >
            <circle cx="8" cy="8" r="6" />
            <path d="M8 4.5v3.8l2.4 1.6" />
          </svg>
          {t("canvas.actions.activity")}
        </button>
        <button
          type="button"
          className="canvas-actions__btn canvas-actions__btn--in-more"
          onClick={() => {
            setMoreOpen(false);
            // Delegate to InspiraApp — the LlmModesPanel lives up at the
            // app root so the summary / outline / dedupe tabs persist
            // across canvas re-renders and share one cache.
            window.dispatchEvent(
              new CustomEvent("inspira:open-llm-modes"),
            );
          }}
          title={t("canvas.actions.summary_title")}
        >
          {t("canvas.actions.summary")}
        </button>
        <button
          type="button"
          className="canvas-actions__btn canvas-actions__btn--in-more"
          onClick={() => {
            setMoreOpen(false);
            // Product decision: Code is now its own
            // top-level route (rail tab). Navigate to /code/<projectId>
            // so the page mounts inside AuthedShell with the rail
            // visible — partner can hop to /workspaces or other PRs
            // mid-IDE. Replaces the legacy `inspira:open-artifact`
            // window-event dispatch that transitioned the InspiraApp
            // phase machine into a modal "artifact" phase.
            navigate(`/code/${encodeURIComponent(projectId)}`);
          }}
          title="Open the generated code"
        >
          Code
        </button>
      </div>

      {/* Screen-reader and keyboard-user fallback for the canvas. React
          Flow is a visual surface — its pan/zoom + drag handlers absorb
          most keyboard input. This list mirrors the canvas as a flat,
          keyboard-navigable structure so users of AT can still open a
          topic without needing to interact with the graph layout.
          Sighted users never see this panel. */}
      <CanvasTopicListFallback
        topics={topics}
        relationships={relationships}
        onOpenTopic={openTopicFromList}
      />

      <CanvasErrorBoundary>
        <div
          style={{ position: "relative", width: "100%", height: "100%" }}
          onPointerMove={(e) => {
            // Convert screen coords to canvas (flow) coords and send.
            // Throttled inside the hook at 33ms to stay under network
            // + re-render budgets.
            const flow = screenToFlowPosition({
              x: e.clientX,
              y: e.clientY,
            });
            sendCursorRef.current(flow.x, flow.y);
          }}
        >
          <ReactFlow
            nodes={styledNodes}
            edges={styledEdges}
            nodeTypes={nodeTypes}
            edgeTypes={edgeTypes}
            onNodesChange={handleNodesChange}
            onEdgesChange={handleEdgesChange}
            onConnect={handleConnect}
            onEdgeDoubleClick={handleEdgeDoubleClick}
            onSelectionChange={handleSelectionChange}
            onMove={(_e, vp) => {
              sendViewportRef.current(vp.x, vp.y, vp.zoom);
            }}
            fitView
            fitViewOptions={{ padding: 0.25 }}
            minZoom={0.4}
            maxZoom={2.0}
            // When following a peer, disable local pan so the user
            // doesn't fight the auto-tracked viewport. Clicking Exit
            // follow re-enables it.
            nodesDraggable={!realtime.followingSessionId}
            nodesConnectable={!realtime.followingSessionId}
            panOnDrag={!realtime.followingSessionId}
            zoomOnScroll={!realtime.followingSessionId}
            elementsSelectable
            defaultEdgeOptions={dottedEdgeStyle()}
            connectionLineType={"smoothstep" as any}
            proOptions={{ hideAttribution: true }}
            aria-label={t("canvas.topic_list.intro")}
          >
            <Background
              variant={BackgroundVariant.Dots}
              gap={12}
              size={1}
              color="var(--grid-dot, rgba(43, 37, 32, 0.10))"
            />
            <Controls showInteractive={false} />
            {minimapExpanded ? (
              <MiniMap
                className="canvas-minimap"
                nodeColor={minimapNodeColor}
                nodeStrokeWidth={1}
                maskColor="var(--canvas-minimap-mask, rgba(43, 37, 32, 0.08))"
                pannable
                zoomable
              />
            ) : null}
          </ReactFlow>
          {/* Other users' cursors overlay the canvas. The layer itself
              is pointer-events:none so it never eats interaction with
              topic cards or ReactFlow controls. */}
          <RemoteCursors />
        </div>
      </CanvasErrorBoundary>
      <button
        type="button"
        className={
          "canvas-minimap-toggle" +
          (minimapExpanded ? " canvas-minimap-toggle--expanded" : "")
        }
        onClick={toggleMinimap}
        aria-expanded={minimapExpanded}
        aria-label={
          minimapExpanded
            ? t("canvas.minimap.toggle_hide")
            : t("canvas.minimap.toggle_show")
        }
        title={
          minimapExpanded
            ? t("canvas.minimap.toggle_hide")
            : t("canvas.minimap.toggle_show")
        }
      >
        {minimapExpanded
          ? t("canvas.minimap.toggle_hide")
          : t("canvas.minimap.toggle_show")}
      </button>
      {/* No empty-state overlay — product decision: even
          when topics haven't arrived yet, the partner should see the
          canvas surface and watch topics pop in live as the
          orchestrator drafts them. Auto-spawn + polling now lives in
          the useCanvasAutoSpawn hook below; the OrchestratorChip on
          the top-bar surfaces the "Running" state. */}
      {/* Review chrome — relocated to the Artifact Viewer per the
          v4 redesign. The canvas is now an editing
          surface only; approval lives where the code is. ConflictBanner
          only renders when the orchestrator emits conflict.detected. */}
      <ConflictBanner />
      {activityOpen ? (
        <ActivityTimeline
          projectId={projectId}
          onClose={closeActivity}
        />
      ) : null}
      {/* L5c — relationship-label edit dialog. Opens on double-click of
          an edge (handleEdgeDoubleClick → setEditingEdge). The dialog
          owns Save / Delete / Cancel; ProjectCanvas owns persistence
          + optimistic-update revert. The from/to titles are looked up
          from topicById so the user sees the contextual "Title → Title"
          subline rather than opaque ids. */}
      <RelationshipLabelDialog
        open={editingEdge !== null}
        currentLabel={
          typeof editingEdge?.label === "string" ? editingEdge.label : ""
        }
        fromTopicTitle={
          editingEdge
            ? topicById.get(editingEdge.source)?.title ?? ""
            : ""
        }
        toTopicTitle={
          editingEdge
            ? topicById.get(editingEdge.target)?.title ?? ""
            : ""
        }
        onSubmit={handleLabelSubmit}
        onDelete={handleDeleteFromDialog}
        onClose={handleEditingEdgeClose}
      />
    </main>
  );
}

// -----------------------------------------------------------------------
// CanvasTopicListFallback — a visually-hidden, keyboard-accessible list of
// every topic in the project. Screen readers and keyboard-only users can
// tab into it, navigate with arrow keys, and open any topic. Sighted users
// never see it — it's absolute positioned and clipped off-screen. This is
// our answer to the canvas accessibility limitation: graph-based UIs are
// inherently visual, so we provide a parallel landmark that gives the same
// "list of topics" affordance in a traditional linear structure.
// -----------------------------------------------------------------------
function CanvasTopicListFallback({
  topics,
  relationships,
  onOpenTopic,
}: {
  topics: Topic[];
  relationships: Relationship[];
  onOpenTopic: (topicId: string) => void;
}) {
  // For each topic, precompute the neighbors so screen readers announce
  // "Topic X, connected to Y and Z" — mirrors the visual dotted-line
  // information in a form non-visual users can parse.
  const neighborsById = useMemo(() => {
    const map = new Map<string, string[]>();
    const titleById = new Map(topics.map((tp) => [tp.topic_id, tp.title]));
    for (const r of relationships) {
      const src = titleById.get(r.source_topic_id);
      const tgt = titleById.get(r.target_topic_id);
      if (src && tgt) {
        const srcList = map.get(r.source_topic_id) ?? [];
        srcList.push(tgt);
        map.set(r.source_topic_id, srcList);
        const tgtList = map.get(r.target_topic_id) ?? [];
        tgtList.push(src);
        map.set(r.target_topic_id, tgtList);
      }
    }
    return map;
  }, [topics, relationships]);

  if (topics.length === 0) return null;

  return (
    <nav
      className="visually-hidden-focusable canvas-topic-list-fallback"
      aria-label={t("canvas.topic_list.aria")}
    >
      <h2>{t("canvas.topic_list.heading")}</h2>
      <p>{t("canvas.topic_list.intro")}</p>
      <ul>
        {topics.map((tp) => {
          const neighbors = neighborsById.get(tp.topic_id) ?? [];
          const neighborText =
            neighbors.length === 0
              ? ""
              : t("canvas.topic_list.connected_to", { list: neighbors.join(", ") });
          return (
            <li key={tp.topic_id}>
              <button
                type="button"
                onClick={() => onOpenTopic(tp.topic_id)}
                aria-label={t("canvas.topic_list.open_aria", { title: tp.title, status: tp.status, neighbors: neighborText })}
              >
                {t("canvas.topic_list.open_button", { title: tp.title })}
              </button>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}

// Centered warm-editorial placeholder shown when a project has zero
// topics — edge case after delete-all or on a freshly-created empty
// project. Sits above the React Flow pane but below the composer, with
// pointer-events: none so users can still pan the canvas underneath.
// CanvasEmptyState removed — product decision: don't
// block the canvas surface when topics are empty. The auto-spawn +
// polling logic moved to a useEffect in ProjectCanvasInner so the
// canvas renders even with zero topics; topics pop in live as the
// orchestrator drafts them, and the OrchestratorChip on the top-bar
// surfaces "Running" state.

// -- helpers --------------------------------------------------------------

// Floating intro card from the planner. Default-EXPANDED whenever the
// canvas opens on a given project — each new project / re-open gets the
// fresh "here's what we mapped" moment. Collapse is per-session only:
// once the user collapses it we respect that for the rest of the page
// load (auto-collapse on outside-click is still there, also per-session).
// Reload or switch project → expanded again, because remembering "user
// once collapsed this" across projects hides useful context.

function PlannerOpeningCard({ body }: { body: string }) {
  // Expanded by default on EVERY mount. Each time the parent swaps body
  // (new project / fresh kickoff) this component remounts and expands
  // again because ProjectCanvas keys its internal state on projectId.
  const [collapsed, setCollapsed] = useState<boolean>(false);
  const cardRef = useRef<HTMLDivElement | null>(null);

  const toggle = useCallback(() => {
    setCollapsed((prev) => !prev);
  }, []);

  // Auto-collapse when the user clicks anywhere outside the card. We use
  // `pointerdown` in the CAPTURE phase so React Flow (which calls
  // stopPropagation on its own pointer handlers to manage selection /
  // drag) can't swallow the event before we see it. Only attach while
  // expanded — no listener cost when collapsed.
  useEffect(() => {
    if (collapsed) return;
    const onPointerDown = (e: PointerEvent) => {
      const el = cardRef.current;
      if (!el) return;
      // Cast through `unknown` because the React Flow `Node` import
      // shadows the DOM Node type at this scope; we want the DOM one.
      if (el.contains(e.target as unknown as globalThis.Node)) return;
      setCollapsed(true);
    };
    document.addEventListener("pointerdown", onPointerDown, true);
    return () =>
      document.removeEventListener("pointerdown", onPointerDown, true);
  }, [collapsed]);

  return (
    <div
      ref={cardRef}
      className={
        "canvas-planner-card" +
        (collapsed ? " canvas-planner-card--collapsed" : "")
      }
    >
      <button
        type="button"
        className="canvas-planner-card__toggle"
        onClick={toggle}
        aria-expanded={!collapsed}
        aria-label={collapsed ? t("canvas.planner_card.expand_aria") : t("canvas.planner_card.collapse_aria")}
      >
        <span className="canvas-planner-card__eyebrow">{t("canvas.planner_card.eyebrow")}</span>
        <span className="canvas-planner-card__chevron" aria-hidden="true">
          {collapsed ? "▸" : "▾"}
        </span>
      </button>
      {!collapsed ? (
        <p className="canvas-planner-card__body">{body}</p>
      ) : null}
    </div>
  );
}

function topicsToNodes(
  topics: Topic[],
  kickoff: KickoffRawResponse | null | undefined,
  onOpenTopic: ((id: string, originRect: DOMRect) => void) | undefined,
  decisionsByTopicId: Map<string, Decision[]> | undefined,
  pendingDeletionSuggestions?: Record<string, TopicDeletionSuggestion>,
  onDismissDeletionSuggestion?: (topicId: string) => void,
  onConfirmDeletion?: (topicId: string) => void,
): Node<TopicNodeData>[] {
  // Kickoff envelope keeps why_this_topic on its response shape but the
  // authoritative copy lives on the topic row itself (topic.metadata
  // .why_this_topic). Read from the topic first; fall back to the
  // kickoff lookup only for pre-metadata topics that pre-date the
  // column write. Previously we read kickoff-only, which dropped the
  // description on every planner-proposed or user-created topic added
  // after kickoff (their title never landed in whyByTitle).
  const whyByTitle = new Map<string, string>();
  for (const kt of kickoff?.topics ?? []) {
    whyByTitle.set(kt.title, kt.why_this_topic);
  }
  const pickWhy = (tp: Topic): string | undefined => {
    const metaWhy = tp.metadata?.why_this_topic;
    if (typeof metaWhy === "string" && metaWhy.trim()) return metaWhy;
    return whyByTitle.get(tp.title);
  };
  return topics.map((tp) => ({
    id: tp.topic_id,
    type: "topic",
    position: { x: tp.position_x, y: tp.position_y },
    data: {
      title: tp.title,
      icon: tp.icon,
      // P1.6 (#066) — thread the topic's color through to TopicNode so the
      // card's `--topic-accent` CSS variable reflects whatever the user
      // picked in the topic-detail color picker. Without this the picker
      // wrote the value to the backend AND updated the drawer's `Color`
      // pill, but the canvas card stayed on the default `var(--ink-3)`
      // because TopicNode reads `data.color` and we weren't passing it
      // here. See TopicNode.tsx:352 for where the value lands in CSS.
      color: tp.color ?? null,
      whyThisTopic: pickWhy(tp),
      // B1.2 — thread full Decision[] (not just statements) so the
      // card can render per-decision provenance dots. The comments module
      // attaches its CommentTargetWrapper + CommentChip to each bullet, reading
      // decision_id directly from each Decision (no separate parallel
      // array — Decision already carries decision_id).
      decisions: decisionsByTopicId?.get(tp.topic_id) ?? [],
      status: tp.status,
      openQuestionCount: 0,
      conflictCount: 0,
      onOpen: onOpenTopic
        ? (rect: DOMRect) => onOpenTopic(tp.topic_id, rect)
        : undefined,
      deletionSuggestion: pendingDeletionSuggestions?.[tp.topic_id] ?? null,
      onDismissDeletionSuggestion: onDismissDeletionSuggestion
        ? () => onDismissDeletionSuggestion(tp.topic_id)
        : undefined,
      onConfirmDeletion: onConfirmDeletion
        ? () => onConfirmDeletion(tp.topic_id)
        : undefined,
    },
  }));
}

/**
 * Pick the source and target handle IDs that route an edge through the
 * SIDE of each card that's closest to the other card.
 *
 * The card exposes four handles — l-source, l-target, r-source, r-target.
 * If the target's horizontal center is to the right of the source's,
 * the edge exits the source's right side and enters the target's left;
 * otherwise the reverse. This prevents lines from running backward out
 * of a card's left edge when the target is clearly to the right.
 */
function pickClosestHandles(
  sourceX: number | null,
  targetX: number | null,
): { sourceHandle: string; targetHandle: string } {
  // Fallback when we don't know positions: right → left (the common case
  // after a left-to-right dagre layout).
  if (sourceX === null || targetX === null) {
    return { sourceHandle: "r-source", targetHandle: "l-target" };
  }
  const targetIsToTheRight = targetX >= sourceX;
  return targetIsToTheRight
    ? { sourceHandle: "r-source", targetHandle: "l-target" }
    : { sourceHandle: "l-source", targetHandle: "r-target" };
}

function relationshipsToEdges(
  relationships: Relationship[],
  positionById: Map<string, { x: number; y: number }>,
  // L5d — optional toolbar callbacks. When provided, each edge's
  // `data` carries them so the custom RelationshipEdge component can
  // render the floating Edit/Delete pill on selection. Optional
  // because tests + the initial `useMemo`-driven render call this
  // before the parent's callbacks resolve; the toolbar simply doesn't
  // render until the next pass that supplies them.
  toolbarCallbacks?: {
    onEditEdge?: (edgeId: string) => void;
    onDeleteEdge?: (edgeId: string) => void;
  },
): Edge[] {
  // Pre-compute per-node fan-in / fan-out so we can give each edge's
  // label a vertical offset that's distinct from its siblings. Multiple
  // edges meeting at the same node produce labels at the same midpoint
  // by default; spreading them by ~22px per slot keeps the labels from
  // stacking on top of each other.
  const incomingIds = new Map<string, string[]>();
  const outgoingIds = new Map<string, string[]>();
  for (const r of relationships) {
    const inc = incomingIds.get(r.target_topic_id) ?? [];
    inc.push(r.relationship_id);
    incomingIds.set(r.target_topic_id, inc);
    const out = outgoingIds.get(r.source_topic_id) ?? [];
    out.push(r.relationship_id);
    outgoingIds.set(r.source_topic_id, out);
  }

  const baseStyle = dottedEdgeStyle();

  return relationships.map((r) => {
    const src = positionById.get(r.source_topic_id);
    const tgt = positionById.get(r.target_topic_id);
    const srcCenterX =
      src !== undefined ? src.x + TOPIC_CARD_WIDTH / 2 : null;
    const tgtCenterX =
      tgt !== undefined ? tgt.x + TOPIC_CARD_WIDTH / 2 : null;
    const { sourceHandle, targetHandle } = pickClosestHandles(
      srcCenterX,
      tgtCenterX,
    );

    // Pick the bigger of fan-in / fan-out at this edge's endpoints. If
    // 3 edges share a target, they get offsets -22, 0, +22. If 5 share
    // a source, -44, -22, 0, 22, 44. Whichever endpoint has more siblings
    // wins — that's where the visual collision is worst.
    const incoming = incomingIds.get(r.target_topic_id) ?? [];
    const outgoing = outgoingIds.get(r.source_topic_id) ?? [];
    const useIncoming = incoming.length >= outgoing.length;
    const group = useIncoming ? incoming : outgoing;
    const idx = group.indexOf(r.relationship_id);
    const labelOffsetY =
      group.length > 1 ? (idx - (group.length - 1) / 2) * 22 : 0;

    return {
      id: r.relationship_id,
      source: r.source_topic_id,
      target: r.target_topic_id,
      sourceHandle,
      targetHandle,
      // Fallback label if the LLM didn't provide one (legacy rows / edge
      // cases). Every edge should read as a concrete dependency — if all
      // we have is "these two are related," show that explicitly.
      label: r.label && r.label.trim() ? r.label : "relates to",
      // Spread baseStyle FIRST so its keys (animated, markerEnd, style,
      // labelStyle, labelBgStyle, type: "smoothstep") are present.
      // Then override `type` to our custom edge component — the
      // RelationshipEdge renders the smoothstep path internally via
      // getSmoothStepPath, so visuals stay identical. The custom edge
      // also handles the labelOffsetY by adjusting the label's `y`
      // coordinate directly (passed via `data` below) — no CSS
      // transform needed on labelStyle/labelBgStyle anymore.
      ...baseStyle,
      type: "relationship" as const,
      data: {
        labelOffsetY,
        onEditEdge: toolbarCallbacks?.onEditEdge,
        onDeleteEdge: toolbarCallbacks?.onDeleteEdge,
      } satisfies RelationshipEdgeData,
    };
  });
}

/**
 * Dotted, hand-drawn-feeling edge style. Smoothstep routing keeps lines
 * clean even when cards are stacked in rows — no loops or diagonals
 * running through other cards.
 */
function dottedEdgeStyle() {
  return {
    type: "smoothstep" as const,
    animated: false,
    markerEnd: {
      type: MarkerType.ArrowClosed,
      color: "var(--ink-3, #7A6F64)",
      width: 14,
      height: 14,
    },
    style: {
      stroke: "var(--ink-3, #7A6F64)",
      strokeWidth: 1.5,
      strokeDasharray: "4 5",
    },
    // Labels sit on the edge path. Serif italic for the editorial feel,
    // mid-ink for solid contrast against the paper, and a nearly-opaque
    // paper background so text reads cleanly where the dashed line would
    // otherwise run through the glyphs.
    labelStyle: {
      fontFamily: "var(--ff-serif)",
      fontSize: 13,
      fontStyle: "italic",
      letterSpacing: "0",
      fill: "var(--ink-2, #4A413A)",
    },
    labelBgStyle: {
      fill: "var(--paper, #F5F0E6)",
      fillOpacity: 0.96,
    },
    labelBgPadding: [6, 10] as [number, number],
    labelBgBorderRadius: 6,
    labelShowBg: true,
  };
}

function isPositionEnd(c: NodeChange): c is NodePositionChange {
  return c.type === "position" && c.dragging === false;
}
