// Inspira — DocumentView (#094 / Item 3 redesign).
//
// Long-form doc renderer that replaces the per-phase BusinessPlan
// pager. Mounts inside the 3rd tab on LlmModesPanel — same tab key
// ("business_plan") for deep-link backcompat, but the displayed label
// is doc-type-aware ("Business Plan" / "PRD" / "Story Outline" / ...).
// Doc-type derived upstream from project.metadata.domain via
// docTypeForDomain — DocumentView itself just renders. Career and
// personal projects (unmapped domains) never reach this component;
// LlmModesPanel renders an unmapped-domain fallback for those.
//
// 4b shipped render-only; 4c (this file) adds inline edit-on-click +
// regenerate-warn-edited confirmation. The optimistic-UI splice and
// revert-on-4xx live in InspiraApp's onPatchDocumentSection handler
// (4d) — this component just calls onPatchSection and awaits.
//
// Anchored on:
//   - api.ts DocumentView + DocumentSection types
//   - renderMarkdown (FE allowlist; third defensive layer per
//     #094 commit-3 + commit-5 security reviews)
//   - useScrollSpy hook (scroll-spy with rootMargin trick)
//   - llm-modes.css `.document-view*` classes (see 4e)
//
// Accessibility:
//   - Topbar: <h1> for doc-type label.
//   - Sections: <section id> with <h2>; scroll-spy reflects via
//     aria-current="location" on the active sidenav link.
//   - <aside> with explicit aria-label.
//   - Mobile drawer: <dialog>-style toggle with aria-expanded, focus
//     traps not implemented (single-screen modal; user can dismiss
//     via the toggle or any link click).
//   - Reduced-motion: scrollIntoView uses behavior:"auto" when the
//     user has prefers-reduced-motion: reduce.

import {
  type MouseEvent as ReactMouseEvent,
  type ReactElement,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import { t } from "../../i18n";
import { renderMarkdown } from "../marketing/renderMarkdown";
import { formatRelativeTime } from "../../lib/relativeTime";
import { useScrollSpy } from "./useScrollSpy";
import {
  type DocType,
  type DocumentSection,
  type DocumentSectionPatchBody,
  type DocumentView as DocumentViewData,
} from "../inspira/api";
import { VALID_DOC_TYPES } from "../inspira/docTypeMap";

export type DocumentViewProps = {
  projectId: string;
  /** Resolved doc_type for the project's domain, or null when the
   *  domain is unmapped (career, personal, or missing). When null,
   *  the empty state surfaces the unmapped-domain warning AND the
   *  picker so the user can still pick a doc-type and generate
   *  via the BE override path. */
  docType: DocType | null;
  /** Latest known document for (project_id, doc_type), or null
   *  before the prefetch lands or when no document has been
   *  generated yet. */
  document: DocumentViewData | null;
  /** True while a generation is in flight (POST 202 fired, poller
   *  still running). Distinct from document.status === "in_progress"
   *  because the prefetch may briefly be null between POST and the
   *  first GET. */
  pending: boolean;
  /** True when topics/decisions changed after the document was
   *  generated. Renders the warm-editorial banner. Wired in 4c. */
  stale: boolean;
  /** Cap usage for the current user (any doc type — the cap is
   *  shared across all 7 doc types per founder lock-in, sharing the
   *  business_plan_usage table). */
  capUsed: number;
  /** Cap limit (Pro 1, Frontier 100, never "unlimited"). */
  capLimit: number;
  /** Generate (or regenerate) the document. The handler in
   *  InspiraApp branches on whether a completed doc already exists
   *  (regenerate path), kicks off the BG task, and spawns the
   *  poller. On error, shows a typed toast and rejects so the
   *  component can clear its in-flight flag.
   *
   *  Optional `docTypeOverride` (post-#094 follow-up): when supplied,
   *  the BE generates as that doc-type instead of the project-domain
   *  derived value. Only meaningful in the empty-state Generate path
   *  — once a doc exists, regenerate uses the existing doc's type.
   *  Project metadata is unchanged. */
  onGenerate: (docTypeOverride?: DocType) => Promise<void>;
  /** PATCH a single section. 4c wires this up; in 4b the prop is
   *  accepted but unused (so InspiraApp's wiring in 4d can pass it
   *  through without a follow-up signature change). */
  onPatchSection?: (
    sectionId: string,
    body: DocumentSectionPatchBody,
  ) => Promise<void>;
};

/** Doc-type-aware copy lookup with fallback. Tries
 *  `llm_modes.document.{pattern}.{docType}` first; if the i18n stub
 *  returns the key (key missing in the locale), falls back to
 *  `llm_modes.document.{pattern}_fallback`. Protects against drift
 *  between this component and the en/es JSON when a new doc_type is
 *  added but its strings haven't been translated yet. */
function docTypeCopy(pattern: string, docType: DocType): string {
  const key = `llm_modes.document.${pattern}.${docType}`;
  const resolved = t(key);
  return resolved === key
    ? t(`llm_modes.document.${pattern}_fallback`)
    : resolved;
}

const emptyTitle = (docType: DocType): string =>
  docTypeCopy("empty_title", docType);
const emptyBody = (docType: DocType): string =>
  docTypeCopy("empty_body", docType);
const generateCta = (docType: DocType): string =>
  docTypeCopy("generate_cta", docType);
const tabLabelForDocType = (docType: DocType): string =>
  docTypeCopy("tab_label", docType);

/** Cap-pill copy. Singular when limit is 1 (Pro). Plural otherwise.
 *  Founder lock-in: never says "unlimited"; even at 0/100 we render
 *  "0/100 plans this month". */
function capPillCopy(used: number, limit: number): string {
  const key =
    limit === 1
      ? "llm_modes.document.cap_pill_singular"
      : "llm_modes.document.cap_pill_plural";
  return t(key, { used: String(used), cap: String(limit) });
}

/** prefers-reduced-motion check, evaluated lazily so SSR / jsdom
 *  return a sane default. */
function prefersReducedMotion(): boolean {
  if (typeof window === "undefined" || typeof window.matchMedia === "undefined") {
    return false;
  }
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

export function DocumentView(props: DocumentViewProps): ReactElement {
  const {
    docType,
    document: doc,
    pending,
    stale,
    capUsed,
    capLimit,
    onGenerate,
  } = props;

  // Local in-flight flag mirrors the BusinessPlanPager pattern — kicks
  // on at button click, off on resolution. The parent's `pending` prop
  // also reflects an in-flight poll; we OR them when disabling the CTA.
  const [generating, setGenerating] = useState<boolean>(false);

  // Inline-edit state. Only one section can be in edit mode at a time
  // (mirrors BusinessPlanPager's editingIndex pattern). The draft
  // fields hold the user's in-progress text; on Save we PATCH only
  // the fields that diverge from the original (BE Pydantic enforces
  // at-least-one).
  const [editingSectionId, setEditingSectionId] = useState<string | null>(null);
  const [editTitle, setEditTitle] = useState<string>("");
  const [editProse, setEditProse] = useState<string>("");
  const [editSaving, setEditSaving] = useState<boolean>(false);

  // Tracks whether the user has saved at least one inline edit on this
  // document. Used to surface a confirmation prompt before regenerate
  // (mirrors BusinessPlanPager:125-134's regenerate_warn_edited path).
  // Resets when the document_id changes (new generation lands a fresh
  // doc with no edits).
  const [hasUserEdits, setHasUserEdits] = useState<boolean>(false);
  const lastDocumentIdRef = useRef<string | null>(null);
  if (doc && doc.document_id !== lastDocumentIdRef.current) {
    lastDocumentIdRef.current = doc.document_id;
    if (hasUserEdits) setHasUserEdits(false);
  }

  const sections: DocumentSection[] = useMemo(
    () => doc?.content?.sections ?? [],
    [doc],
  );
  const sectionIds = useMemo(
    () => sections.map((s) => s.section_id),
    [sections],
  );

  // Drawer state for mobile <1024px. CSS hides the toggle button at
  // ≥1024px, so the open state is harmless on desktop.
  const [drawerOpen, setDrawerOpen] = useState<boolean>(false);

  // #094 follow-up: per-session doc-type override. The user can pick
  // a different doc-type from the empty-state picker if the auto-
  // derived value (from project domain) was wrong, OR pick one when
  // the project's domain is unmapped (career / personal / missing).
  // The selection applies to THIS generation only — project metadata
  // is unchanged. Persistent override is tracked as #097.
  //
  // Initialized to the prop docType when present, "business_plan"
  // when null (a sensible default for the picker; user can change
  // before generating). Resets on prop change so a project switch
  // shows the new project's default.
  const [selectedDocType, setSelectedDocType] = useState<DocType>(
    docType ?? "business_plan",
  );
  // hasManuallyPicked: true once the user has explicitly chosen from
  // the dropdown. Drives the empty-state copy: when domain is unmapped
  // AND the user hasn't picked, we show the "no doc type for this
  // project" warning rather than implying we inferred "business_plan".
  const [hasManuallyPicked, setHasManuallyPicked] = useState<boolean>(false);
  const lastPropDocTypeRef = useRef<DocType | null>(docType);
  if (docType !== lastPropDocTypeRef.current) {
    lastPropDocTypeRef.current = docType;
    setSelectedDocType(docType ?? "business_plan");
    setHasManuallyPicked(false);
  }
  const showUnmappedCopy = docType === null && !hasManuallyPicked;

  // a11y: dismiss the mobile drawer on Escape (matches modal-dismiss
  // convention; no other key should trigger close). Only fires when
  // the drawer is actually open so we don't compete with other Esc
  // handlers (edit textarea, panel close).
  useEffect(() => {
    if (!drawerOpen) return;
    if (typeof window === "undefined") return;
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === "Escape") setDrawerOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [drawerOpen]);

  // a11y: auto-focus the title input when entering edit mode. The
  // textarea is the more common edit target, but the title comes
  // first in DOM order so screen readers announce the section
  // identity first. Caller can Tab to the textarea directly.
  useEffect(() => {
    if (!editingSectionId) return;
    if (typeof window === "undefined") return;
    const el = document.getElementById(
      `docsection-edit-title-${editingSectionId}`,
    );
    if (el && el instanceof HTMLInputElement) {
      el.focus();
      // Position cursor at end so the user can append without
      // re-selecting.
      el.setSelectionRange(el.value.length, el.value.length);
    }
  }, [editingSectionId]);

  const bodyRef = useRef<HTMLDivElement | null>(null);
  const activeSectionId = useScrollSpy(sectionIds, bodyRef);

  const handleGenerate = useCallback(
    async (isRegenerate: boolean): Promise<void> => {
      if (generating || pending) return;
      // Confirmation when regenerating an edited document — losing
      // user-saved edits. Mirrors BusinessPlanPager:125-134.
      if (
        isRegenerate
        && hasUserEdits
        && typeof window !== "undefined"
      ) {
        const confirmed = window.confirm(
          t("llm_modes.document.regenerate_warn_edited"),
        );
        if (!confirmed) return;
      }
      setGenerating(true);
      try {
        // For the empty-state Generate path, pass the picker selection
        // (may differ from prop docType if the user overrode). For
        // regenerate paths the existing doc dictates the type, so we
        // pass undefined (BE will derive from the existing doc's
        // doc_type via the in-flight idempotency check anyway).
        const override = isRegenerate ? undefined : selectedDocType;
        await onGenerate(override);
      } catch (err) {
        // Parent surfaces the typed toast; we just reset the flag.
        console.warn("[document_view] generate error", err);
      } finally {
        setGenerating(false);
      }
    },
    [generating, hasUserEdits, onGenerate, pending, selectedDocType],
  );

  const startEdit = useCallback((section: DocumentSection): void => {
    setEditingSectionId(section.section_id);
    setEditTitle(section.title);
    setEditProse(section.prose_markdown);
  }, []);

  const cancelEdit = useCallback((): void => {
    setEditingSectionId(null);
    setEditTitle("");
    setEditProse("");
  }, []);

  const saveEdit = useCallback(
    async (originalSection: DocumentSection): Promise<void> => {
      if (editingSectionId !== originalSection.section_id) return;
      if (!props.onPatchSection) return;
      // Build a minimal PATCH body — only fields that changed from
      // the original. BE Pydantic 422s on empty body so we early-out
      // when nothing changed (no-op save = close editor).
      const body: DocumentSectionPatchBody = {};
      if (editTitle.trim() && editTitle !== originalSection.title) {
        body.title = editTitle.trim();
      }
      if (editProse !== originalSection.prose_markdown) {
        body.prose_markdown = editProse;
      }
      if (body.title === undefined && body.prose_markdown === undefined) {
        cancelEdit();
        return;
      }
      setEditSaving(true);
      try {
        await props.onPatchSection(originalSection.section_id, body);
        setHasUserEdits(true);
        cancelEdit();
      } catch (err) {
        // Parent's handler surfaces the toast + reverts the
        // optimistic splice. We keep the editor open so the user can
        // retry without re-typing.
        console.warn("[document_view] section edit save failed", err);
      } finally {
        setEditSaving(false);
      }
    },
    [cancelEdit, editingSectionId, editProse, editTitle, props],
  );

  const handleJumpToSection = useCallback(
    (sectionId: string) =>
      (e: ReactMouseEvent<HTMLAnchorElement>): void => {
        // Custom scroll so we can honor reduced-motion + close the
        // drawer atomically. preventDefault() stops the browser from
        // updating the URL hash (we don't want history pollution from
        // every scroll-jump).
        e.preventDefault();
        const el = document.getElementById(sectionId);
        if (!el) return;
        el.scrollIntoView({
          behavior: prefersReducedMotion() ? "auto" : "smooth",
          block: "start",
        });
        setDrawerOpen(false);
      },
    [],
  );

  const status = doc?.status ?? null;
  const showEmpty = !pending && doc === null && !generating;
  const showPending = pending || generating || status === "in_progress";
  const showFailed = !pending && !generating && status === "failed";
  const showCompleted = !pending && !generating && status === "completed";

  return (
    <div className="document-view">
      {/* Sticky topbar — doc-type label + last-generated + cap pill +
          Regenerate (or Generate when empty). When the project's
          domain is unmapped (docType prop is null) AND the user
          hasn't manually picked from the dropdown yet, show the
          generic "Document" label rather than the picker default
          (avoids implying we inferred a type). */}
      <div className="document-view__topbar">
        <h1 className="document-view__doctype-label">
          {showUnmappedCopy
            ? t("llm_modes.document.tab_label_fallback")
            : tabLabelForDocType(selectedDocType)}
        </h1>

        {showCompleted && doc?.completed_at ? (
          <span className="document-view__last-generated">
            {t("llm_modes.document.last_generated", {
              time: formatRelativeTime(doc.completed_at),
            })}
          </span>
        ) : null}

        <span className="document-view__cap-pill" aria-live="polite">
          {capPillCopy(capUsed, capLimit)}
        </span>

        {showCompleted ? (
          <button
            type="button"
            className="llm-pill"
            onClick={() => void handleGenerate(true)}
            disabled={generating || pending || capUsed >= capLimit}
          >
            {t("llm_modes.document.regenerate_cta")}
          </button>
        ) : null}
      </div>

      {/* Stale banner — visual surface ready in 4b; trigger wired in 4c. */}
      {stale && showCompleted ? (
        <div className="document-view__stale-banner" role="status">
          <p className="document-view__stale-banner-copy">
            {t("llm_modes.document.stale_banner")}
          </p>
          <button
            type="button"
            className="llm-pill"
            onClick={() => void handleGenerate(true)}
            disabled={generating || pending || capUsed >= capLimit}
          >
            {t("llm_modes.document.regenerate_cta")}
          </button>
        </div>
      ) : null}

      {/* Body — branch on state. Empty / Pending / Failed render
          centered; Completed renders the two-column long-form layout. */}
      {showEmpty ? (
        <div className="document-view__empty">
          <h2 className="document-view__empty-title">
            {showUnmappedCopy
              ? t("llm_modes.document.unmapped_domain_title")
              : emptyTitle(selectedDocType)}
          </h2>
          <p className="document-view__empty-body">
            {showUnmappedCopy
              ? t("llm_modes.document.unmapped_domain_body")
              : emptyBody(selectedDocType)}
          </p>
          <button
            type="button"
            className="llm-pill llm-pill--primary"
            onClick={() => void handleGenerate(false)}
            disabled={generating || pending || capUsed >= capLimit}
          >
            {showUnmappedCopy
              ? t("llm_modes.document.generate_cta_fallback")
              : generateCta(selectedDocType)}
          </button>

          {/* Doc-type picker (#094 follow-up). Lets the user override
              the auto-derived doc-type before generating, in case
              kickoff inferred the wrong domain. Always rendered so
              unmapped-domain projects can still generate (the picker
              IS the doc-type resolution path for those). Native
              <select> for accessibility (keyboard, screen readers,
              mobile UI come for free). The selection is session-local;
              project metadata is unchanged. */}
          <div className="document-view__doctype-picker">
            <label
              htmlFor="document-view-doctype-select"
              className="document-view__doctype-picker-label"
            >
              {showUnmappedCopy
                ? t("llm_modes.document.pick_doc_type_label")
                : t("llm_modes.document.change_doc_type_label")}
            </label>
            <select
              id="document-view-doctype-select"
              className="document-view__doctype-picker-select"
              value={selectedDocType}
              onChange={(e) => {
                setSelectedDocType(e.target.value as DocType);
                setHasManuallyPicked(true);
              }}
              disabled={generating || pending}
              aria-label={t("llm_modes.document.aria_change_doc_type")}
            >
              {VALID_DOC_TYPES.map((dt) => (
                <option key={dt} value={dt}>
                  {tabLabelForDocType(dt)}
                </option>
              ))}
            </select>
          </div>
        </div>
      ) : null}

      {showPending && !showEmpty ? (
        <div className="document-view__generating" role="status">
          <p className="document-view__generating-title">
            {t("llm_modes.document.generating_title")}
          </p>
          <p className="document-view__generating-body">
            {t("llm_modes.document.generating_body")}
          </p>
        </div>
      ) : null}

      {showFailed ? (
        <div className="document-view__failed" role="alert">
          <p className="document-view__failed-title">
            {t("llm_modes.document.failed_title")}
          </p>
          {doc?.error_message ? (
            <p className="document-view__failed-detail">{doc.error_message}</p>
          ) : null}
          <button
            type="button"
            className="llm-pill"
            onClick={() => void handleGenerate(false)}
            disabled={generating || pending || capUsed >= capLimit}
          >
            {t("llm_modes.document.failed_retry_cta")}
          </button>
        </div>
      ) : null}

      {showCompleted ? (
        <div className="document-view__layout">
          {/* Mobile drawer toggle — visible <1024px via CSS. */}
          <button
            type="button"
            className="document-view__drawer-toggle"
            onClick={() => setDrawerOpen((o) => !o)}
            aria-expanded={drawerOpen}
            aria-controls="document-view-sidenav"
          >
            {t("llm_modes.document.sections_drawer_title")}
          </button>

          {/* Sticky desktop aside + mobile drawer share the same nav
              markup; CSS toggles between sticky-left and bottom-sheet
              based on viewport width. */}
          <aside
            id="document-view-sidenav"
            className={
              drawerOpen
                ? "document-view__sidenav document-view__sidenav--open"
                : "document-view__sidenav"
            }
            aria-label={t("llm_modes.document.aria_sidenav_label")}
          >
            <nav>
              <ol className="document-view__sidenav-list">
                {sections.map((s) => {
                  const isActive = activeSectionId === s.section_id;
                  return (
                    <li
                      key={s.section_id}
                      className="document-view__sidenav-item"
                    >
                      <a
                        href={`#${s.section_id}`}
                        className={
                          isActive
                            ? "document-view__sidenav-link document-view__sidenav-link--active"
                            : "document-view__sidenav-link"
                        }
                        aria-current={isActive ? "location" : undefined}
                        aria-label={t("llm_modes.document.aria_jump_to_section", {
                          section: s.title,
                        })}
                        onClick={handleJumpToSection(s.section_id)}
                      >
                        {s.title}
                      </a>
                    </li>
                  );
                })}
              </ol>
            </nav>
          </aside>

          <main
            ref={bodyRef}
            className="document-view__body"
            aria-label={t("llm_modes.document.aria_main_label")}
          >
            {sections.map((s) => {
              const isEditing = editingSectionId === s.section_id;
              return (
              <section
                key={s.section_id}
                id={s.section_id}
                className="document-view__section"
                aria-labelledby={`docsection-title-${s.section_id}`}
              >
                {isEditing ? (
                  <>
                    <label
                      htmlFor={`docsection-edit-title-${s.section_id}`}
                      className="document-view__edit-label"
                    >
                      {t("llm_modes.document.edit_title_label")}
                    </label>
                    <input
                      id={`docsection-edit-title-${s.section_id}`}
                      type="text"
                      className="document-view__edit-title"
                      value={editTitle}
                      maxLength={200}
                      onChange={(e) => setEditTitle(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Escape") {
                          e.preventDefault();
                          cancelEdit();
                        }
                      }}
                    />
                    <label
                      htmlFor={`docsection-edit-prose-${s.section_id}`}
                      className="document-view__edit-label"
                    >
                      {t("llm_modes.document.edit_prose_label")}
                    </label>
                    <textarea
                      id={`docsection-edit-prose-${s.section_id}`}
                      className="document-view__edit-textarea"
                      value={editProse}
                      maxLength={4000}
                      rows={12}
                      onChange={(e) => setEditProse(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Escape") {
                          e.preventDefault();
                          cancelEdit();
                        }
                      }}
                    />
                    <div className="document-view__edit-actions">
                      <button
                        type="button"
                        className="llm-pill llm-pill--primary"
                        onClick={() => void saveEdit(s)}
                        disabled={editSaving || editTitle.trim().length === 0}
                      >
                        {t("llm_modes.document.save_cta")}
                      </button>
                      <button
                        type="button"
                        className="llm-pill"
                        onClick={cancelEdit}
                        disabled={editSaving}
                      >
                        {t("llm_modes.document.cancel_cta")}
                      </button>
                    </div>
                  </>
                ) : (
                  <>
                <h2
                  id={`docsection-title-${s.section_id}`}
                  className="document-view__section-title"
                >
                  {s.title}
                </h2>

                <div className="document-view__prose">
                  {renderMarkdown(s.prose_markdown ?? "")}
                </div>

                {s.key_points.length > 0 ? (
                  <div className="document-view__key-points">
                    <h3 className="document-view__key-points-label">
                      {t("llm_modes.document.key_points_label")}
                    </h3>
                    <ul>
                      {s.key_points.map((kp, i) => (
                        <li key={`${s.section_id}-kp-${i}`}>{kp}</li>
                      ))}
                    </ul>
                  </div>
                ) : null}

                {s.cited_topics.length > 0 ? (
                  <div className="document-view__cited-chips">
                    <span className="document-view__cited-chips-label">
                      {t("llm_modes.document.cited_topics_label")}
                    </span>
                    {s.cited_topics.map((ct, i) => (
                      <span
                        key={`${s.section_id}-ct-${i}`}
                        className="document-view__cited-chip"
                      >
                        {ct}
                      </span>
                    ))}
                  </div>
                ) : null}

                {props.onPatchSection ? (
                  <div className="document-view__section-actions">
                    <button
                      type="button"
                      className="llm-pill"
                      onClick={() => startEdit(s)}
                      disabled={editingSectionId !== null || editSaving}
                      aria-label={t("llm_modes.document.aria_edit_section", {
                        section: s.title,
                      })}
                    >
                      {t("llm_modes.document.edit_cta")}
                    </button>
                  </div>
                ) : null}
                  </>
                )}
              </section>
              );
            })}
          </main>
        </div>
      ) : null}
    </div>
  );
}

export default DocumentView;
