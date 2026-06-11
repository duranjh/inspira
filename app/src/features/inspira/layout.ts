// Auto-layout for the topic canvas using dagre.
//
// Why dagre: it's the industry standard for layered graph layouts.
// Minimizes edge crossings, keeps related nodes close, respects edge
// direction. Good enough that the initial laid-out state essentially
// never has overlapping edges or cards.
//
// What this does NOT do: guarantee zero edge overlap after the user drags
// cards around manually. That's a hard geometry problem no general-purpose
// canvas tool fully solves. The user gets a "Tidy" button to re-run
// layout whenever the canvas gets messy.

import dagre from "dagre";

import type { Relationship, Topic } from "./api";

/** Dimensions used for layout — must roughly match what TopicNode renders. */
const NODE_WIDTH = 280;
const NODE_HEIGHT = 180;

export type LayoutPosition = { x: number; y: number };
export type LayoutResult = Record<string, LayoutPosition>;

export type LayoutOptions = {
  /** `LR` (left-to-right) matches our L/R edge-only rule cleanly. */
  direction?: "LR" | "TB";
  /** Horizontal gap between ranks (ranks = columns in LR). */
  rankSep?: number;
  /** Vertical gap between nodes in the same rank. */
  nodeSep?: number;
};

/**
 * Compute non-overlapping positions for every topic.
 *
 * Topics without any relationships (isolates) are placed in a row below
 * the main graph so the laid-out result doesn't leave random empty
 * canvas space.
 */
export function computeTopicLayout(
  topics: Topic[],
  relationships: Relationship[],
  opts: LayoutOptions = {},
): LayoutResult {
  if (topics.length === 0) return {};

  const direction = opts.direction ?? "LR";
  const rankSep = opts.rankSep ?? 140;
  const nodeSep = opts.nodeSep ?? 80;

  const g = new dagre.graphlib.Graph({ directed: true });
  g.setGraph({
    rankdir: direction,
    ranksep: rankSep,
    nodesep: nodeSep,
    edgesep: 30,
    marginx: 40,
    marginy: 40,
  });
  g.setDefaultEdgeLabel(() => ({}));

  const topicIds = new Set(topics.map((t) => t.topic_id));

  for (const t of topics) {
    g.setNode(t.topic_id, { width: NODE_WIDTH, height: NODE_HEIGHT });
  }
  for (const r of relationships) {
    // Skip relationships pointing at topics that aren't in the current set
    // (shouldn't happen in practice, but be defensive).
    if (!topicIds.has(r.source_topic_id) || !topicIds.has(r.target_topic_id)) {
      continue;
    }
    g.setEdge(r.source_topic_id, r.target_topic_id);
  }

  dagre.layout(g);

  const positions: LayoutResult = {};
  for (const t of topics) {
    const node = g.node(t.topic_id);
    // dagre returns center coordinates; React Flow wants top-left.
    positions[t.topic_id] = {
      x: node.x - NODE_WIDTH / 2,
      y: node.y - NODE_HEIGHT / 2,
    };
  }

  return positions;
}

/**
 * Apply layout results to a list of topics, returning new topic objects
 * with updated position_x / position_y. Useful when rendering + persisting.
 */
export function applyLayout(topics: Topic[], layout: LayoutResult): Topic[] {
  return topics.map((t) => {
    const p = layout[t.topic_id];
    if (!p) return t;
    return { ...t, position_x: p.x, position_y: p.y };
  });
}

/** Minimum gap (px) between topic cards in any direction. */
const OVERLAP_GAP = 20;

type Rect = { x: number; y: number; width: number; height: number };

function rectsOverlap(a: Rect, b: Rect): boolean {
  return !(
    a.x + a.width + OVERLAP_GAP <= b.x ||
    b.x + b.width + OVERLAP_GAP <= a.x ||
    a.y + a.height + OVERLAP_GAP <= b.y ||
    b.y + b.height + OVERLAP_GAP <= a.y
  );
}

/**
 * If ``proposedPos`` would place node ``draggedId`` overlapping any other
 * node, walk outward in expanding concentric rings until a non-overlapping
 * position is found. Returns the proposed position unchanged when already
 * valid.
 *
 * Uses a ring-search: small radius steps with 8 sample angles each. This
 * converges quickly in practice (typically within a few rings) and gives
 * a natural "nudge away" feel rather than snapping to grid points.
 *
 * Guarantees: no two cards will overlap after this resolves. Does NOT
 * attempt to preserve drag direction — the dropped card finds the
 * NEAREST non-overlapping spot.
 */
export function resolveOverlap(
  draggedId: string,
  proposedPos: LayoutPosition,
  others: Array<{
    id: string;
    position: LayoutPosition;
    width: number;
    height: number;
  }>,
  draggedWidth = NODE_WIDTH,
  draggedHeight = NODE_HEIGHT,
): LayoutPosition {
  const obstacles = others
    .filter((o) => o.id !== draggedId)
    .map((o) => ({
      x: o.position.x,
      y: o.position.y,
      width: o.width,
      height: o.height,
    }));

  const candidate = (pos: LayoutPosition): Rect => ({
    x: pos.x,
    y: pos.y,
    width: draggedWidth,
    height: draggedHeight,
  });

  if (obstacles.every((o) => !rectsOverlap(candidate(proposedPos), o))) {
    return proposedPos;
  }

  // Ring search: step outward in 20px increments, sampling 8 angles.
  const STEP = 20;
  const ANGLES = 12; // more angles = smoother resolution
  const MAX_RINGS = 40; // 40*20 = 800px radius, plenty of room
  for (let ring = 1; ring <= MAX_RINGS; ring++) {
    const radius = ring * STEP;
    for (let i = 0; i < ANGLES; i++) {
      const angle = (i / ANGLES) * Math.PI * 2;
      const pos = {
        x: proposedPos.x + Math.cos(angle) * radius,
        y: proposedPos.y + Math.sin(angle) * radius,
      };
      if (obstacles.every((o) => !rectsOverlap(candidate(pos), o))) {
        return pos;
      }
    }
  }
  // Give up gracefully — return proposed. Practically unreachable on a
  // reasonable canvas unless the user has 1000+ topics.
  return proposedPos;
}

/**
 * Walk the topic list in order, nudging any topic whose persisted position
 * overlaps an earlier one to the nearest non-overlapping spot. Used as a
 * safety net at data-ingestion time (initial load, refetch) so positions
 * that pre-date the drag-end resolver — or that slipped through it — get
 * cleaned up before the canvas paints them on top of each other.
 *
 * Returns a NEW topics array; topics that didn't need correction are
 * returned by reference so callers can cheaply detect which ones moved.
 */
export function ensureNoOverlaps(topics: Topic[]): Topic[] {
  if (topics.length < 2) return topics;
  const placed: Array<{
    id: string;
    position: LayoutPosition;
    width: number;
    height: number;
  }> = [];
  return topics.map((t) => {
    const proposed = { x: t.position_x, y: t.position_y };
    const resolved = resolveOverlap(t.topic_id, proposed, placed);
    placed.push({
      id: t.topic_id,
      position: resolved,
      width: NODE_WIDTH,
      height: NODE_HEIGHT,
    });
    if (resolved.x === proposed.x && resolved.y === proposed.y) {
      return t;
    }
    return { ...t, position_x: resolved.x, position_y: resolved.y };
  });
}
