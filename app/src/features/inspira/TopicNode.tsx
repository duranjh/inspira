// Topic card rendered as a React Flow node.
//
// Minimal hi-fi — matches the warm editorial aesthetic from the HTML mocks:
// cream paper card, serif display title, ink border, key decisions as bullets.
// Relationship handles live only on left/right edges per the design rules.
//
// Accessibility notes:
// - The card root is a `button` role with a keyboard handler so Enter/Space
//   opens the topic just like double-click. React Flow's own pan/drag layer
//   sits above this on pointer events, so keyboard users get a path in via
//   focus + Enter without fighting the canvas.
// - The status dot is both color-coded and shape-coded via the CSS
//   `data-status` attribute (hollow / half / full), so users who can't
//   distinguish hue still see the state.
// - Icon glyphs are decorative and marked aria-hidden — the accessible
//   name comes from the visually-hidden status description + title.

import { useCallback, useEffect, useRef, useState } from "react";
import { Handle, Position, type NodeProps } from "reactflow";
import { t } from "../../i18n";
import {
  api,
  type Decision,
  type TopicColor,
  type TopicDeletionSuggestion,
} from "./api";
import { MultiAgentDots } from "./chrome/MultiAgentDots";
import { ProvenanceBadge } from "./chrome/ProvenanceBadge";
import { useRealtimeContext } from "./RealtimeContext";
import { toast } from "../../components/ToastProvider";
import { RenameProjectDialog } from "../../components/dialogs";
import { CommentTargetWrapper, CommentChip } from "./comments";

// Map a color slug to the theme CSS variable name. Kept as a lookup (not
// a string interpolation) so the set of accepted slugs is enforced by
// TypeScript — a typo surfaces at compile time instead of silently
// rendering with no border accent.
const TOPIC_COLOR_VAR: Record<TopicColor, string> = {
  sage: "var(--sage)",
  rust: "var(--rust)",
  gold: "var(--gold)",
  ink: "var(--ink)",
  paper: "var(--paper-edge)",
};

// Handle style: invisible by default (just a small hit target), sage-tinted
// dot on node hover so the user sees where to drag from. Both sides are
// source+target-capable so a user can drag from either edge to either edge.
const handleBase: React.CSSProperties = {
  width: 10,
  height: 10,
  background: "var(--sage, #6A9A7A)",
  border: "2px solid var(--paper-lifted, #fbf7ee)",
  opacity: 0,
  transition: "opacity 160ms ease",
};

export type TopicNodeData = {
  title: string;
  icon: string;
  whyThisTopic?: string;
  // B1.2 — full Decision objects (not just statements) so each
  // bullet can render a per-decision provenance badge (gold dot for
  // AI-proposed, half-fill for AI-seeded-then-human-edited). The comments
  // module attaches its CommentTargetWrapper + CommentChip to the same row,
  // reading decision_id directly from each Decision (no separate
  // parallel array needed — the Decision objects already carry it).
  decisions: Decision[];
  status: "empty" | "in_progress" | "fleshed_out";
  openQuestionCount?: number;
  conflictCount?: number;
  // Receives the card's current viewport rect so the detail view can
  // morph open from this exact position. The rect is captured at
  // double-click time — by capture, not by ref — so we always have
  // the post-pan/zoom screen position.
  onOpen?: (originRect: DOMRect) => void;
  // Planner-suggested deletion — non-null when the planner thinks this
  // topic is moot given a recent decision. The user confirms or dismisses.
  deletionSuggestion?: TopicDeletionSuggestion | null;
  onDismissDeletionSuggestion?: () => void;
  onConfirmDeletion?: () => void;
  // Optional color tag for visual grouping on the canvas. Resolves to a
  // theme CSS variable through ``TOPIC_COLOR_VAR`` so dark mode flips
  // automatically. ``null`` / ``undefined`` both mean "no color" and the
  // node falls back to the default ink-3 accent.
  color?: TopicColor | null;
};

function getStatusLabel(status: TopicNodeData["status"]): string {
  switch (status) {
    case "empty": return t("topic_node.status.empty");
    case "in_progress": return t("topic_node.status.in_progress");
    case "fleshed_out": return t("topic_node.status.fleshed_out");
  }
}

export function TopicNode({ data, id }: NodeProps<TopicNodeData>) {
  const hasOpenQs = (data.openQuestionCount ?? 0) > 0;
  const hasConflicts = (data.conflictCount ?? 0) > 0;
  const [deletionDialogOpen, setDeletionDialogOpen] = useState(false);

  // L4 / #035 — kebab menu (Rename / Delete) on the topic card.
  // Mirrors the ProjectCard pattern. Dispatches existing window
  // events so the parent (InspiraApp) handles the actual flow:
  //   - Rename: api.updateTopic + inspira:topics-changed
  //   - Delete: inspira:topic-delete-request → InspiraApp opens
  //     its DeleteConfirmDialog
  const [menuOpen, setMenuOpen] = useState(false);
  const [renameOpen, setRenameOpen] = useState(false);
  const menuTriggerRef = useRef<HTMLButtonElement | null>(null);
  const menuRef = useRef<HTMLDivElement | null>(null);

  // Outside-click + Escape close. Only attached when menu is open.
  useEffect(() => {
    if (!menuOpen) return;
    const onDocClick = (e: MouseEvent) => {
      const target = e.target as Node | null;
      if (!target) return;
      if (menuRef.current?.contains(target)) return;
      if (menuTriggerRef.current?.contains(target)) return;
      setMenuOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setMenuOpen(false);
        menuTriggerRef.current?.focus();
      }
    };
    document.addEventListener("mousedown", onDocClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDocClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [menuOpen]);

  const handleMenuRenameClick = useCallback(() => {
    setMenuOpen(false);
    setRenameOpen(true);
  }, []);

  const handleRenameSubmit = useCallback(
    async (nextTitle: string) => {
      const trimmed = nextTitle.trim();
      if (!trimmed) {
        // Defensive — RenameProjectDialog disables submit on empty,
        // but make the contract explicit.
        return;
      }
      try {
        await api.updateTopic(id, { title: trimmed });
        toast.success(t("topic_node.rename_success"));
        // Tell the canvas to refetch so the title updates without
        // a full project reload. Same plumbing as the topic-color
        // sync path.
        if (typeof window !== "undefined") {
          window.dispatchEvent(new CustomEvent("inspira:topics-changed"));
        }
        setRenameOpen(false);
      } catch (err) {
        console.error("[Inspira] failed to rename topic", err);
        toast.error(t("topic_node.rename_failed"));
        // Re-throw so RenameProjectDialog paints an inline error.
        throw err;
      }
    },
    [id],
  );

  const handleMenuDeleteClick = useCallback(() => {
    setMenuOpen(false);
    if (typeof window === "undefined") return;
    // InspiraApp listens for this and opens its DeleteConfirmDialog.
    // Same event the canvas-action delete-key path already uses.
    window.dispatchEvent(
      new CustomEvent("inspira:topic-delete-request", {
        detail: { topicId: id, title: data.title },
      }),
    );
  }, [id, data.title]);

  // Real-time remote lock: when another user is in this topic's Q&A
  // drawer, their color bleeds onto the card via a subtle pulsing
  // glow. Owner-is-me locks render normally (you don't glow at
  // yourself). When the realtime context is disconnected or no lock
  // exists, this is a no-op.
  const rt = useRealtimeContext();
  const remoteLock = rt.locks[id];
  const isLockedByOther =
    !!remoteLock && remoteLock.ownerSessionId !== rt.mySessionId;

  // Capture the card's CURRENT viewport rect so the detail view can morph
  // open from this exact pixel position. We read it here, not later, so
  // any in-progress canvas pan/zoom is reflected in the rect handed off.
  const handleOpen = useCallback(
    (
      event:
        | React.MouseEvent<HTMLDivElement>
        | React.KeyboardEvent<HTMLDivElement>,
    ) => {
      if (!data.onOpen) return;
      data.onOpen(event.currentTarget.getBoundingClientRect());
    },
    [data],
  );

  const handleKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLDivElement>) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        handleOpen(event);
      }
    },
    [handleOpen],
  );

  // T5.4: single-click (or tap) opens the drawer on every pointer
  // type — desktop mouse + touch + pen. Previously desktop required
  // a double-click which conflicted with the user's mental model
  // ("matches mobile"). Drag-to-pan keeps working because we only
  // count it as a "tap" when pointerUp lands within TAP_SLOP_PX of
  // pointerDown — anything bigger is a drag and React Flow handles
  // the pan/move as before.
  const tapStartRef = useRef<{ x: number; y: number; t: number } | null>(null);
  const TAP_SLOP_PX = 8;
  const TAP_MAX_MS = 500;
  const handlePointerDown = useCallback(
    (event: React.PointerEvent<HTMLDivElement>) => {
      tapStartRef.current = {
        x: event.clientX,
        y: event.clientY,
        t: Date.now(),
      };
    },
    [],
  );
  const handlePointerUp = useCallback(
    (event: React.PointerEvent<HTMLDivElement>) => {
      const start = tapStartRef.current;
      tapStartRef.current = null;
      if (!start) return;
      const dx = Math.abs(event.clientX - start.x);
      const dy = Math.abs(event.clientY - start.y);
      const dt = Date.now() - start.t;
      if (dx <= TAP_SLOP_PX && dy <= TAP_SLOP_PX && dt <= TAP_MAX_MS) {
        handleOpen(event as unknown as React.MouseEvent<HTMLDivElement>);
      }
    },
    [handleOpen],
  );

  const statusText = getStatusLabel(data.status);
  const openQsPart = hasOpenQs
    ? " " + (data.openQuestionCount === 1
      ? t("topic_node.open_questions_one", { count: String(data.openQuestionCount) })
      : t("topic_node.open_questions_many", { count: String(data.openQuestionCount) }))
    : "";
  const conflictsPart = hasConflicts
    ? " " + (data.conflictCount === 1
      ? t("topic_node.conflicts_one", { count: String(data.conflictCount) })
      : t("topic_node.conflicts_many", { count: String(data.conflictCount) }))
    : "";
  const accessibleName = `${data.title}. ${t("topic_node.status_prefix", { status: statusText })}${openQsPart}${conflictsPart}`;

  return (
    <>
    {deletionDialogOpen && data.deletionSuggestion && (
      <div
        className="topic-node__deletion-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="dsd-title"
        style={{
          position: "fixed",
          inset: 0,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          zIndex: 9000,
          background: "rgba(40,30,20,0.35)",
        }}
        onClick={(e) => { if (e.target === e.currentTarget) setDeletionDialogOpen(false); }}
      >
        <div style={{
          background: "var(--paper-lifted, #fbf7ee)",
          border: "1px solid #2a1f0f",
          borderRadius: 6,
          padding: "24px 28px",
          maxWidth: 420,
          width: "90vw",
          fontFamily: "inherit",
        }}>
          <h2 id="dsd-title" style={{ margin: "0 0 8px", fontSize: 16, fontWeight: 700 }}>
            {t("delete_suggestion_dialog.title")}
          </h2>
          <p style={{ margin: "0 0 4px", fontSize: 12, color: "#7a6a4a", textTransform: "uppercase", letterSpacing: "0.04em" }}>
            {t("delete_suggestion_dialog.reason_label")}
          </p>
          <p style={{ margin: "0 0 20px", fontSize: 14, color: "#2a1f0f" }}>
            {data.deletionSuggestion.reason}
          </p>
          <div style={{ display: "flex", gap: 10, justifyContent: "flex-end" }}>
            <button
              style={{
                padding: "8px 16px",
                border: "1px solid #2a1f0f",
                borderRadius: 4,
                background: "transparent",
                cursor: "pointer",
                fontSize: 13,
              }}
              onClick={() => {
                setDeletionDialogOpen(false);
                data.onDismissDeletionSuggestion?.();
              }}
            >
              {t("delete_suggestion_dialog.dismiss")}
            </button>
            <button
              style={{
                padding: "8px 16px",
                border: "none",
                borderRadius: 4,
                background: "#8B3A2A",
                color: "#fff",
                cursor: "pointer",
                fontSize: 13,
                fontWeight: 600,
              }}
              onClick={() => {
                setDeletionDialogOpen(false);
                data.onConfirmDeletion?.();
              }}
            >
              {t("delete_suggestion_dialog.accept")}
            </button>
          </div>
        </div>
      </div>
    )}
    <div
      className={
        "topic-node" +
        (isLockedByOther ? " topic-node--locked-remote" : "")
      }
      role="button"
      tabIndex={0}
      aria-label={
        isLockedByOther && remoteLock
          ? `${accessibleName} — ${remoteLock.ownerDisplayName} is answering`
          : accessibleName
      }
      onDoubleClick={handleOpen}
      onKeyDown={handleKeyDown}
      onPointerDown={handlePointerDown}
      onPointerUp={handlePointerUp}
      // Color accent: a thin left bar driven by a CSS variable. When no
      // color is set we fall back to ``--ink-3`` so the style hook is
      // inert (same hue as unemphasized chrome, user sees no tag).
      // ``--topic-accent`` is also exposed as a custom property on the
      // element so any child rule (e.g. a future corner dot) can reuse it.
      // --lock-color is also set when a remote user holds this topic's
      // focus lock; the .topic-node--locked-remote rule uses it for
      // the pulsing glow effect.
      style={{
        ["--topic-accent" as string]: data.color ? TOPIC_COLOR_VAR[data.color] : "var(--ink-3)",
        borderLeft: data.color ? "3px solid var(--topic-accent)" : undefined,
        ...(isLockedByOther && remoteLock
          ? { ["--lock-color" as string]: remoteLock.ownerColor }
          : {}),
      }}
    >
      {/* Planner deletion-suggestion banner — rust strip at top of card. */}
      {data.deletionSuggestion && (
        <div
          className="topic-node__deletion-banner"
          style={{
            background: "#8B3A2A",
            color: "#fff",
            fontSize: 11,
            padding: "4px 8px",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            gap: 6,
            borderRadius: "4px 4px 0 0",
            marginBottom: 4,
            cursor: "default",
          }}
          onClick={(e) => { e.stopPropagation(); setDeletionDialogOpen(true); }}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") {
              e.stopPropagation();
              setDeletionDialogOpen(true);
            }
          }}
          role="button"
          tabIndex={0}
          aria-label={t("topic_node.deletion_suggested_banner")}
        >
          <span>{t("topic_node.deletion_suggested_banner")}</span>
          <button
            style={{
              background: "none",
              border: "none",
              color: "#fff",
              cursor: "pointer",
              fontSize: 13,
              padding: 0,
              lineHeight: 1,
            }}
            aria-label={t("topic_node.deletion_suggested_dismiss_aria")}
            onClick={(e) => {
              e.stopPropagation();
              data.onDismissDeletionSuggestion?.();
            }}
          >
            ×
          </button>
        </div>
      )}
      {/* Left + right only — never top or bottom per design rules. Each
          edge hosts a SOURCE and a TARGET handle layered on top of each
          other so the user can drag from either side to either side. */}
      <Handle
        id="l-target"
        type="target"
        position={Position.Left}
        style={handleBase}
        className="topic-node__handle"
      />
      <Handle
        id="l-source"
        type="source"
        position={Position.Left}
        style={handleBase}
        className="topic-node__handle"
      />

      {/* V5 layout (Canvas Review.html .tc__hd): icon at left, title
          flexes, status dot pinned right. The dot itself conveys
          empty / in_progress / fleshed_out via its data-status
          attribute (see App.css), so the legacy fleshed_out checkmark
          glyph is gone — redundant and noisy. */}
      <div className="topic-node__header">
        <span className="topic-node__icon" aria-hidden="true">
          {iconGlyph(data.icon)}
        </span>
        <span className="topic-node__title">{data.title}</span>
        <span
          className="topic-node__status-dot"
          data-status={data.status}
          aria-label={getStatusLabel(data.status)}
          title={getStatusLabel(data.status)}
        />
      </div>

      {/* V5 .tc__dots: three sage bouncy dots on their own row when a
          sub-agent is actively working on this topic. Self-gated by
          MultiAgentDots — renders nothing when idle. */}
      <MultiAgentDots topicId={id} />

      {data.decisions.length > 0 ? (
        <ul className="topic-node__decisions">
          {data.decisions.slice(0, 7).map((d) => (
            // Combined: per-decision ProvenanceBadge AND
            // CommentTargetWrapper + CommentChip. decision_id comes
            // straight off the Decision object (no separate parallel
            // array). The selection hook reads highlights inside the
            // wrapper; the chip indicator anchors the comment thread.
            <li key={d.decision_id} style={{ position: "relative" }}>
              <CommentTargetWrapper kind="decision" id={d.decision_id}>
                <ProvenanceBadge decision={d} />
                {d.statement}
              </CommentTargetWrapper>
              <CommentChip target={{ kind: "decision", id: d.decision_id }} />
            </li>
          ))}
          {data.decisions.length > 7 ? (
            <li className="topic-node__more">{t("topic_node.more_decisions", { count: String(data.decisions.length - 7) })}</li>
          ) : null}
        </ul>
      ) : data.whyThisTopic ? (
        <p className="topic-node__why">{data.whyThisTopic}</p>
      ) : null}

      {/* V5 .tc__ft: footer row with Q-open badge + conflict badge +
          decision count + spacer + (timestamp slot, deferred until
          TopicNodeData carries updated_at). Replaces the legacy
          .topic-node__badges row so every card has a consistent
          footer rhythm. */}
      {(hasOpenQs || hasConflicts || data.decisions.length > 0) && (
        <div className="topic-node__footer">
          {hasOpenQs ? (
            <span
              className="topic-node__badge"
              title={t("topic_node.open_questions_title")}
              aria-label={data.openQuestionCount === 1
                ? t("topic_node.open_questions_aria_one", { count: String(data.openQuestionCount) })
                : t("topic_node.open_questions_aria_many", { count: String(data.openQuestionCount) })}
            >
              <span aria-hidden="true">◦ </span>
              {data.openQuestionCount}
            </span>
          ) : null}
          {hasConflicts ? (
            <span
              className="topic-node__badge topic-node__badge--conflict"
              title={t("topic_node.conflicts_title")}
              aria-label={data.conflictCount === 1
                ? t("topic_node.conflicts_aria_one", { count: String(data.conflictCount) })
                : t("topic_node.conflicts_aria_many", { count: String(data.conflictCount) })}
            >
              <span aria-hidden="true">⚑ </span>
              {data.conflictCount}
            </span>
          ) : null}
          <span className="topic-node__footer-spacer" />
          {data.decisions.length > 0 ? (
            <span className="topic-node__decision-count">
              {data.decisions.length === 1
                ? "1 decision"
                : `${data.decisions.length} decisions`}
            </span>
          ) : null}
        </div>
      )}

      {/* L4 / #035 — kebab menu in the top-right corner. Rendered
          INSIDE the topic-node so the absolute positioning anchors
          to the card. Pointer events on the trigger AND the menu
          stop propagation so they don't reach the card's
          tap-to-open handler. */}
      <div
        className="topic-node__menu-wrap"
        onPointerDown={(e) => e.stopPropagation()}
        onPointerUp={(e) => e.stopPropagation()}
        onClick={(e) => e.stopPropagation()}
        onDoubleClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => e.stopPropagation()}
      >
        <button
          ref={menuTriggerRef}
          type="button"
          className="topic-node__menu-trigger"
          aria-haspopup="menu"
          aria-expanded={menuOpen}
          aria-label={t("topic_node.options_aria", { title: data.title })}
          onClick={(e) => {
            e.stopPropagation();
            setMenuOpen((v) => !v);
          }}
        >
          <span aria-hidden="true">{"⋯"}</span>
        </button>
        {menuOpen ? (
          <div ref={menuRef} className="topic-node__menu" role="menu">
            <button
              type="button"
              role="menuitem"
              className="topic-node__menu-item"
              onClick={handleMenuRenameClick}
            >
              {t("topic_node.rename")}
            </button>
            <button
              type="button"
              role="menuitem"
              className="topic-node__menu-item topic-node__menu-item--danger"
              onClick={handleMenuDeleteClick}
            >
              {t("topic_node.delete")}
            </button>
          </div>
        ) : null}
      </div>

      <Handle
        id="r-source"
        type="source"
        position={Position.Right}
        style={handleBase}
        className="topic-node__handle"
      />
      <Handle
        id="r-target"
        type="target"
        position={Position.Right}
        style={handleBase}
        className="topic-node__handle"
      />
    </div>
    <RenameProjectDialog
      open={renameOpen}
      currentTitle={data.title}
      onSubmit={handleRenameSubmit}
      onClose={() => setRenameOpen(false)}
      titleOverride={t("topic_node.rename_dialog_title")}
      labelOverride={t("topic_node.rename_dialog_label")}
      hintOverride={t("topic_node.rename_dialog_hint")}
    />
    </>
  );
}

// Tiny curated icon set — unicode glyphs for now. Swap for a real SVG icon
// registry once the design system is ported.
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
