// Inspira — Paste customer feedback dialog (v4 — Option A).
//
// Full v4 connector flow: PM pastes raw customer feedback (any size),
// Inspira extracts 3-5 recurring themes via a cheap LLM call, then
// auto-generates one project per theme by firing kickoffs in parallel.
// User ends up on the workspace home with N new projects ready to
// review.
//
// Pipeline:
//   1. Parse the pasted blob into individual items (line-based / CSV /
//      JSON; no item-count cap — B2B SaaS pastes can be thousands).
//   2. POST /api/v2/feedback/extract-themes → returns 3-5 themes,
//      each with title + summary + source_indices.
//   3. For each theme: create project + fire kickoff in parallel
//      (Promise.all). The kickoff prompt bundles the theme summary +
//      the cited feedback items so the planner has rich context.
//   4. Toast progress → close dialog → onComplete fires a re-fetch of
//      the projects list so the new projects show up immediately.
//
// Real ingestion + dedupe + persistent feedback_items table is the
// W2-W3 build per the engineering plan. This is the Sunday-ship version
// — visibly demonstrates the v4 connector promise end-to-end.

import {
  useCallback,
  useEffect,
  useState,
  type ChangeEvent,
} from "react";

import { Dialog } from "../../components/dialogs/Dialog";
import { t } from "../../i18n";
import { api } from "./api";

export type PasteFeedbackDialogProps = {
  open: boolean;
  /** Called once all per-theme kickoffs complete. Parent should refetch
   *  the projects list and surface the new auto-generated projects. */
  onComplete: (themeCount: number) => void;
  onClose: () => void;
};

// 64K char paste cap — generous for B2B SaaS triage sessions
// (~1000 short items at avg 60 chars). Real backend ingestion endpoint
// would handle 10K+ items via streaming; that's W2.
const MAX_FEEDBACK_CHARS = 64000;

type Phase =
  | { kind: "idle" }
  | { kind: "synthesizing" }
  | { kind: "planning"; total: number; done: number }
  | { kind: "done"; total: number }
  | { kind: "error"; message: string };

/** Parse the pasted blob into individual feedback items.
 *
 * Accepts: one item per line, CSV rows (we take the first column),
 * or JSON arrays of strings/objects. No artificial item cap — backend
 * accepts up to 2000 items per call.
 */
function parseItems(raw: string): string[] {
  const trimmed = raw.trim();
  if (!trimmed) return [];

  // Try JSON first — array of strings or array of {body|text|content|message}
  try {
    const parsed = JSON.parse(trimmed) as unknown;
    if (Array.isArray(parsed)) {
      const items = parsed
        .map((entry) => {
          if (typeof entry === "string") return entry.trim();
          if (entry && typeof entry === "object") {
            const obj = entry as Record<string, unknown>;
            for (const key of ["body", "text", "content", "message", "feedback"]) {
              const value = obj[key];
              if (typeof value === "string" && value.trim()) {
                return value.trim();
              }
            }
          }
          return "";
        })
        .filter((s): s is string => Boolean(s));
      if (items.length > 0) return items;
    }
  } catch {
    // Not JSON; fall through to line-based split.
  }

  // Line-based: drop CSV column commas after the first column. Cheap
  // heuristic — the user can paste cleaner input if they want all
  // columns preserved.
  return trimmed
    .split(/\r?\n/)
    .map((line) => line.replace(/^"?(.*?)"?\s*(?:,|$)/, "$1").trim())
    .filter((line) => Boolean(line) && !line.startsWith("#"));
}

/** Build the per-theme kickoff prompt that fires for each theme.
 *
 * Bundles the theme title + summary + the cited feedback items so the
 * planner has rich context. The kickoff API caps user_idea at 8000
 * chars; we sample cited items to stay bounded.
 */
function buildThemeKickoffPrompt(
  theme: { title: string; summary: string; source_indices: number[] },
  allItems: string[],
): string {
  const cited = theme.source_indices
    .map((i) => allItems[i])
    .filter((item): item is string => typeof item === "string" && item.length > 0)
    .slice(0, 15);
  const citedSection = cited.length > 0
    ? `\n\nGround in these customer-feedback items:\n${cited.map((c, i) => `${i + 1}. ${c}`).join("\n")}`
    : "";
  return (
    `Plan a feature for our B2B SaaS roadmap: ${theme.title}\n\n` +
    `Context: ${theme.summary}${citedSection}\n\n` +
    `Map this into topics, draft the key questions per topic, and surface the ` +
    `decisions an engineering team will need to ship the right thing.`
  );
}

export function PasteFeedbackDialog({
  open,
  onComplete,
  onClose,
}: PasteFeedbackDialogProps) {
  const [text, setText] = useState<string>("");
  const [phase, setPhase] = useState<Phase>({ kind: "idle" });

  // Reset every time the dialog opens. Defensive — if a previous
  // session ended in error or done state, the user shouldn't see
  // that on re-open.
  useEffect(() => {
    if (open) {
      setText("");
      setPhase({ kind: "idle" });
    }
  }, [open]);

  const handleChange = useCallback((e: ChangeEvent<HTMLTextAreaElement>) => {
    setText(e.target.value);
    if (phase.kind === "error") setPhase({ kind: "idle" });
  }, [phase]);

  const handleSubmit = useCallback(async () => {
    const items = parseItems(text);
    if (items.length === 0) {
      setPhase({ kind: "error", message: t("paste_feedback.error_empty") });
      return;
    }

    setPhase({ kind: "synthesizing" });
    let themes: { title: string; summary: string; source_indices: number[] }[];
    try {
      const result = await api.extractThemes(items);
      themes = result.themes;
    } catch (err) {
      setPhase({
        kind: "error",
        message: err instanceof Error ? err.message : t("paste_feedback.error_extract_failed"),
      });
      return;
    }
    if (themes.length === 0) {
      setPhase({
        kind: "error",
        message: t("paste_feedback.error_no_themes"),
      });
      return;
    }

    setPhase({ kind: "planning", total: themes.length, done: 0 });

    // Fire all per-theme kickoffs in parallel. Each: create project +
    // run kickoff with the theme's bundled context. Counts completions
    // for the progress UI; bubbles errors as a phase shift.
    let completed = 0;
    const results = await Promise.allSettled(
      themes.map(async (theme) => {
        const { project } = await api.createV2Project(theme.title);
        const prompt = buildThemeKickoffPrompt(theme, items);
        await api.kickoff(project.project_id, prompt);
        completed += 1;
        setPhase({ kind: "planning", total: themes.length, done: completed });
      }),
    );
    const failed = results.filter((r) => r.status === "rejected");
    if (failed.length === themes.length) {
      // All kickoffs failed — surface a real error.
      const first = failed[0] as PromiseRejectedResult;
      const reason = first.reason instanceof Error ? first.reason.message : String(first.reason);
      setPhase({ kind: "error", message: reason });
      return;
    }

    // At least some themes succeeded. Surface success even with partial
    // failures — the user will see the projects on the home and can
    // re-run for any missing ones. Real per-theme failure handling is
    // a W4 polish item.
    const successCount = themes.length - failed.length;
    setPhase({ kind: "done", total: successCount });
    onComplete(successCount);
  }, [text, onComplete]);

  // Status text per phase. Localized via i18n with {n} interpolation.
  const statusText = (() => {
    switch (phase.kind) {
      case "synthesizing":
        return t("paste_feedback.status_synthesizing");
      case "planning":
        return t("paste_feedback.status_planning", {
          done: phase.done,
          total: phase.total,
        });
      case "done":
        return t("paste_feedback.status_done", { count: phase.total });
      case "error":
        return phase.message;
      default:
        return "";
    }
  })();

  const busy = phase.kind === "synthesizing" || phase.kind === "planning";
  const canSubmit = phase.kind === "idle" || phase.kind === "error";

  return (
    <Dialog
      open={open}
      onClose={busy ? () => {} : onClose}
      title={t("paste_feedback.title")}
      width={560}
      dismissOnBackdrop={!busy}
      primaryAction={{
        label: busy
          ? t("paste_feedback.processing")
          : t("paste_feedback.submit"),
        onClick: handleSubmit,
        disabled: !text.trim() || busy || !canSubmit,
        busy,
      }}
      secondaryAction={
        busy
          ? undefined
          : { label: t("paste_feedback.cancel"), onClick: onClose }
      }
    >
      <p
        style={{
          fontFamily: "var(--ff-serif)",
          fontSize: 14,
          lineHeight: 1.6,
          color: "var(--ink-2, #423a2d)",
          margin: "0 0 12px",
        }}
      >
        {t("paste_feedback.body")}
      </p>
      <textarea
        value={text}
        onChange={handleChange}
        placeholder={t("paste_feedback.placeholder")}
        rows={10}
        maxLength={MAX_FEEDBACK_CHARS}
        disabled={busy}
        style={{
          width: "100%",
          padding: "12px",
          fontFamily: "var(--ff-mono, monospace)",
          fontSize: 13,
          lineHeight: 1.5,
          color: "var(--ink-1)",
          backgroundColor: "var(--paper)",
          border: "1px solid var(--paper-edge, #d3c9b6)",
          borderRadius: 4,
          resize: "vertical",
          boxSizing: "border-box",
          opacity: busy ? 0.6 : 1,
        }}
        autoFocus
      />
      <p
        style={{
          fontFamily: "var(--ff-sans)",
          fontSize: 11,
          color: "var(--ink-3)",
          margin: "8px 0 0",
          letterSpacing: "0.02em",
        }}
      >
        {t("paste_feedback.hint")}
      </p>
      {statusText ? (
        <p
          style={{
            fontFamily: "var(--ff-sans)",
            fontSize: 13,
            color:
              phase.kind === "error"
                ? "var(--rust, #a95a2f)"
                : phase.kind === "done"
                  ? "var(--sage, #5e7a5a)"
                  : "var(--ink-2)",
            margin: "12px 0 0",
            fontWeight: 500,
          }}
          aria-live="polite"
        >
          {statusText}
        </p>
      ) : null}
    </Dialog>
  );
}
