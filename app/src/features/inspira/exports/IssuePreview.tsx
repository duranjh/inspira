// Read-only preview of the issue body that will land in Linear / GitHub.
//
// Mirrors the design's IssuePreview component (B4.2 + B4.3): title,
// "What this addresses" prose, decisions list, source-data chips, and
// the trailing sub-issues / tasks list (one entry per topic).
//
// Pulls data from the project envelope already loaded by the parent.
// No fetching here — preview is pure rendering.

import type { Decision, Topic, V2Project } from "../api";
import type { ExportProvider } from "./types";

export type IssuePreviewProps = {
  project: V2Project;
  topics: Topic[];
  decisions: Decision[];
  provider: ExportProvider;
  showCanvasLink: boolean;
  showSourceFeedback: boolean;
  sourceFeedbackCount?: number;
};

function getProjectMetadata(project: V2Project): {
  description: string | null;
  tradeoffs: string[];
} {
  const meta = project.metadata as Record<string, unknown> | undefined;
  const description =
    typeof meta?.description === "string" && meta.description.trim()
      ? (meta.description as string).trim()
      : null;
  const rawTradeoffs = meta?.tradeoffs ?? meta?.trade_offs;
  const tradeoffs = Array.isArray(rawTradeoffs)
    ? rawTradeoffs
        .filter((t): t is string => typeof t === "string" && t.trim().length > 0)
        .map((t) => t.trim())
    : [];
  return { description, tradeoffs };
}

export function IssuePreview({
  project,
  topics,
  decisions,
  provider,
  showCanvasLink,
  showSourceFeedback,
  sourceFeedbackCount,
}: IssuePreviewProps) {
  const { description, tradeoffs } = getProjectMetadata(project);
  const decisionStatements = decisions
    .filter((d) => d.status !== "retracted")
    .map((d) => d.statement.trim())
    .filter((s) => s.length > 0);
  const visibleDecisions = decisionStatements.slice(0, 6);
  const hiddenDecisionCount = Math.max(
    0,
    decisionStatements.length - visibleDecisions.length,
  );

  return (
    <div className="ex-preview" role="region" aria-label="Issue preview">
      <div className="ex-preview__title">{project.title}</div>

      {description && (
        <div className="ex-preview__section">
          <div className="ex-preview__section-hd">What this addresses</div>
          <div className="ex-preview__text">{description}</div>
        </div>
      )}

      {decisionStatements.length > 0 && (
        <div className="ex-preview__section">
          <div className="ex-preview__section-hd">
            Decisions ({decisionStatements.length})
          </div>
          {visibleDecisions.map((d, i) => (
            <div key={`d-${i}`} className="ex-preview__bullet">
              {d}
            </div>
          ))}
          {hiddenDecisionCount > 0 && (
            <div
              className="ex-preview__more"
              aria-label={`${hiddenDecisionCount} more decisions`}
            >
              … +{hiddenDecisionCount} more
            </div>
          )}
        </div>
      )}

      {showSourceFeedback && (sourceFeedbackCount ?? 0) > 0 && (
        <div className="ex-preview__section">
          <div className="ex-preview__section-hd">Source data</div>
          <div className="ex-preview__chips">
            <span className="ex-chip ex-chip--gold">
              {sourceFeedbackCount} cited item
              {(sourceFeedbackCount ?? 0) === 1 ? "" : "s"}
            </span>
          </div>
        </div>
      )}

      {tradeoffs.length > 0 && (
        <div className="ex-preview__section">
          <div className="ex-preview__section-hd">Trade-offs considered</div>
          {tradeoffs.map((t, i) => (
            <div key={`t-${i}`} className="ex-preview__bullet">
              {t}
            </div>
          ))}
        </div>
      )}

      {topics.length > 0 && (
        <div className="ex-sub">
          <div className="ex-sub__hd">
            {provider === "github" ? "Tasks" : "Sub-issues"}
          </div>
          {topics.map((topic) => (
            <div key={topic.topic_id} className="ex-sub__item">
              <span className="ex-sub__check" aria-hidden="true" />
              <span className="ex-sub__topic">{topic.title}</span>
            </div>
          ))}
        </div>
      )}

      {showCanvasLink && (
        <div className="ex-preview__link" aria-hidden="true">
          Linked from Inspira project →
        </div>
      )}
    </div>
  );
}
