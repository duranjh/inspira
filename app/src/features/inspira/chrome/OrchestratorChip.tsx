// V5 canvas chrome — orchestrator status chip.
//
// Pulses when one or more sub-agents are actively drafting topics for
// the currently-viewed project. Listens to the same `inspira:sse:*`
// window events emitted by `useSSE` (mounted by ProjectCanvas + the
// CodeBody route) and renders "Running · N sub-agent(s)".
//
// The SSE stream is project-scoped (the EventSource is opened against
// /api/v2/projects/{projectId}/events), so all events on the wire
// already relate to the current canvas — no additional filtering
// needed here. When the count returns to 0 (every started has a
// matching completed/failed) the chip flips to its idle variant.

import { useEffect, useState } from "react";

type Status = "idle" | "running";

export function OrchestratorChip() {
  const [activeCount, setActiveCount] = useState(0);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const onStart = () => {
      setActiveCount((c) => c + 1);
    };
    const onEnd = () => {
      setActiveCount((c) => Math.max(0, c - 1));
    };
    window.addEventListener("inspira:sse:sub_agent.started", onStart);
    window.addEventListener("inspira:sse:sub_agent.completed", onEnd);
    window.addEventListener("inspira:sse:sub_agent.failed", onEnd);
    return () => {
      window.removeEventListener("inspira:sse:sub_agent.started", onStart);
      window.removeEventListener("inspira:sse:sub_agent.completed", onEnd);
      window.removeEventListener("inspira:sse:sub_agent.failed", onEnd);
    };
  }, []);

  const status: Status = activeCount > 0 ? "running" : "idle";
  if (status === "idle") {
    return (
      <span
        className="orch-chip orch-chip--idle"
        role="status"
        aria-label="Orchestrator idle"
      >
        <span className="orch-chip__dot" aria-hidden="true" />
        Idle
      </span>
    );
  }
  const label = `Running · ${activeCount} sub-agent${
    activeCount === 1 ? "" : "s"
  }`;
  return (
    <span
      className="orch-chip orch-chip--running"
      role="status"
      aria-live="polite"
      aria-label={label}
    >
      <span
        className="orch-chip__dot orch-chip__dot--pulse"
        aria-hidden="true"
      />
      {label}
    </span>
  );
}
