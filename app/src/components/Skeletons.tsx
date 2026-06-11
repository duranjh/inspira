// Reusable skeleton primitives for loading states.
//
// All three components are pure presentational — no effects, no state, no
// external dependencies. Styling lives in App.css under the `.skeleton*`
// class namespace. The shared pulse animation is a gentle cream-to-paper-2
// shimmer at ~1.4s; `prefers-reduced-motion` collapses it to a static dim.
//
// The intent is a "quiet pause" — skeletons should hint at shape and
// nothing more. Avoid stacking so many of them that the viewport looks
// like a busy progress screen.

import type { CSSProperties } from "react";

export type SkeletonLineProps = {
  // Width of the line. Number values are treated as pixels; strings are
  // passed through to CSS as-is (so `"80%"` and `"12ch"` both work).
  // Default is `100%` so lines naturally fill their container.
  width?: string | number;
  // Vertical thickness of the line in pixels. Defaults to 12 — enough to
  // feel like a line of body text without looking like a bar.
  height?: number;
  className?: string;
};

export function SkeletonLine({
  width = "100%",
  height = 12,
  className,
}: SkeletonLineProps) {
  const style: CSSProperties = {
    width: typeof width === "number" ? `${width}px` : width,
    height: `${height}px`,
  };
  return (
    <div
      className={"skeleton skeleton--line" + (className ? ` ${className}` : "")}
      style={style}
      aria-hidden="true"
    />
  );
}

export type SkeletonCardProps = {
  // Override if you need a placeholder that's taller or shorter than the
  // default topic-card footprint — used e.g. in the Q&A thread to hint at
  // varying turn-bubble heights.
  height?: number;
  className?: string;
};

// A topic-card-shaped placeholder. Default dimensions mirror the live
// TopicNode (~280×180) so swapping the skeleton for the real card on
// first paint doesn't nudge the layout.
export function SkeletonCard({ height = 180, className }: SkeletonCardProps) {
  const style: CSSProperties = { height: `${height}px` };
  return (
    <div
      className={"skeleton skeleton--card" + (className ? ` ${className}` : "")}
      style={style}
      aria-hidden="true"
    >
      <div className="skeleton skeleton--line skeleton--card-eyebrow" />
      <div className="skeleton skeleton--line skeleton--card-title" />
      <div className="skeleton skeleton--line skeleton--card-body" />
      <div className="skeleton skeleton--line skeleton--card-body-short" />
    </div>
  );
}

export type SkeletonColumnProps = {
  // Widths for each line in the column. Accepts `"80%"`, `"12ch"`, or
  // a number (treated as pixels). Defaults to three lines at varying
  // widths so the output looks like body copy, not a progress bar.
  lines?: Array<string | number>;
  className?: string;
};

// A vertical stack of SkeletonLines used for the Decisions column and
// anywhere else a few lines of prose are loading.
export function SkeletonColumn({
  lines = ["80%", "90%", "60%"],
  className,
}: SkeletonColumnProps) {
  return (
    <div
      className={
        "skeleton skeleton--column" + (className ? ` ${className}` : "")
      }
      aria-hidden="true"
    >
      {lines.map((w, i) => (
        <SkeletonLine key={i} width={w} />
      ))}
    </div>
  );
}
