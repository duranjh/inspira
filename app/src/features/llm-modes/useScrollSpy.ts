// IntersectionObserver-based active-section tracker for long-form
// documents with a sticky side nav. (#094 / Item 3 redesign.)
//
// The rootMargin trick collapses the viewport to a thin horizontal
// band ~40% from the viewport top, so exactly one <section> intersects
// at any scroll position. This avoids the classic threshold-0.4
// dead-zone where long sections (>60vh) never reach 40% intersection
// and the active id falls back to the last-known.
//
// Intentionally side-effect-free for reduced-motion users — the hook
// only reports the active id; smooth-scroll behavior is the caller's
// responsibility (DocumentView checks prefers-reduced-motion before
// passing `behavior: "smooth"` vs `"auto"` to scrollIntoView).

import { type RefObject, useEffect, useState } from "react";

/**
 * Track which section is currently considered "active" as the user
 * scrolls a long document. Returns the first section_id (in DOM order)
 * intersecting the focus band, or `null` when no section is on screen
 * (happens briefly on initial mount before the IO callback fires; the
 * caller should treat null as "no highlight").
 *
 * @param sectionIds Ordered list of section ids to observe. The hook
 *                   resolves them by `document.getElementById` so the
 *                   sections must be mounted before the effect runs.
 * @param rootRef    Ref to the scrollable container. When null, the
 *                   browser viewport is used (window-level scroll).
 */
export function useScrollSpy(
  sectionIds: readonly string[],
  rootRef: RefObject<HTMLElement | null>,
): string | null {
  const [activeId, setActiveId] = useState<string | null>(
    sectionIds[0] ?? null,
  );

  // Stable dep key — joining the ids prevents re-running the effect
  // when the parent passes a new array reference but the contents are
  // unchanged. Pipe is a safe separator (section_ids are snake_case
  // ASCII per the BE canonical-list).
  const idsKey = sectionIds.join("|");

  useEffect(() => {
    // SSR / jsdom-without-IO safety.
    if (
      typeof window === "undefined"
      || typeof IntersectionObserver === "undefined"
    ) {
      return;
    }
    if (sectionIds.length === 0) return;

    const idOrder = new Map<string, number>();
    sectionIds.forEach((id, i) => {
      idOrder.set(id, i);
    });

    const elements = sectionIds
      .map((id) => document.getElementById(id))
      .filter((el): el is HTMLElement => el !== null);
    if (elements.length === 0) return;

    const observer = new IntersectionObserver(
      (entries) => {
        // Maintain a running set of intersecting ids; pick the first
        // in DOM order (lowest index in idOrder). We track via state
        // to remember between callbacks since IO only reports
        // transitions, not the full visibility set.
        setActiveId((prev) => {
          // Build the next intersecting set from this batch + the
          // observer's stored map (we keep our own).
          const intersecting = new Set<string>(
            entries
              .filter((e) => e.isIntersecting)
              .map((e) => (e.target as HTMLElement).id),
          );
          // For sections that fired with isIntersecting=false, drop
          // them from the prev tracker — the IO doesn't re-fire for
          // already-out-of-view elements.
          const exiting = new Set<string>(
            entries
              .filter((e) => !e.isIntersecting)
              .map((e) => (e.target as HTMLElement).id),
          );
          // Update our tracker (kept in closure as `currentlyVisible`).
          for (const id of exiting) currentlyVisible.delete(id);
          for (const id of intersecting) currentlyVisible.add(id);

          if (currentlyVisible.size === 0) {
            // No section in band — keep the previous active id (avoids
            // flicker between sections during fast scroll).
            return prev;
          }
          let best: string | null = null;
          let bestRank = Number.POSITIVE_INFINITY;
          for (const id of currentlyVisible) {
            const rank = idOrder.get(id);
            if (rank === undefined) continue;
            if (rank < bestRank) {
              bestRank = rank;
              best = id;
            }
          }
          return best ?? prev;
        });
      },
      {
        root: rootRef.current ?? null,
        rootMargin: "-40% 0px -55% 0px",
        threshold: 0,
      },
    );

    // Observer-private visibility tracker (IO doesn't expose its own).
    const currentlyVisible = new Set<string>();

    for (const el of elements) observer.observe(el);

    return () => {
      observer.disconnect();
    };
    // rootRef is a ref object — its identity is stable across renders.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [idsKey]);

  return activeId;
}
