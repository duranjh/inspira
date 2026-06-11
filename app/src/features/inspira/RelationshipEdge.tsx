// Inspira — custom edge component (L5d, #037).
//
// Wraps React Flow's smoothstep edge path so we can:
//   1. Continue to render the dotted-line / italic-label look the canvas
//      already had (preserves the visual language — no behavior change
//      for users who never select an edge).
//   2. Render a small Edit / Delete pill near the edge's midpoint when
//      the edge is `selected`. This is the "discoverability" affordance
//      that ADR-001 §3.4 calls for: the existing double-click-label and
//      Delete-key paths still work, but new users now see the controls
//      without having to guess them.
//
// The pill renders via React Flow's `EdgeLabelRenderer` — that portals
// children into a fixed overlay layer above the SVG, with a CSS
// transform that places them at the edge's labelX/labelY (adjusted
// here so the pill sits BELOW the existing label rather than on top).
//
// Why a separate edge type rather than a global overlay?
//   - React Flow gives us labelX/labelY for free per edge — the
//     overlay approach would have to recompute path geometry from
//     getBoundingClientRect on each render, which is fragile and
//     re-runs constantly during pan/zoom.
//   - Per-edge selection state is already passed via the `selected`
//     prop, so toggling visibility is a simple boolean.
//
// Performance:
//   - When no edge is selected, the toolbar renders nothing (returns
//     null inside the EdgeLabelRenderer block) — zero DOM cost on the
//     not-selected edges.
//   - When one edge IS selected, the toolbar renders one absolutely-
//     positioned div + 2 buttons. Negligible.

import { useCallback } from "react";
import {
  BaseEdge,
  EdgeLabelRenderer,
  EdgeText,
  getSmoothStepPath,
  type EdgeProps,
} from "reactflow";

import { t } from "../../i18n";

export type RelationshipEdgeData = {
  /** Fired when the user clicks Edit on the floating toolbar. The
   * parent (ProjectCanvas) opens the RelationshipLabelDialog with the
   * edge's current label pre-filled. */
  onEditEdge?: (edgeId: string) => void;
  /** Fired when the user clicks Delete on the floating toolbar. The
   * parent runs the optimistic-removal-then-persist flow. */
  onDeleteEdge?: (edgeId: string) => void;
  /** Vertical offset for the label relative to the edge midpoint, used
   * by the existing collision-spreader logic when multiple edges share
   * a node. The toolbar sits 26px below the (offset) label. */
  labelOffsetY?: number;
};

export function RelationshipEdge(props: EdgeProps<RelationshipEdgeData>) {
  const {
    id,
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourcePosition,
    targetPosition,
    label,
    labelStyle,
    labelBgStyle,
    labelBgPadding,
    labelBgBorderRadius,
    style,
    markerEnd,
    selected,
    data,
  } = props;

  // Smoothstep path geometry — exact same routing the default
  // SmoothStepEdge would compute. We render it via BaseEdge so React
  // Flow still owns the SVG element + selection / hover wiring.
  const [edgePath, labelX, labelY] = getSmoothStepPath({
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourcePosition,
    targetPosition,
  });

  const offsetY = data?.labelOffsetY ?? 0;

  // Memoize the click handlers so the inner buttons don't re-bind on
  // every render of an unrelated parent state change.
  const handleEdit = useCallback(() => {
    data?.onEditEdge?.(id);
  }, [data, id]);
  const handleDelete = useCallback(() => {
    data?.onDeleteEdge?.(id);
  }, [data, id]);

  return (
    <>
      <BaseEdge id={id} path={edgePath} style={style} markerEnd={markerEnd} />
      {/* Label — match what the default SmoothStepEdge would render so
          the warm-italic look is preserved. We pass the existing
          labelStyle / labelBgStyle through verbatim. */}
      {label ? (
        <EdgeText
          x={labelX}
          y={labelY + offsetY}
          label={label}
          labelStyle={labelStyle}
          labelShowBg
          labelBgStyle={labelBgStyle}
          labelBgPadding={labelBgPadding ?? [4, 6]}
          labelBgBorderRadius={labelBgBorderRadius ?? 4}
        />
      ) : null}
      {/* Toolbar — only renders when `selected === true` AND at least
          one handler is wired. Both handlers are usually present
          together (the parent passes them as a pair); the OR keeps the
          component robust if a caller ever passes only one. */}
      {selected && (data?.onEditEdge || data?.onDeleteEdge) ? (
        <EdgeLabelRenderer>
          <div
            style={{
              position: "absolute",
              transform: `translate(-50%, 0%) translate(${labelX}px, ${
                labelY + offsetY + 14
              }px)`,
              pointerEvents: "all",
              display: "flex",
              gap: 4,
              padding: "3px 4px",
              background: "var(--paper-lifted, #f0eadc)",
              border: "1px solid var(--paper-edge, #d8cfb6)",
              borderRadius: 999,
              boxShadow: "0 2px 6px rgba(0, 0, 0, 0.1)",
              fontFamily: "var(--ff-mono, monospace)",
              fontSize: 11,
              zIndex: 10,
              // Keeps focus-trap + keyboard nav happy when the toolbar
              // is the active surface.
              userSelect: "none",
            }}
            // The toolbar lives inside React Flow's overlay tree but
            // doesn't represent a node — stop pointer events from
            // bubbling and triggering pan/zoom on the canvas behind.
            onMouseDown={(e) => e.stopPropagation()}
            onClick={(e) => e.stopPropagation()}
            role="toolbar"
            aria-label={t("edge_toolbar.aria")}
          >
            {data?.onEditEdge ? (
              <button
                type="button"
                onClick={handleEdit}
                style={toolbarButtonStyle}
                title={t("edge_toolbar.edit_title")}
              >
                {t("edge_toolbar.edit")}
              </button>
            ) : null}
            {data?.onDeleteEdge ? (
              <button
                type="button"
                onClick={handleDelete}
                style={{ ...toolbarButtonStyle, color: "var(--rust, #9a4e38)" }}
                title={t("edge_toolbar.delete_title")}
              >
                {t("edge_toolbar.delete")}
              </button>
            ) : null}
          </div>
        </EdgeLabelRenderer>
      ) : null}
    </>
  );
}

const toolbarButtonStyle: React.CSSProperties = {
  border: "none",
  background: "transparent",
  cursor: "pointer",
  padding: "2px 8px",
  borderRadius: 999,
  color: "var(--ink-2, #423a2d)",
  fontFamily: "inherit",
  fontSize: "inherit",
  fontWeight: 500,
};
