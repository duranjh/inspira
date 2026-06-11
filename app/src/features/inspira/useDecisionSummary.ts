import { useCallback, useEffect, useState } from "react";

import { mockDecisionSummary, type DecisionSummary } from "./decisionSummary";

// Single-string contract Session α targets: its SSE onComplete callback
// dispatches `window.dispatchEvent(new CustomEvent(EVENT_NAME, { detail: payload }))`.
// The dev trigger button uses the same dispatch path so production wiring
// requires zero changes inside the drawer when α merges.
export const ORCHESTRATOR_COMPLETED_EVENT = "inspira:orchestrator-completed";

export type UseDecisionSummary = {
  summary: DecisionSummary | null;
  drawerOpen: boolean;
  open: () => void;
  close: () => void;
  triggerMock: () => void;
};

export function useDecisionSummary(): UseDecisionSummary {
  const [summary, setSummary] = useState<DecisionSummary | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);

  useEffect(() => {
    const onComplete = (ev: Event) => {
      const detail = (ev as CustomEvent).detail as DecisionSummary | undefined;
      if (!detail) return;
      setSummary(detail);
      setDrawerOpen(true);
    };
    window.addEventListener(ORCHESTRATOR_COMPLETED_EVENT, onComplete);
    return () =>
      window.removeEventListener(ORCHESTRATOR_COMPLETED_EVENT, onComplete);
  }, []);

  const open = useCallback(() => {
    setDrawerOpen(true);
  }, []);

  const close = useCallback(() => {
    setDrawerOpen(false);
  }, []);

  const triggerMock = useCallback(() => {
    window.dispatchEvent(
      new CustomEvent(ORCHESTRATOR_COMPLETED_EVENT, {
        detail: mockDecisionSummary,
      }),
    );
  }, []);

  return { summary, drawerOpen, open, close, triggerMock };
}
