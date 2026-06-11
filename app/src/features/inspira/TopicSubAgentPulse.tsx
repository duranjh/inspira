// W2 η — pulse next to the Topic Detail header title when a sub-agent
// is actively working on this topic's theme.
//
// Filters by `theme_id` rather than `topic_id` because the orchestrator
// emits sub_agent.* with a theme/cluster identifier, not a per-topic one
// (one sub-agent run produces all the topics + decisions for one theme).
// δ's existing `MultiAgentDots` filters by topic_id and never matches
// the orchestrator's payload — that's a separate bug, not ours to fix.
//
// Reuses `.multi-agent-dots` + `.multi-agent-dots__dot` CSS from
// ProjectCanvas's `chrome/chrome.css` import. No animation work here.

import { useEffect, useState } from "react";

// Strings inlined intentionally — η ships before the en→{de,es,fr,it,
// ja,nl,pl,pt} translations land. Migrate to t("topic_detail.*") in a
// follow-up that updates all 9 locale bundles together.

export interface TopicSubAgentPulseProps {
  themeId: string | undefined | null;
  label?: string;
}

type SubAgentDetail = {
  theme_id?: string;
};

export function TopicSubAgentPulse({
  themeId,
  label,
}: TopicSubAgentPulseProps) {
  const [active, setActive] = useState(false);

  useEffect(() => {
    if (!themeId) return;
    if (typeof window === "undefined") return;

    const matches = (e: Event): boolean => {
      const detail = (e as CustomEvent<SubAgentDetail>).detail;
      return detail?.theme_id === themeId;
    };

    const onStart = (e: Event) => {
      if (matches(e)) setActive(true);
    };
    const onEnd = (e: Event) => {
      if (matches(e)) setActive(false);
    };

    window.addEventListener("inspira:sse:sub_agent.started", onStart);
    window.addEventListener("inspira:sse:sub_agent.completed", onEnd);
    window.addEventListener("inspira:sse:sub_agent.failed", onEnd);

    return () => {
      window.removeEventListener("inspira:sse:sub_agent.started", onStart);
      window.removeEventListener("inspira:sse:sub_agent.completed", onEnd);
      window.removeEventListener("inspira:sse:sub_agent.failed", onEnd);
    };
  }, [themeId]);

  if (!active) return null;

  const text = label ?? "Sub-agent working…";
  return (
    <span
      className="topic-detail__sub-agent-pulse"
      role="status"
      aria-live="polite"
    >
      <span className="multi-agent-dots" aria-hidden="true">
        <span className="multi-agent-dots__dot" />
        <span className="multi-agent-dots__dot" />
        <span className="multi-agent-dots__dot" />
      </span>
      <span className="topic-detail__sub-agent-pulse-label">{text}</span>
    </span>
  );
}
