/**
 * SummaryView — renders a generated plan summary.
 *
 * Shown inside LlmModesPanel's Summary tab. The backend returns clean
 * prose already (no markdown parsing needed here); we split on blank
 * lines and render each paragraph as a <p>. The title bar, domain
 * framing chip, regenerate pill, and copy pill round out the warm
 * editorial surface.
 *
 * When the summary's ``domain_framing`` smells software-adjacent,
 * ``scaffoldSlot`` (owned by the caller) is rendered under the prose
 * body — that's the CTA + progress + result surface for the paid-tier
 * code-scaffold feature. Keeping the decision here means SummaryView
 * stays the one place that knows "is this a software project" and the
 * caller only has to render a slot.
 *
 * Loading and error shapes are also handled here so the panel host can
 * switch on `status` without caring about the internals.
 */

import { type ReactElement, type ReactNode } from "react";
import { t } from "../../i18n";

// Domain-framing tokens that trigger the scaffold CTA. Matched
// case-insensitively against ``domain_framing``; any substring hit
// is enough. Kept terse — false negatives are fine (the user can
// regenerate), false positives are the expensive case.
const SOFTWARE_DOMAIN_TOKENS = ["software", "product", "app", "tech"];

/** True iff ``framing`` reads as a software-adjacent plan. */
export function isSoftwareDomain(framing: string | undefined): boolean {
  if (!framing) return false;
  const lower = framing.toLowerCase();
  return SOFTWARE_DOMAIN_TOKENS.some((token) => lower.includes(token));
}

export type SummaryViewProps = {
  summary: string;
  suggested_title?: string;
  domain_framing?: string;
  /** Project / feedback / issue title rendered as the card heading.
   *  Product decision: the card title is whatever this
   *  summary is implementing — falls back to "Plan Summary" if not
   *  supplied, but in practice LlmModesPanel passes the active
   *  project's title. */
  cardTitle?: string;
  onRegenerate: () => Promise<void>;
  onClose: () => void;
  onCopyMarkdown: () => void;
  onExportPdf?: () => void;
  /** Rendered below the summary body iff the domain smells software-y. */
  scaffoldSlot?: ReactNode;
};

// Parse plain-text paragraphs (double-newline separated). No markdown.
function splitParagraphs(text: string): string[] {
  const trimmed = (text || "").trim();
  if (!trimmed) return [];
  return trimmed
    .split(/\n{2,}/)
    .map((p) => p.replace(/\n+/g, " ").trim())
    .filter((p) => p.length > 0);
}

export function SummaryView(props: SummaryViewProps): ReactElement {
  const { summary, suggested_title, domain_framing, cardTitle, onRegenerate,
    onClose, onCopyMarkdown, onExportPdf, scaffoldSlot } = props;
  const paragraphs = splitParagraphs(summary);
  const showScaffold = isSoftwareDomain(domain_framing) && scaffoldSlot;
  const title = cardTitle?.trim() || t("summary_view.title");

  return (
    <div className="llm-card" role="region" aria-label={t("summary_view.aria")}>
      <header className="llm-card__titlebar">
        <span className="llm-card__eyebrow">{t("summary_view.eyebrow")}</span>
        <h2 className="llm-card__title">{title}</h2>
        <button
          type="button"
          className="llm-pill"
          onClick={onClose}
          aria-label={t("summary_view.close_aria")}
        >
          {t("summary_view.close")}
        </button>
      </header>

      {suggested_title ? (
        <p className="summary-suggested-title">{suggested_title}</p>
      ) : null}

      <div className="summary-body">
        {paragraphs.length === 0 ? (
          <div className="summary-empty">
            <p className="summary-empty__text">
              <em>{t("summary_view.empty")}</em>
            </p>
            <p className="summary-empty__hint">
              {t("summary_view.empty_hint")}
            </p>
          </div>
        ) : (
          paragraphs.map((p, i) => <p key={i}>{p}</p>)
        )}
      </div>

      {showScaffold ? scaffoldSlot : null}

      <footer className="summary-footer">
        {domain_framing ? (
          <span className="summary-framing">
            <span className="summary-framing__label">{t("summary_view.framing_label")}</span>
            <span className="llm-pill llm-pill--sage">{domain_framing}</span>
          </span>
        ) : null}
        <button
          type="button"
          className="llm-pill"
          onClick={() => {
            void onRegenerate();
          }}
        >
          {t("summary_view.regenerate")}
        </button>
        <button type="button" className="llm-pill" onClick={onCopyMarkdown}>
          {t("summary_view.copy_markdown")}
        </button>
        {onExportPdf ? (
          <button type="button" className="llm-pill" onClick={onExportPdf}>
            {t("summary_view.export_pdf")}
          </button>
        ) : null}
      </footer>
    </div>
  );
}

/* ---- Loading and error surfaces -------------------------------------- */

export function SummaryViewLoading(): ReactElement {
  return (
    <div className="llm-card" aria-busy="true" aria-label={t("summary_view.loading_aria")}>
      <header className="llm-card__titlebar">
        <span className="llm-card__eyebrow">{t("summary_view.eyebrow")}</span>
        <h2 className="llm-card__title">{t("summary_view.title")}</h2>
      </header>
      <p className="llm-status">{t("summary_view.loading_status")}</p>
      <div className="summary-body" aria-hidden="true">
        <div className="summary-skeleton__line" style={{ width: "92%" }} />
        <div className="summary-skeleton__line" style={{ width: "96%" }} />
        <div className="summary-skeleton__line" style={{ width: "88%" }} />
        <div className="summary-skeleton__line" style={{ width: "72%" }} />
        <div className="summary-skeleton__gap" />
        <div className="summary-skeleton__line" style={{ width: "90%" }} />
        <div className="summary-skeleton__line" style={{ width: "94%" }} />
        <div className="summary-skeleton__line" style={{ width: "66%" }} />
        <div className="summary-skeleton__gap" />
        <div className="summary-skeleton__line" style={{ width: "86%" }} />
        <div className="summary-skeleton__line" style={{ width: "58%" }} />
      </div>
    </div>
  );
}

export type SummaryViewErrorProps = {
  message: string;
  onRetry: () => void;
};

export function SummaryViewError(
  { message, onRetry }: SummaryViewErrorProps,
): ReactElement {
  return (
    <div className="llm-error" role="alert">
      <div className="llm-error__body">
        {t("summary_view.error_body", { message })}
      </div>
      <button type="button" className="llm-error__retry" onClick={onRetry}>
        {t("summary_view.error_retry")}
      </button>
    </div>
  );
}
