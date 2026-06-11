// V5 canvas — multi-agent loading dots overlay.
//
// Renders three small sage dots (typing-indicator bounce) on a topic
// card whenever the orchestrator has any sub-agent actively drafting
// for the current project. Driven by α's `sub_agent.started` /
// `sub_agent.completed` / `sub_agent.failed` SSE events fanned out by
// useSSE as `inspira:sse:<name>` window events.
//
// Note on filter granularity: the backend emits `theme_id` + `project_id`
// in the payload but NOT `topic_id`, so we can't pin the dots to a
// specific topic. The SSE EventSource itself is project-scoped (opened
// against /api/v2/projects/{projectId}/events), so every event on the
// wire already relates to the current canvas — we just count active
// sub-agents and show dots when ≥1 is running. Every topic on the
// canvas pulses while *any* sub-agent is working, which matches the
// v5 design's "agent at work" visual signal even without per-topic
// granularity.
//
// `topicId` prop is retained for the future per-topic event story
// (orchestrator.py line 310 already records topic_id alongside
// decisions; emitting it on `decision.drafted` would let us narrow
// the highlight per-topic without changing this component's API).

import { useEffect, useState } from "react";

export interface MultiAgentDotsProps {
  /** Reserved for future per-topic filtering — currently unused (see
   *  module-level note). Keep the prop so callers don't need to
   *  change when the backend gains per-topic events. */
  topicId?: string;
}

export function MultiAgentDots(_props: MultiAgentDotsProps = {}) {
  const [activeCount, setActiveCount] = useState(0);

  useEffect(() => {
    if (typeof window === "undefined") return;

    const onStart = () => setActiveCount((c) => c + 1);
    const onEnd = () => setActiveCount((c) => Math.max(0, c - 1));

    window.addEventListener("inspira:sse:sub_agent.started", onStart);
    window.addEventListener("inspira:sse:sub_agent.completed", onEnd);
    window.addEventListener("inspira:sse:sub_agent.failed", onEnd);

    return () => {
      window.removeEventListener("inspira:sse:sub_agent.started", onStart);
      window.removeEventListener("inspira:sse:sub_agent.completed", onEnd);
      window.removeEventListener("inspira:sse:sub_agent.failed", onEnd);
    };
  }, []);

  if (activeCount === 0) return null;

  return (
    <span
      className="multi-agent-dots"
      role="status"
      aria-label="Sub-agent drafting"
    >
      <span className="multi-agent-dots__dot" aria-hidden="true" />
      <span className="multi-agent-dots__dot" aria-hidden="true" />
      <span className="multi-agent-dots__dot" aria-hidden="true" />
    </span>
  );
}
