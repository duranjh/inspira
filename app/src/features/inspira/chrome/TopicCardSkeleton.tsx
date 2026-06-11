// V5 canvas — skeleton placeholder cards.
//
// Renders a small row of shimmering rectangles above the empty
// ReactFlow viewport when the orchestrator hasn't shipped its first
// topic yet (gated on `topics.length === 0` at the wire-in point in
// ProjectCanvas.tsx). Sibling to DraftingBanner — together they fill
// the empty-canvas / pre-first-topic visual gap that issue #173
// reported.
//
// Pure display. The shimmer animation lives in
// `app/src/features/inspira/chrome/chrome.css` under the keyframe
// `inspira-skeleton-shimmer` (duplicated from the Kanban's `kb-shimmer`
// to avoid cross-stylesheet coupling).
//
// `aria-hidden` because DraftingBanner already provides the live status
// for assistive tech — the skeletons are visual decoration only.

export interface TopicCardSkeletonProps {
  /** Number of skeleton cards to render. Defaults to 3. */
  count?: number;
}

export function TopicCardSkeleton({ count = 3 }: TopicCardSkeletonProps) {
  return (
    <div className="inspira-topic-skeleton-row" aria-hidden="true">
      {Array.from({ length: count }, (_, i) => (
        <div key={i} className="inspira-topic-skeleton" />
      ))}
    </div>
  );
}
