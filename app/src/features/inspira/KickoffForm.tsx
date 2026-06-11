// The empty-canvas kickoff surface: "Tell me about your idea."
// First thing a user sees when they open a fresh project.

import { useRef, useState } from "react";

import type { AttachedSource } from "./api";
import { fetchUrlAsSource } from "./sources";
import { t } from "../../i18n";
import { PasteFeedbackDialog } from "./PasteFeedbackDialog";
import { TemplatePicker } from "../templates/TemplatePicker";

export type KickoffFormProps = {
  onSubmit: (idea: string, attachments: AttachedSource[]) => Promise<void>;
  disabled?: boolean;
  error?: string | null;
  // Pre-fill the textarea. Used when an anonymous visitor comes back to
  // the kickoff form after hitting the auth gate — we restore what they
  // had typed so they don't lose their idea.
  initialIdea?: string;
  // Called when the user picks a template card instead of typing an idea.
  // The parent turns this into a `createProjectFromTemplate` call and
  // opens the canvas directly, bypassing the planner's domain inference.
  onSelectTemplate?: (slug: string) => void;
  // Called when the user submits the markdown import mode. Receives the raw
  // markdown string; the parent is responsible for calling the API + opening
  // the canvas on the result.
  onImportMarkdown?: (markdown: string) => Promise<void>;
  // Called when the user clicks "Or import a JSON export →". The parent
  // opens the ImportFromJsonDialog which handles file parsing + API call.
  // Kept separate from onImportMarkdown because JSON imports take a parsed
  // object, not a raw string, and the UX is a file picker rather than a
  // textarea.
  onOpenImportJson?: () => void;
};

// File types we'll read inline as text. Binary files attach by name only
// and excerpt becomes a stub that at least tells the planner the file
// exists. Matches the cap used in TopicDetail's composer.
const KICKOFF_TEXT_MIME = /^text\/|^application\/(json|xml|x-yaml|yaml|csv|toml)/;
const KICKOFF_MAX_EXCERPT_CHARS = 8000;

// Kept as a named const so the translator-facing string lives in one
// place; the actual lookup runs per-render via t() inside the component.
const PLACEHOLDER_KEY = "kickoff.placeholder";

export function KickoffForm({
  onSubmit,
  disabled,
  error,
  initialIdea,
  onSelectTemplate,
  onImportMarkdown,
  onOpenImportJson,
}: KickoffFormProps) {
  const [idea, setIdea] = useState(initialIdea ?? "");
  const [selectedTemplateSlug, setSelectedTemplateSlug] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [attachments, setAttachments] = useState<AttachedSource[]>([]);
  const [fetchingUrl, setFetchingUrl] = useState(false);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  // Markdown import mode — swaps the normal textarea for a taller monospace
  // input and changes the submit button to "Import →".
  const [markdownMode, setMarkdownMode] = useState(false);
  const [markdownText, setMarkdownText] = useState("");

  // B3 (v4 connector promise) — Paste customer feedback dialog. On
  // submit, the synthesized prompt prefills the kickoff textarea so
  // the user can review before clicking "Plan it →".
  const [pasteFeedbackOpen, setPasteFeedbackOpen] = useState(false);

  // The "Or try:" rotating-chip row was removed — users read it as
  // redundant with the template picker cards below. The picker instantiates
  // real seeded projects, which does strictly more than a chip that just
  // prefilled the textarea.

  // User can submit once they've got either 20+ chars of idea OR at least
  // one attachment. An attached research report or brief is itself enough
  // context for the planner to map. A selected template slug also unlocks
  // the submit button via the template path.
  const usingTemplate = selectedTemplateSlug !== null;
  const canSubmit = markdownMode
    ? !submitting && !disabled && markdownText.trim().length > 0
    : !submitting && !disabled &&
      (usingTemplate || idea.trim().length >= 20 || attachments.length > 0);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit) return;
    setSubmitting(true);
    try {
      // Markdown import path — call the parent's onImportMarkdown handler
      if (markdownMode && onImportMarkdown) {
        await onImportMarkdown(markdownText.trim());
        return;
      }
      // If a template is selected, hand off to the template path and skip
      // the regular planner kickoff entirely.
      if (usingTemplate && selectedTemplateSlug && onSelectTemplate) {
        onSelectTemplate(selectedTemplateSlug);
        return;
      }
      await onSubmit(idea.trim(), attachments);
    } finally {
      setSubmitting(false);
    }
  }

  function handleTemplateSelect(slug: string) {
    // Toggle: clicking the already-selected card deselects it.
    setSelectedTemplateSlug((prev) => (prev === slug ? null : slug));
  }

  async function handleFilesPicked(files: FileList | null) {
    if (!files || files.length === 0) return;
    const next: AttachedSource[] = [];
    for (const f of Array.from(files)) {
      if (KICKOFF_TEXT_MIME.test(f.type)) {
        try {
          const text = await f.text();
          next.push({
            display_name: f.name,
            kind: f.type || "file:text",
            excerpt: text.slice(0, KICKOFF_MAX_EXCERPT_CHARS),
          });
        } catch (err) {
          console.warn("[Inspira] failed to read kickoff file", f.name, err);
        }
      } else {
        next.push({
          display_name: f.name,
          kind: f.type || "file:binary",
          excerpt: `(binary file, ${Math.round(f.size / 1024)} KB — content not inlined)`,
        });
      }
    }
    setAttachments((prev) => [...prev, ...next]);
  }

  async function handleAddLink() {
    if (submitting || disabled) return;
    const url = window.prompt(t("kickoff.link_prompt"));
    if (!url) return;
    const trimmed = url.trim();
    if (!trimmed) return;
    setFetchingUrl(true);
    try {
      const source = await fetchUrlAsSource(trimmed);
      setAttachments((prev) => [...prev, source]);
    } catch (err) {
      console.warn("[Inspira] failed to fetch URL for kickoff", trimmed, err);
    } finally {
      setFetchingUrl(false);
    }
  }

  function removeAttachment(idx: number) {
    setAttachments((prev) => prev.filter((_, i) => i !== idx));
  }

  return (
    <form className="kickoff" onSubmit={handleSubmit}>
      <div className="kickoff__inner">
        <div className="kickoff__eyebrow">{t("kickoff.eyebrow")}</div>
        <h1 id="kickoff-heading" className="kickoff__heading">
          {/* The serif heading needs its italicized phrase rendered inside
              an <em>, so we split `kickoff.heading` at its {your_idea}
              placeholder and render the emphasis via a second key. */}
          {t("kickoff.heading").split("{your_idea}")[0]}
          <em>{t("kickoff.your_idea")}</em>
          {t("kickoff.heading").split("{your_idea}")[1] ?? ""}
        </h1>
        {markdownMode ? (
          <textarea
            className="kickoff__textarea kickoff__textarea--markdown"
            value={markdownText}
            onChange={(e) => setMarkdownText(e.target.value)}
            placeholder={t("kickoff.markdown_placeholder")}
            rows={14}
            disabled={submitting || disabled}
            autoFocus
            aria-labelledby="kickoff-heading"
          />
        ) : (
          <textarea
            className="kickoff__textarea"
            value={idea}
            onChange={(e) => setIdea(e.target.value)}
            placeholder={t(PLACEHOLDER_KEY)}
            rows={8}
            disabled={submitting || disabled}
            autoFocus
            aria-labelledby="kickoff-heading"
          />
        )}
        {onImportMarkdown && !markdownMode ? (
          <div className="kickoff__markdown-toggle-row">
            <button
              type="button"
              className="kickoff__markdown-toggle"
              onClick={() => setPasteFeedbackOpen(true)}
              disabled={submitting || disabled}
            >
              {t("kickoff.paste_feedback_link")}
            </button>
            <button
              type="button"
              className="kickoff__markdown-toggle"
              onClick={() => {
                setMarkdownMode(true);
              }}
              disabled={submitting || disabled}
            >
              {t("kickoff.paste_markdown_link")}
            </button>
            {onOpenImportJson ? (
              <button
                type="button"
                className="kickoff__markdown-toggle"
                onClick={onOpenImportJson}
                disabled={submitting || disabled}
              >
                {t("kickoff.import_json_link")}
              </button>
            ) : null}
          </div>
        ) : markdownMode ? (
          <div className="kickoff__markdown-toggle-row">
            <button
              type="button"
              className="kickoff__markdown-toggle"
              onClick={() => {
                setMarkdownMode(false);
                setMarkdownText("");
              }}
              disabled={submitting || disabled}
            >
              {t("kickoff.back_to_idea_link")}
            </button>
          </div>
        ) : null}
        {onSelectTemplate && !markdownMode ? (
          <TemplatePicker
            onSelect={handleTemplateSelect}
            selectedSlug={selectedTemplateSlug}
            disabled={submitting || disabled}
          />
        ) : null}
        {attachments.length > 0 ? (
          <div className="kickoff__attachments">
            {attachments.map((a, i) => (
              <span
                key={`${a.display_name}-${i}`}
                className="kickoff__attachment-chip"
              >
                <span className="kickoff__attachment-glyph" aria-hidden="true">
                  ⧉
                </span>
                <span className="kickoff__attachment-name">{a.display_name}</span>
                <button
                  type="button"
                  className="kickoff__attachment-remove"
                  onClick={() => removeAttachment(i)}
                  aria-label={t("kickoff.remove_attachment", {
                    name: a.display_name,
                  })}
                >
                  ×
                </button>
              </span>
            ))}
          </div>
        ) : null}
        {!markdownMode ? (
          <div className="kickoff__attach-row">
            <button
              type="button"
              className="kickoff__attach-btn"
              onClick={() => fileInputRef.current?.click()}
              disabled={submitting || disabled}
            >
              <span aria-hidden="true">⧉</span>
              <span>{t("kickoff.attach_files")}</span>
            </button>
            <button
              type="button"
              className="kickoff__attach-btn"
              onClick={() => void handleAddLink()}
              disabled={submitting || disabled || fetchingUrl}
            >
              <span aria-hidden="true">↗</span>
              <span>
                {fetchingUrl ? t("kickoff.fetching_link") : t("kickoff.add_link")}
              </span>
            </button>
            <input
              ref={fileInputRef}
              type="file"
              multiple
              hidden
              onChange={(e) => {
                void handleFilesPicked(e.target.files);
                e.currentTarget.value = "";
              }}
            />
          </div>
        ) : null}
        <div className="kickoff__footer">
          <div className="kickoff__hint">
            {markdownMode
              ? submitting
                ? t("kickoff.importing")
                : t("kickoff.markdown_hint")
              : !canSubmit && idea.trim().length < 20 && attachments.length === 0
                ? t("kickoff.paragraph_hint")
                : submitting
                  ? t("kickoff.mapping_idea")
                  : t("kickoff.ready")}
          </div>
          <button
            type="submit"
            className="kickoff__submit"
            disabled={!canSubmit}
          >
            {submitting
              ? markdownMode
                ? t("kickoff.importing")
                : t("kickoff.mapping")
              : markdownMode
                ? t("kickoff.import_action")
                : usingTemplate
                  ? t("kickoff.start_from_template")
                  : t("kickoff.map_it")}
          </button>
        </div>
        {error ? <div className="kickoff__error">{error}</div> : null}
      </div>
      <style>{`
        .kickoff__markdown-toggle-row {
          margin-top: -4px;
          display: flex;
          flex-wrap: wrap;
          gap: 14px;
        }
        .kickoff__markdown-toggle {
          font-family: var(--ff-serif);
          font-style: italic;
          font-size: 13px;
          color: var(--ink-3);
          background: transparent;
          border: none;
          padding: 0;
          cursor: pointer;
          text-decoration: underline;
          text-decoration-color: var(--ink-5);
          text-underline-offset: 2px;
          transition: color 140ms ease;
        }
        .kickoff__markdown-toggle:hover:not(:disabled) {
          color: var(--ink);
        }
        .kickoff__markdown-toggle:disabled {
          opacity: 0.5;
          cursor: not-allowed;
        }
        .kickoff__textarea--markdown {
          font-family: var(--ff-mono);
          font-size: 12.5px;
          line-height: 1.6;
          resize: vertical;
        }
        .kickoff__attach-row {
          display: flex;
          gap: 8px;
          flex-wrap: wrap;
          align-items: center;
        }
        .kickoff__attach-btn {
          display: inline-flex;
          align-items: center;
          gap: 6px;
          font-family: var(--ff-sans);
          font-size: 12.5px;
          color: var(--ink-2);
          background: transparent;
          border: 1px solid var(--paper-edge);
          border-radius: 999px;
          padding: 7px 14px;
          cursor: pointer;
          transition: background-color 160ms ease, border-color 160ms ease,
            color 160ms ease;
        }
        .kickoff__attach-btn:hover:not(:disabled) {
          background: var(--paper-2);
          border-color: var(--ink-5);
          color: var(--ink);
        }
        .kickoff__attach-btn:disabled {
          opacity: 0.5;
          cursor: not-allowed;
        }
        .kickoff__attachments {
          display: flex;
          flex-wrap: wrap;
          gap: 8px;
          margin-top: -8px;
        }
        .kickoff__attachment-chip {
          display: inline-flex;
          align-items: center;
          gap: 8px;
          font-family: var(--ff-mono);
          font-size: 11.5px;
          color: var(--ink-2);
          background: var(--paper-lifted);
          border: 1px solid var(--paper-edge);
          border-radius: 999px;
          padding: 6px 6px 6px 12px;
        }
        .kickoff__attachment-glyph {
          font-size: 12px;
          color: var(--ink-3);
        }
        .kickoff__attachment-name {
          max-width: 260px;
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
        .kickoff__attachment-remove {
          border: none;
          background: transparent;
          cursor: pointer;
          color: var(--ink-4);
          font-size: 14px;
          line-height: 1;
          padding: 0 6px;
          border-radius: 999px;
          transition: color 120ms ease, background 120ms ease;
        }
        .kickoff__attachment-remove:hover {
          color: var(--rust);
          background: var(--paper-2);
        }
      `}</style>
      <PasteFeedbackDialog
        open={pasteFeedbackOpen}
        onClose={() => setPasteFeedbackOpen(false)}
        onComplete={() => {
          // v4 flow — extract-themes + N parallel kickoffs landed all
          // their projects. Navigate to the workspace home so the user
          // sees the auto-generated projects appear in the projects list.
          // window.location.assign re-runs the app shell's state machine
          // which routes a signed-in user with projects to the
          // projects_list phase.
          setPasteFeedbackOpen(false);
          window.location.assign("/app");
        }}
      />
    </form>
  );
}
