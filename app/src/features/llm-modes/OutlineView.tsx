/**
 * OutlineView — renders a hierarchical artifact outline.
 *
 * Top-level sections get serif roman numerals (I, II, III…). The
 * second level uses letters (A, B, C…), the third uses numbers. Each
 * level carries a short italic note beneath its title. No chevrons —
 * everything stays open, the hierarchy itself is the affordance.
 *
 * An artifact-type chip row sits above the tree so the user can
 * re-frame the outline without leaving the view. Clicking a chip
 * calls `onArtifactTypeChange`, which the host is expected to follow
 * up with an immediate regenerate.
 */

import { type ReactElement } from "react";
import { t } from "../../i18n";

export type OutlineSubSubsection = {
  number: string;
  title: string;
  note: string;
};

export type OutlineSubsection = {
  letter: string;
  title: string;
  note: string;
  sub_subsections?: OutlineSubSubsection[];
};

export type OutlineSection = {
  numeral: string;
  title: string;
  note: string;
  subsections?: OutlineSubsection[];
};

export type OutlineShape = {
  artifact_kind: string;
  title: string;
  sections: OutlineSection[];
};

export type OutlineViewProps = {
  outline: OutlineShape;
  onRegenerate: () => Promise<void>;
  onClose: () => void;
  onArtifactTypeChange: (t: string) => void;
  currentArtifactType: string;
};

// Preset artifact types. The value is sent to the backend `artifact_type`
// body field. The backend is permissive about the exact string — it just
// uses this to guide the model. Labels are retrieved via t() at render time.
// Includes a "Custom" escape hatch; we surface it as a sage chip when the
// current type isn't one of the presets.
const ARTIFACT_VALUES: readonly string[] = [
  "chapter outline",
  "pitch deck",
  "research report",
  "brief",
  "course syllabus",
  "screenplay structure",
];

// Map backend value → i18n key for the chip label.
function artifactLabel(value: string): string {
  const keyMap: Record<string, string> = {
    "chapter outline": "outline_view.artifact.chapter_outline",
    "pitch deck": "outline_view.artifact.pitch_deck",
    "research report": "outline_view.artifact.research_report",
    "brief": "outline_view.artifact.brief",
    "course syllabus": "outline_view.artifact.course_syllabus",
    "screenplay structure": "outline_view.artifact.screenplay_structure",
  };
  const key = keyMap[value.toLowerCase()];
  return key ? t(key) : value;
}

export function OutlineView(props: OutlineViewProps): ReactElement {
  const { outline, onRegenerate, onClose,
    onArtifactTypeChange, currentArtifactType } = props;

  const currentKnown = ARTIFACT_VALUES.some(
    (av) => av.toLowerCase() === currentArtifactType.toLowerCase(),
  );
  const sections = outline.sections ?? [];
  const hasAny = sections.length > 0;

  return (
    <>
      <div className="outline-chips">
        <span className="outline-chips__label">{t("outline_view.chips_label")}</span>
        {ARTIFACT_VALUES.map((av) => {
          const active =
            av.toLowerCase() === currentArtifactType.toLowerCase();
          return (
            <button
              key={av}
              type="button"
              className={
                "outline-chip" + (active ? " outline-chip--active" : "")
              }
              onClick={() => {
                if (active) return;
                onArtifactTypeChange(av);
              }}
            >
              {artifactLabel(av)}
            </button>
          );
        })}
        {!currentKnown && currentArtifactType ? (
          <button
            type="button"
            className="outline-chip outline-chip--active"
            onClick={() => {
              /* already active — noop */
            }}
          >
            {currentArtifactType}
          </button>
        ) : null}
      </div>

      <div className="llm-card" role="region" aria-label={t("outline_view.aria")}>
        <header className="llm-card__titlebar">
          <span className="llm-card__eyebrow">{t("outline_view.eyebrow")}</span>
          <h2 className="llm-card__title">
            {outline.title || t("outline_view.default_title")}
          </h2>
          <button
            type="button"
            className="llm-pill"
            onClick={() => {
              void onRegenerate();
            }}
          >
            {t("outline_view.regenerate")}
          </button>
          <button
            type="button"
            className="llm-pill"
            onClick={onClose}
            aria-label={t("outline_view.close_aria")}
          >
            {t("outline_view.close")}
          </button>
        </header>

        {hasAny ? (
          <ol className="outline-tree">
            {sections.map((sec, i) => (
              <li
                key={`${sec.numeral}-${i}`}
                className="outline-section"
              >
                <div className="outline-section__numeral">{sec.numeral}</div>
                <div>
                  <div className="outline-section__title">{sec.title}</div>
                  {sec.note ? (
                    <div className="outline-section__note">{sec.note}</div>
                  ) : null}
                  {sec.subsections && sec.subsections.length > 0 ? (
                    <ol className="outline-subsections">
                      {sec.subsections.map((sub, j) => (
                        <li
                          key={`${sub.letter}-${j}`}
                          className="outline-subsection"
                        >
                          <div className="outline-subsection__letter">
                            {sub.letter}
                          </div>
                          <div>
                            <div className="outline-subsection__title">
                              {sub.title}
                            </div>
                            {sub.note ? (
                              <div className="outline-subsection__note">
                                {sub.note}
                              </div>
                            ) : null}
                            {sub.sub_subsections &&
                            sub.sub_subsections.length > 0 ? (
                              <ol className="outline-sub-subsections">
                                {sub.sub_subsections.map((sss, k) => (
                                  <li
                                    key={`${sss.number}-${k}`}
                                    className="outline-sub-subsection"
                                  >
                                    <div className="outline-sub-subsection__number">
                                      {sss.number}
                                    </div>
                                    <div>
                                      <div className="outline-sub-subsection__title">
                                        {sss.title}
                                      </div>
                                      {sss.note ? (
                                        <div className="outline-sub-subsection__note">
                                          {sss.note}
                                        </div>
                                      ) : null}
                                    </div>
                                  </li>
                                ))}
                              </ol>
                            ) : null}
                          </div>
                        </li>
                      ))}
                    </ol>
                  ) : null}
                </div>
              </li>
            ))}
          </ol>
        ) : (
          <div className="outline-empty">
            <p className="outline-empty__text">{t("outline_view.empty")}</p>
            <p className="outline-empty__hint">{t("outline_view.empty_hint")}</p>
          </div>
        )}
      </div>
    </>
  );
}

/* ---- Loading and error surfaces -------------------------------------- */

export function OutlineViewLoading(): ReactElement {
  return (
    <div className="llm-card" aria-busy="true" aria-label={t("outline_view.loading_aria")}>
      <header className="llm-card__titlebar">
        <span className="llm-card__eyebrow">{t("outline_view.eyebrow")}</span>
        <h2 className="llm-card__title">{t("outline_view.default_title")}</h2>
      </header>
      <p className="llm-status">{t("outline_view.loading_status")}</p>
      <div aria-hidden="true">
        <div className="summary-skeleton__line" style={{ width: "40%" }} />
        <div className="summary-skeleton__line" style={{ width: "72%" }} />
        <div className="summary-skeleton__gap" />
        <div className="summary-skeleton__line" style={{ width: "36%" }} />
        <div className="summary-skeleton__line" style={{ width: "64%" }} />
        <div className="summary-skeleton__gap" />
        <div className="summary-skeleton__line" style={{ width: "42%" }} />
        <div className="summary-skeleton__line" style={{ width: "78%" }} />
      </div>
    </div>
  );
}

export type OutlineViewErrorProps = {
  message: string;
  onRetry: () => void;
};

export function OutlineViewError(
  { message, onRetry }: OutlineViewErrorProps,
): ReactElement {
  return (
    <div className="llm-error" role="alert">
      <div className="llm-error__body">
        {t("outline_view.error_body", { message })}
      </div>
      <button type="button" className="llm-error__retry" onClick={onRetry}>
        {t("outline_view.error_retry")}
      </button>
    </div>
  );
}
