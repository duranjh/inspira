// W2 η — collapsed-by-default reasoning expander inside Topic Detail.
//
// Four sections per the B2.4 design HTML at /tmp/inspira-v12/Topic Detail.html:
//   1. Cited feedback items        — provenance chips per decision
//   2. ROI rationale               — topic.metadata.why_this_topic
//   3. Decision derivation         — existing Decision rows
//   4. Re-think this topic         — disabled button (Wave 3 / θ ships the
//                                    regenerate-cascade endpoint)
//
// Provenance comes from two paths:
//   - Live SSE: parent accumulates `decision.drafted` events into the map
//   - Cold open: parent passes `onLoadProvenance` so this component can
//     trigger a one-shot REST fetch the first time the user expands.
//
// All visible strings inlined; i18n migration tracked in the same
// follow-up that adds the other η strings to en.json.

import { useEffect, useRef } from "react";

import type { Decision, Topic, TopicProvenanceRow } from "./api";

export interface ReasoningTraceProps {
  topic: Topic;
  decisions: Decision[];
  provenanceByDecisionId: Map<string, TopicProvenanceRow[]>;
  isOpen: boolean;
  onToggle: () => void;
  /** Optional one-shot REST fallback — invoked the first time the user
   *  opens the expander if the provenance map is still empty. Pass null
   *  when the parent has no project context (e.g. legacy non-orchestrator
   *  topics, where there's nothing to load). */
  onLoadProvenance?: (() => void) | null;
}

export function ReasoningTrace({
  topic,
  decisions,
  provenanceByDecisionId,
  isOpen,
  onToggle,
  onLoadProvenance,
}: ReasoningTraceProps) {
  const hasLazyLoaded = useRef(false);

  // Trigger the one-shot REST load the first time the user opens the
  // expander. Always run it (even when live SSE rows are already in the
  // map) because SSE payloads only carry feedback_item IDs — without the
  // REST hydrate, "Cited feedback items" would render IDs as titles.
  // The merge in the parent's loadProvenanceFromRest replaces any live
  // placeholders with full server rows; live-only entries that REST
  // doesn't yet know about (mid-flight decisions) are preserved.
  useEffect(() => {
    if (!isOpen) return;
    if (hasLazyLoaded.current) return;
    if (onLoadProvenance) {
      hasLazyLoaded.current = true;
      onLoadProvenance();
    }
  }, [isOpen, onLoadProvenance]);

  const whyThisTopic =
    typeof topic.metadata?.why_this_topic === "string"
      ? (topic.metadata.why_this_topic as string).trim()
      : "";

  const allProvenance: TopicProvenanceRow[] = [];
  for (const rows of provenanceByDecisionId.values()) {
    allProvenance.push(...rows);
  }

  const bodyId = `reasoning-body-${topic.topic_id}`;

  return (
    <section className="topic-detail__reasoning">
      <button
        type="button"
        className="topic-detail__reasoning-trigger"
        onClick={onToggle}
        aria-expanded={isOpen}
        aria-controls={bodyId}
      >
        <span className="topic-detail__reasoning-chev" aria-hidden="true">
          {isOpen ? "▾" : "▸"}
        </span>
        <span className="topic-detail__reasoning-label">
          How Inspira reached these decisions
        </span>
        <span className="topic-detail__reasoning-chip">reasoning</span>
      </button>

      {isOpen && (
        <div
          id={bodyId}
          role="region"
          aria-label="Reasoning trace"
          className="topic-detail__reasoning-body"
        >
          {/* 1. Cited feedback items */}
          <section className="topic-detail__reasoning-section">
            <h4 className="topic-detail__reasoning-heading">
              Cited feedback items
            </h4>
            {allProvenance.length === 0 ? (
              <p className="topic-detail__reasoning-empty">
                No cited feedback for this topic.
              </p>
            ) : (
              <ul className="topic-detail__reasoning-sources">
                {allProvenance.map((row) => (
                  <li
                    key={`${row.decision_id}-${row.feedback_item_id}`}
                    className="topic-detail__reasoning-source"
                  >
                    <span className="topic-detail__reasoning-source-chip">
                      {row.feedback_item.source}
                    </span>
                    <span className="topic-detail__reasoning-source-text">
                      {row.feedback_item.title}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </section>

          {/* 2. ROI rationale */}
          <section className="topic-detail__reasoning-section">
            <h4 className="topic-detail__reasoning-heading">ROI rationale</h4>
            {whyThisTopic ? (
              <p className="topic-detail__reasoning-rationale">
                {whyThisTopic}
              </p>
            ) : (
              <p className="topic-detail__reasoning-empty">
                No rationale recorded for this topic.
              </p>
            )}
          </section>

          {/* 3. Decision derivation */}
          <section className="topic-detail__reasoning-section">
            <h4 className="topic-detail__reasoning-heading">
              Decision derivation
            </h4>
            {decisions.length === 0 ? (
              <p className="topic-detail__reasoning-empty">
                No decisions yet.
              </p>
            ) : (
              <ul className="topic-detail__reasoning-derivation">
                {decisions.map((d) => (
                  <li
                    key={d.decision_id}
                    className="topic-detail__reasoning-derivation-row"
                  >
                    <span
                      className="topic-detail__reasoning-derivation-dot"
                      aria-hidden="true"
                    />
                    <span className="topic-detail__reasoning-derivation-text">
                      {d.statement}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </section>

          {/* 4. Re-think — disabled until θ ships regenerate-cascade */}
          <section className="topic-detail__reasoning-section">
            <button
              type="button"
              className="topic-detail__reasoning-rethink"
              disabled
              aria-disabled="true"
              aria-label="Re-think this topic — available next release"
              title="Available next release"
            >
              ↻ Re-think this topic
            </button>
          </section>
        </div>
      )}
    </section>
  );
}
