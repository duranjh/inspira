// Live feed of decision.drafted events for the open Topic Detail.
//
// The parent (TopicDetail) filters incoming SSE events by theme_id +
// topic.order_index and feeds the matched payloads in. This component
// is dumb: render the rows, auto-scroll, cap at MAX_VISIBLE.

import { useEffect, useRef } from "react";

export type LiveDecisionEvent = {
  decision_id: string;
  statement: string;
  rationale: string | null;
  subject: string;
  // ISO 8601 — appended at receive time so the stream stays ordered
  // even if the orchestrator reorders mid-flight.
  received_at: string;
};

export interface SubAgentStreamProps {
  events: LiveDecisionEvent[];
  isActive: boolean;
  isOpen: boolean;
  onToggle: () => void;
}

const MAX_VISIBLE = 50;

export function SubAgentStream({
  events,
  isActive,
  isOpen,
  onToggle,
}: SubAgentStreamProps) {
  const feedRef = useRef<HTMLDivElement | null>(null);

  // Bounds the DOM at MAX_VISIBLE rows so a long-running canvas (50+
  // decisions) doesn't blow up the drawer's render cost. Latest rows
  // win; older rows drop off the top of the visible window.
  const visible =
    events.length > MAX_VISIBLE ? events.slice(-MAX_VISIBLE) : events;

  // Auto-scroll the feed to the bottom whenever a new row arrives.
  useEffect(() => {
    if (!isOpen) return;
    const el = feedRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [isOpen, visible.length]);

  if (!isActive) return null;

  return (
    <section className="topic-detail__stream" aria-live="polite">
      <button
        type="button"
        className="topic-detail__stream-trigger"
        onClick={onToggle}
        aria-expanded={isOpen}
      >
        <span className="topic-detail__stream-chev" aria-hidden="true">
          {isOpen ? "▾" : "▸"}
        </span>
        <span className="topic-detail__stream-label">
          Watch sub-agent (live)
        </span>
        <span className="topic-detail__stream-count">
          {events.length} {events.length === 1 ? "decision" : "decisions"}
        </span>
      </button>
      {isOpen && (
        <div ref={feedRef} className="topic-detail__stream-feed">
          {visible.length === 0 ? (
            <p className="topic-detail__stream-empty">
              Waiting for the next decision…
            </p>
          ) : (
            visible.map((evt) => (
              <div key={evt.decision_id} className="topic-detail__stream-row">
                <span className="topic-detail__stream-ts">
                  {formatTime(evt.received_at)}
                </span>
                <span className="topic-detail__stream-text">
                  {evt.statement}
                </span>
              </div>
            ))
          )}
        </div>
      )}
    </section>
  );
}

function formatTime(iso: string): string {
  // ISO → "HH:MM:SS" in the user's locale, no date — the date is
  // implicit (the live stream is right-now). Falls back to the raw
  // string if parsing fails so we never crash the drawer.
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleTimeString(undefined, { hour12: false });
}
