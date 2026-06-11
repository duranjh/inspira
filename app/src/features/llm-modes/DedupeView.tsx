/**
 * DedupeView — renders merge proposals with accept/reject per pair.
 *
 * Backend returns an array of {topic_a_id, topic_b_id, overlap_reason,
 * suggested_merged_title, suggested_action}. We look up the two topics
 * by id from the provided `topicsById` map to show titles + icons,
 * and render each pair as its own paper card. The two topic chips sit
 * side-by-side separated by a dotted sage arrow; the overlap reason
 * reads below; a suggested merge title appears on "merge" action
 * cards; accept/reject pills live at the bottom.
 *
 * Accept/reject callbacks are stubbed at the container level — no
 * "apply merge" endpoint exists yet. The container shows a "coming
 * soon" toast on accept and removes the proposal locally either way.
 */

import { type ReactElement } from "react";
import { t } from "../../i18n";

export type MergeProposal = {
  topic_a_id: string;
  topic_b_id: string;
  overlap_reason: string;
  suggested_merged_title: string;
  suggested_action: "merge" | "keep_both_but_note";
};

export type TopicStub = { title: string; icon: string };

export type DedupeViewProps = {
  proposals: MergeProposal[];
  topicsById: Map<string, TopicStub>;
  onAcceptProposal: (p: MergeProposal) => Promise<void>;
  onRejectProposal: (p: MergeProposal) => Promise<void>;
  onClose: () => void;
};

export function DedupeView(props: DedupeViewProps): ReactElement {
  const { proposals, topicsById, onAcceptProposal,
    onRejectProposal, onClose } = props;

  if (proposals.length === 0) {
    return (
      <>
        <div className="llm-card" role="region" aria-label={t("dedupe_view.aria")}>
          <header className="llm-card__titlebar">
            <span className="llm-card__eyebrow">{t("dedupe_view.eyebrow")}</span>
            <h2 className="llm-card__title">{t("dedupe_view.title")}</h2>
            <button
              type="button"
              className="llm-pill"
              onClick={onClose}
              aria-label={t("dedupe_view.close_aria")}
            >
              {t("dedupe_view.close")}
            </button>
          </header>
          <div className="dedupe-empty">
            <p className="dedupe-empty__text">{t("dedupe_view.empty")}</p>
            <p className="dedupe-empty__hint">{t("dedupe_view.empty_hint")}</p>
          </div>
        </div>
      </>
    );
  }

  return (
    <>
      <div className="llm-card" role="region" aria-label={t("dedupe_view.aria")}>
        <header className="llm-card__titlebar">
          <span className="llm-card__eyebrow">{t("dedupe_view.eyebrow")}</span>
          <h2 className="llm-card__title">
            {t("dedupe_view.title_count", { count: String(proposals.length) })}
          </h2>
          <button
            type="button"
            className="llm-pill"
            onClick={onClose}
            aria-label={t("dedupe_view.close_aria")}
          >
            {t("dedupe_view.close")}
          </button>
        </header>
        <div className="dedupe-stack">
          {proposals.map((p) => (
            <DedupeCard
              key={`${p.topic_a_id}-${p.topic_b_id}`}
              proposal={p}
              topicsById={topicsById}
              onAccept={onAcceptProposal}
              onReject={onRejectProposal}
            />
          ))}
        </div>
      </div>
    </>
  );
}

type DedupeCardProps = {
  proposal: MergeProposal;
  topicsById: Map<string, TopicStub>;
  onAccept: (p: MergeProposal) => Promise<void>;
  onReject: (p: MergeProposal) => Promise<void>;
};

function DedupeCard(
  { proposal, topicsById, onAccept, onReject }: DedupeCardProps,
): ReactElement {
  const a = topicsById.get(proposal.topic_a_id);
  const b = topicsById.get(proposal.topic_b_id);
  const isMerge = proposal.suggested_action === "merge";

  return (
    <div className="dedupe-card">
      <div className="dedupe-card__pair">
        <span className="dedupe-chip">
          <span className="dedupe-chip__eyebrow">A</span>
          <span className="dedupe-chip__icon">{a?.icon ?? "?"}</span>
          <span className="dedupe-chip__title">
            {a?.title ?? t("dedupe_view.unknown_topic")}
          </span>
        </span>
        <span className="dedupe-arrow" aria-hidden="true">
          {"\u2219\u2219\u2219\u2192"}
        </span>
        <span className="dedupe-chip">
          <span className="dedupe-chip__eyebrow">B</span>
          <span className="dedupe-chip__icon">{b?.icon ?? "?"}</span>
          <span className="dedupe-chip__title">
            {b?.title ?? t("dedupe_view.unknown_topic")}
          </span>
        </span>
      </div>

      <p className="dedupe-card__reason">{proposal.overlap_reason}</p>

      {isMerge ? (
        <div className="dedupe-card__merged">
          <div className="dedupe-card__merged-label">{t("dedupe_view.merged_label")}</div>
          <div className="dedupe-card__merged-title">
            {proposal.suggested_merged_title}
          </div>
        </div>
      ) : (
        <div className="dedupe-card__note">
          {t("dedupe_view.keep_both")}
        </div>
      )}

      <div className="dedupe-card__actions">
        <button
          type="button"
          className="llm-pill"
          onClick={() => {
            void onReject(proposal);
          }}
        >
          {t("dedupe_view.reject")}
        </button>
        <button
          type="button"
          className="llm-pill llm-pill--primary"
          onClick={() => {
            void onAccept(proposal);
          }}
        >
          {t("dedupe_view.accept")}
        </button>
      </div>
    </div>
  );
}

/* ---- Loading and error surfaces -------------------------------------- */

export function DedupeViewLoading(): ReactElement {
  return (
    <div className="llm-card" aria-busy="true" aria-label={t("dedupe_view.loading_aria")}>
      <header className="llm-card__titlebar">
        <span className="llm-card__eyebrow">{t("dedupe_view.eyebrow")}</span>
        <h2 className="llm-card__title">{t("dedupe_view.title")}</h2>
      </header>
      <p className="llm-status">{t("dedupe_view.loading_status")}</p>
      <div aria-hidden="true">
        <div className="summary-skeleton__line" style={{ width: "60%" }} />
        <div className="summary-skeleton__line" style={{ width: "84%" }} />
        <div className="summary-skeleton__gap" />
        <div className="summary-skeleton__line" style={{ width: "54%" }} />
        <div className="summary-skeleton__line" style={{ width: "76%" }} />
      </div>
    </div>
  );
}

export type DedupeViewErrorProps = {
  message: string;
  onRetry: () => void;
};

export function DedupeViewError(
  { message, onRetry }: DedupeViewErrorProps,
): ReactElement {
  return (
    <div className="llm-error" role="alert">
      <div className="llm-error__body">
        {t("dedupe_view.error_body", { message })}
      </div>
      <button type="button" className="llm-error__retry" onClick={onRetry}>
        {t("dedupe_view.error_retry")}
      </button>
    </div>
  );
}
