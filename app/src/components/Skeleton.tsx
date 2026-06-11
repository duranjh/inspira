// Skeleton — the shared primitive for content-shape loading placeholders.
//
// Why a new file alongside Skeletons.tsx?
// `Skeletons.tsx` already hosts SkeletonLine / SkeletonCard / SkeletonColumn
// — the original primitives consumed by ProjectsListPage and TopicDetail.
// Rather than churn every existing import site we add this new entry point
// that exposes:
//   - <Skeleton /> — a single warm-editorial shimmer block with a
//     `variant` prop ("text" | "card" | "circle") plus free-form
//     width / height / borderRadius overrides.
//   - <ProjectCardSkeleton /> — re-exported from features/projects so
//     anyone importing from "components/Skeleton" gets the matching
//     project-card-shaped placeholder without having to know which
//     feature folder owns the live card.
//   - <TopicNodeSkeleton /> — matches the canvas TopicNode footprint
//     (~280×180, ink-3 left accent bar) for the canvas initial-load
//     state. Consumers can absolutely-position several of these to
//     hint at the spatial layout that's about to land.
//   - <TurnSkeleton /> — matches a Q&A turn bubble used while the
//     planner is thinking. Renders an optional status text below the
//     shimmer so the surrounding aria-live region has something to
//     announce ("Planner is thinking…").
//
// All three compositions reuse the shared shimmer animation defined
// for `.skeleton` in App.css. Reduced-motion users get a flat dim
// background — we never strobe.
//
// Color palette: the shimmer sweeps `--paper-2` → `--paper-3` →
// `--paper-2`, exposed via the `--paper`-family CSS variables. Dark
// mode swaps both ends to the espresso tones at the :root level so the
// shimmer stays warm in both themes.

import type { CSSProperties, ReactNode } from "react";

import "./skeleton.css";

// ---- Shared primitive -----------------------------------------------------

export type SkeletonVariant = "text" | "card" | "circle";

export type SkeletonProps = {
  // Number values are treated as pixels. Strings pass through to CSS as-is
  // so callers can use "80%", "12ch", "calc(100% - 24px)", etc.
  width?: string | number;
  height?: string | number;
  // Override the variant's default radius. Numbers are pixels; strings
  // pass through. Pass `"50%"` to force a circle on a non-circle variant.
  borderRadius?: string | number;
  // "text" — short line, default 12px tall, full width.
  // "card" — block with rounded corners, default 100% × 120px.
  // "circle" — equal width/height with 50% border-radius (default 36px).
  variant?: SkeletonVariant;
  className?: string;
  // Set explicitly to attach to a screen-reader region. Default is
  // aria-hidden so the shimmer doesn't get announced repeatedly while
  // the surrounding `aria-busy` region waits for content.
  "aria-label"?: string;
};

// Convert a JS prop into a CSS dimension value: numbers → "Npx",
// strings pass through unchanged, undefined returns undefined so the
// variant default kicks in.
function toCss(value: string | number | undefined): string | undefined {
  if (value == null) return undefined;
  return typeof value === "number" ? `${value}px` : value;
}

export function Skeleton({
  width,
  height,
  borderRadius,
  variant = "text",
  className,
  ...rest
}: SkeletonProps) {
  // Variant defaults — only applied when the corresponding prop is
  // missing. Explicit overrides (number 0 still counts as set) win.
  let defaultWidth: string | undefined;
  let defaultHeight: string | undefined;
  let defaultRadius: string | undefined;
  switch (variant) {
    case "text":
      defaultWidth = "100%";
      defaultHeight = "12px";
      defaultRadius = "4px";
      break;
    case "card":
      defaultWidth = "100%";
      defaultHeight = "120px";
      defaultRadius = "10px";
      break;
    case "circle":
      defaultWidth = "36px";
      defaultHeight = "36px";
      defaultRadius = "50%";
      break;
  }

  const style: CSSProperties = {
    width: toCss(width) ?? defaultWidth,
    height: toCss(height) ?? defaultHeight,
    borderRadius: toCss(borderRadius) ?? defaultRadius,
  };

  const cls =
    "skeleton skeleton--" + variant + (className ? " " + className : "");

  // Default aria-hidden; opt-in to a label only when the caller passes one.
  const ariaLabel = rest["aria-label"];
  return (
    <div
      className={cls}
      style={style}
      aria-hidden={ariaLabel ? undefined : true}
      aria-label={ariaLabel}
      role={ariaLabel ? "status" : undefined}
    />
  );
}

// ---- Compositions ---------------------------------------------------------

// ProjectCardSkeleton matches the card footprint used in the projects
// grid (paper card, two-line clamped title, italic "updated …" line, then
// a row of pills). The live ProjectCard already exports its own skeleton
// twin (`ProjectCardSkeleton` in features/projects/ProjectCard.tsx); we
// re-export it from this module so callers can pull the whole
// loading-state toolkit from `components/Skeleton`.
export { ProjectCardSkeleton } from "../features/projects/ProjectCard";

// TopicNodeSkeleton — matches the canvas TopicNode footprint (~280×180,
// thin left accent bar in --ink-3, paper-2 background, ink border). Use
// for the canvas initial-load placeholder so the user sees roughly how
// many cards are about to land instead of a single empty pane. Apply
// `style={{ position: "absolute", top, left }}` from the caller to drop
// these at hand-picked coordinates that hint at the upcoming layout.
export type TopicNodeSkeletonProps = {
  className?: string;
  style?: CSSProperties;
};

export function TopicNodeSkeleton({
  className,
  style,
}: TopicNodeSkeletonProps) {
  const cls =
    "topic-node-skeleton" + (className ? " " + className : "");
  return (
    <div className={cls} style={style} aria-hidden="true">
      <div className="topic-node-skeleton__header">
        <Skeleton variant="circle" width={10} height={10} />
        <Skeleton variant="text" height={14} width="60%" />
      </div>
      <div className="topic-node-skeleton__body">
        <Skeleton variant="text" height={9} width="92%" />
        <Skeleton variant="text" height={9} width="84%" />
        <Skeleton variant="text" height={9} width="68%" />
      </div>
    </div>
  );
}

// TurnSkeleton — placeholder for a Q&A turn bubble while the planner
// is thinking. Two lines of shimmer + an optional status string
// underneath ("Planner is thinking…"). Pass `align="user"` for the
// short, right-aligned variant (matches the user-reply bubble).
export type TurnSkeletonProps = {
  // Optional status caption rendered under the bubble. Used as the
  // accessible label for the wrapper too — when omitted the wrapper
  // is purely decorative.
  status?: ReactNode;
  // "planner" (default) renders left-aligned with three lines.
  // "user" renders right-aligned with two short lines so it reads as
  // an answer rather than another question.
  align?: "planner" | "user";
  className?: string;
};

export function TurnSkeleton({
  status,
  align = "planner",
  className,
}: TurnSkeletonProps) {
  const isUser = align === "user";
  const cls =
    "turn-skeleton" +
    (isUser ? " turn-skeleton--user" : " turn-skeleton--planner") +
    (className ? " " + className : "");
  // When a status is supplied the wrapper becomes an aria-live region so
  // screen readers announce the change; without one we stay decorative.
  const role = status ? "status" : undefined;
  const ariaLive = status ? "polite" : undefined;
  return (
    <div className={cls} role={role} aria-live={ariaLive}>
      <div className="turn-skeleton__bubble" aria-hidden="true">
        <Skeleton variant="text" height={11} width="92%" />
        <Skeleton variant="text" height={11} width={isUser ? "60%" : "84%"} />
        {!isUser ? <Skeleton variant="text" height={11} width="46%" /> : null}
      </div>
      {status ? <p className="turn-skeleton__status">{status}</p> : null}
    </div>
  );
}
