/**
 * TimelineView — decisions for the active project in reverse-chronological
 * order, grouped by day, with topic badges and relative timestamps.
 *
 * UX shape:
 *   - Filter pill row at the top: "All" + one pill per topic that has decisions.
 *   - Muted date headers separate groups.
 *   - Each decision sits on a dashed vertical rail with a sage dot.
 *   - Clicking a decision dispatches `inspira:open-topic-detail` so the
 *     parent can open the TopicDetail drawer on that topic.
 *   - Empty state: warm card nudging the user to start a Q&A.
 *
 * Data is fetched lazily on first tab activation (managed by LlmModesPanel
 * via `loaded` / `onLoad` props), then held in a ref so switching tabs
 * doesn't re-fetch. The parent bumps `cacheVersion` to force a re-read from
 * the ref on each render pass.
 */

import { useState, type ReactElement } from "react";

import { t } from "../../i18n";
import { formatDate, formatRelativeTime } from "../../i18n/format";
import type { Decision, Topic } from "../inspira/api";

// ---- Types ----------------------------------------------------------------

export type TimelineViewProps = {
  decisions: Decision[];
  topicsById: Map<string, Pick<Topic, "title" | "icon">>;
  onClose: () => void;
};

export type TimelineViewErrorProps = {
  message: string;
  onRetry: () => void;
};

// ---- Helpers ---------------------------------------------------------------

/** ISO date string → "YYYY-MM-DD" bucket key (local time). */
function dayKey(iso: string): string {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso.slice(0, 10);
  // Use local date components so "today" matches the user's wall clock.
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

/** Group decisions by day bucket, most-recent day first. */
function groupByDay(
  decisions: Decision[],
): Array<{ key: string; label: string; items: Decision[] }> {
  const map = new Map<string, Decision[]>();
  for (const d of decisions) {
    const k = dayKey(d.created_at);
    if (!map.has(k)) map.set(k, []);
    map.get(k)!.push(d);
  }
  // Sort days descending; items within each day are already reverse-chron
  // because `decisions` arrives sorted by the backend (created_at DESC).
  const sorted = [...map.entries()].sort((a, b) =>
    b[0].localeCompare(a[0]),
  );
  return sorted.map(([key, items]) => ({
    key,
    label: formatDate(key) || key,
    items,
  }));
}

// ---- Sub-components --------------------------------------------------------

export function TimelineViewLoading(): ReactElement {
  return (
    <div
      className="llm-card"
      role="region"
      aria-label={t("timeline_view.loading_aria")}
    >
      <header className="llm-card__titlebar">
        <span className="llm-card__eyebrow">{t("timeline_view.eyebrow")}</span>
        <h2 className="llm-card__title">{t("timeline_view.title")}</h2>
      </header>
      <p className="llm-status" aria-live="polite">
        {t("timeline_view.loading_status")}
      </p>
      <div aria-hidden="true">
        <div className="summary-skeleton__line" style={{ width: "80%" }} />
        <div className="summary-skeleton__line" style={{ width: "60%" }} />
        <div className="summary-skeleton__gap" />
        <div className="summary-skeleton__line" style={{ width: "75%" }} />
        <div className="summary-skeleton__line" style={{ width: "50%" }} />
      </div>
    </div>
  );
}

export function TimelineViewError(
  props: TimelineViewErrorProps,
): ReactElement {
  const { message, onRetry } = props;
  return (
    <div className="llm-error" role="alert">
      <span className="llm-error__body">
        {t("timeline_view.error_body", { message })}
      </span>
      <button
        type="button"
        className="llm-error__retry"
        onClick={onRetry}
      >
        {t("timeline_view.error_retry")}
      </button>
    </div>
  );
}

// ---- Main component --------------------------------------------------------

export function TimelineView(props: TimelineViewProps): ReactElement {
  const { decisions, topicsById, onClose } = props;
  const [activeTopicFilter, setActiveTopicFilter] = useState<string | null>(
    null,
  );

  // ---- Filter logic --------------------------------------------------------
  // Collect unique topic ids that appear in the decisions list (in insertion
  // order, which is reverse-chron — "most active topic first" in the pill row).
  const topicIdsInOrder: string[] = [];
  const seen = new Set<string>();
  for (const d of decisions) {
    if (!seen.has(d.topic_id)) {
      seen.add(d.topic_id);
      topicIdsInOrder.push(d.topic_id);
    }
  }

  const filtered =
    activeTopicFilter === null
      ? decisions
      : decisions.filter((d) => d.topic_id === activeTopicFilter);

  const groups = groupByDay(filtered);

  // ---- Empty state ---------------------------------------------------------
  if (decisions.length === 0) {
    return (
      <div
        className="llm-card timeline-empty-card"
        role="region"
        aria-label={t("timeline_view.aria")}
      >
        <header className="llm-card__titlebar">
          <span className="llm-card__eyebrow">
            {t("timeline_view.eyebrow")}
          </span>
          <h2 className="llm-card__title">{t("timeline_view.title")}</h2>
          <button
            type="button"
            className="llm-pill"
            onClick={onClose}
            aria-label={t("timeline_view.close_aria")}
          >
            {t("timeline_view.close")}
          </button>
        </header>
        <div className="timeline-empty">
          <span className="timeline-empty__icon" aria-hidden="true">
            &#10040;
          </span>
          <p className="timeline-empty__text">{t("timeline_view.empty")}</p>
          <p className="timeline-empty__hint">{t("timeline_view.empty_hint")}</p>
        </div>
      </div>
    );
  }

  // ---- Normal render -------------------------------------------------------
  return (
    <div
      className="llm-card"
      role="region"
      aria-label={t("timeline_view.aria")}
    >
      <header className="llm-card__titlebar">
        <span className="llm-card__eyebrow">{t("timeline_view.eyebrow")}</span>
        <h2 className="llm-card__title">
          {t("timeline_view.title_count", {
            count: String(filtered.length),
          })}
        </h2>
        <button
          type="button"
          className="llm-pill"
          onClick={onClose}
          aria-label={t("timeline_view.close_aria")}
        >
          {t("timeline_view.close")}
        </button>
      </header>

      {/* ---- Topic filter pills ------------------------------------------ */}
      {topicIdsInOrder.length > 1 && (
        <div
          className="timeline-filters"
          role="toolbar"
          aria-label={t("timeline_view.filter_aria")}
        >
          <button
            type="button"
            className={
              "timeline-filter-pill" +
              (activeTopicFilter === null
                ? " timeline-filter-pill--active"
                : "")
            }
            onClick={() => setActiveTopicFilter(null)}
          >
            {t("timeline_view.filter_all")}
          </button>
          {topicIdsInOrder.map((topicId) => {
            const topic = topicsById.get(topicId);
            const icon = topic?.icon ?? "";
            const title =
              topic?.title ?? t("timeline_view.unknown_topic");
            const active = activeTopicFilter === topicId;
            return (
              <button
                key={topicId}
                type="button"
                className={
                  "timeline-filter-pill" +
                  (active ? " timeline-filter-pill--active" : "")
                }
                onClick={() =>
                  setActiveTopicFilter(active ? null : topicId)
                }
                aria-pressed={active}
              >
                {icon ? (
                  <span className="timeline-filter-pill__icon" aria-hidden="true">
                    {icon}
                  </span>
                ) : null}
                {title}
              </button>
            );
          })}
        </div>
      )}

      {/* ---- Day groups -------------------------------------------------- */}
      <div className="timeline-rail-wrapper" role="list">
        {groups.map((group) => (
          <div key={group.key} className="timeline-day-group">
            <div className="timeline-day-header" role="separator">
              {group.label}
            </div>
            {group.items.map((decision) => {
              const topic = topicsById.get(decision.topic_id);
              const topicIcon = topic?.icon ?? "";
              const topicTitle =
                topic?.title ?? t("timeline_view.unknown_topic");
              const relTime = formatRelativeTime(decision.created_at);

              return (
                <div
                  key={decision.decision_id}
                  className="timeline-entry"
                  role="listitem"
                >
                  {/* Dashed rail + sage dot */}
                  <div className="timeline-entry__gutter" aria-hidden="true">
                    <div className="timeline-entry__dot" />
                  </div>

                  {/* Decision card */}
                  <button
                    type="button"
                    className="timeline-entry__card"
                    onClick={() => {
                      if (typeof window !== "undefined") {
                        window.dispatchEvent(
                          new CustomEvent("inspira:open-topic-detail", {
                            detail: { topic_id: decision.topic_id },
                          }),
                        );
                      }
                    }}
                    aria-label={t("timeline_view.open_topic_aria", {
                      topic: topicTitle,
                    })}
                  >
                    <p className="timeline-entry__statement">
                      {decision.statement}
                    </p>
                    <div className="timeline-entry__meta">
                      <span className="timeline-entry__topic-badge">
                        {topicIcon ? (
                          <span
                            className="timeline-entry__topic-icon"
                            aria-hidden="true"
                          >
                            {topicIcon}
                          </span>
                        ) : null}
                        <span className="timeline-entry__topic-title">
                          {topicTitle}
                        </span>
                      </span>
                      {relTime ? (
                        <time
                          className="timeline-entry__time"
                          dateTime={decision.created_at}
                        >
                          {relTime}
                        </time>
                      ) : null}
                    </div>
                  </button>
                </div>
              );
            })}
          </div>
        ))}
      </div>
    </div>
  );
}
