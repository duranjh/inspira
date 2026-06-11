// V5 canvas — top-of-canvas "Inspira is drafting" status banner.
//
// Sibling to MultiAgentDots / OrchestratorChip — listens to the same
// `inspira:sse:sub_agent.*` window CustomEvents fanned out by useSSE
// (`app/src/hooks/useSSE.ts:80-135`). Renders a single warm-paper pill
// near the top of the canvas while the orchestrator is actively
// drafting topics, so the empty-canvas / streaming-canvas window reads
// as "Inspira is working" rather than "broken".
//
// Issue #173: the canvas used to land empty with
// no signal that work was in progress. Re-scope per RP-2: backend has
// no "expected total topics" hint (the LLM produces an open-ended
// list), so the banner shows a running count only — "N topics so far"
// — rather than an "N of M" denominator.
//
// Visibility (derived, no `quiesced` flag — natural re-arm path):
//   visible = activeCount > 0
//           OR (completedCount > 0 AND idle for < IDLE_MS)
// The idle timer resets on every sub_agent event, so a fresh `started`
// after a long lull naturally re-shows the banner without any reset
// logic. activeCount mirrors MultiAgentDots' ref-count pattern.
//
// No props: SSE events are window-scoped and only one DraftingBanner
// is mounted per canvas (inside ProjectCanvasInner).
//
// Copy uses two i18n keys (`canvas.drafting_banner.count_one`
// / `count_many`) selected at call site — the in-house i18n shim at
// `app/src/i18n/index.ts` has no plural support. Pattern matches
// `app/src/features/shelves/ShelfHeader.tsx:113-115`.

import { useCallback, useEffect, useRef, useState } from "react";

import { t } from "../../../i18n";

const IDLE_MS = 15_000;

export function DraftingBanner() {
  const [activeCount, setActiveCount] = useState(0);
  const [completedCount, setCompletedCount] = useState(0);
  const [idleHidden, setIdleHidden] = useState(false);
  const timerRef = useRef<number | null>(null);

  const bumpIdle = useCallback(() => {
    setIdleHidden(false);
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current);
    }
    timerRef.current = window.setTimeout(() => {
      setIdleHidden(true);
    }, IDLE_MS);
  }, []);

  useEffect(() => {
    if (typeof window === "undefined") return;

    const onStart = () => {
      setActiveCount((c) => c + 1);
      bumpIdle();
    };
    const onCompleted = () => {
      setActiveCount((c) => Math.max(0, c - 1));
      setCompletedCount((c) => c + 1);
      bumpIdle();
    };
    const onFailed = () => {
      setActiveCount((c) => Math.max(0, c - 1));
      bumpIdle();
    };

    window.addEventListener("inspira:sse:sub_agent.started", onStart);
    window.addEventListener("inspira:sse:sub_agent.completed", onCompleted);
    window.addEventListener("inspira:sse:sub_agent.failed", onFailed);

    return () => {
      window.removeEventListener("inspira:sse:sub_agent.started", onStart);
      window.removeEventListener(
        "inspira:sse:sub_agent.completed",
        onCompleted,
      );
      window.removeEventListener("inspira:sse:sub_agent.failed", onFailed);
      if (timerRef.current !== null) {
        window.clearTimeout(timerRef.current);
        timerRef.current = null;
      }
    };
  }, [bumpIdle]);

  const visible = activeCount > 0 || (completedCount > 0 && !idleHidden);
  if (!visible) return null;

  const label =
    completedCount === 1
      ? t("canvas.drafting_banner.count_one")
      : t("canvas.drafting_banner.count_many", {
          count: String(completedCount),
        });

  return (
    <div
      className="inspira-drafting-banner"
      role="status"
      aria-live="polite"
    >
      <span className="inspira-drafting-banner__dot" aria-hidden="true" />
      <span className="inspira-drafting-banner__text">{label}</span>
    </div>
  );
}
