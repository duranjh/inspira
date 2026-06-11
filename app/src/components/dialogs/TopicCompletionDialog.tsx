// Inspira — topic-completion dialog (P1.9, #069/#070-adjacent).
//
// Replaces the inline `CompletionBanner` that used to render at the top
// of the topic-detail thread when the planner saturated the topic
// (all checkpoints answered + last planner turn was `suggest_close`).
// The inline banner had two problems:
//
//   1. It scrolled out of view as the user read the long thread, so
//      the most important moment of the flow ("you're done — pick
//      what's next") was easy to miss.
//   2. It rendered as a plain inline div with bespoke pill styles,
//      not the warm Dialog the rest of the app uses for confirmations.
//      Visually inconsistent with the design language.
//
// The replacement:
//   - Centered modal Dialog (warm shell, backdrop, focus trap).
//   - Title: "The planner already has what it needs." (localized).
//   - Body: "{topicTitle} is well defined. What's next?" — short,
//     friendly, names the topic so the user knows which one closed.
//   - Primary action: Next topic → (closes topic + jumps to next sibling).
//   - Secondary action: Keep asking (dismisses for the session; if the
//     LLM later opens new checkpoints and re-saturates, the dialog
//     fires again).
//   - Tertiary text-link: Don't show again on this topic — persists
//     a localStorage flag keyed by topicId, so subsequent saturations
//     of the SAME topic don't re-pop the dialog. Other topics still
//     trigger normally.
//
// Three-action layout: like RelationshipLabelDialog, this dialog
// leaves the base Dialog's primaryAction/secondaryAction slots
// undefined and renders all three controls inline in the body. That
// keeps the base shell simple (it doesn't need a tertiary slot just
// for one consumer) and lets us put the "Don't show again" text-link
// in a visually-distinct row below the two main pills.

import { useCallback } from "react";

import { Dialog } from "./Dialog";

import { t } from "../../i18n";

const SUPPRESS_PREFIX = "inspira_topic_completion_suppressed:";

/** Read whether the user previously chose "Don't show again" for `topicId`. */
export function isCompletionSuppressed(topicId: string): boolean {
  if (typeof window === "undefined") return false;
  try {
    return window.localStorage.getItem(SUPPRESS_PREFIX + topicId) === "true";
  } catch {
    return false;
  }
}

/** Persist the "Don't show again" choice for a single topic. */
export function suppressCompletionDialog(topicId: string): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(SUPPRESS_PREFIX + topicId, "true");
  } catch {
    /* storage disabled — best effort */
  }
}

export type TopicCompletionDialogProps = {
  open: boolean;
  /** Topic title for the body line ("{title} is well defined. What's next?"). */
  topicTitle: string;
  /** Topic id — used to scope the "Don't show again" suppress flag. */
  topicId: string;
  /** Primary action — close the topic + move to the next sibling. */
  onNextTopic: () => void;
  /** Secondary action — dismiss for this session, keep the thread open. */
  onKeepAsking: () => void;
  /** Backdrop / Esc / X — same as Keep asking (session-only dismiss). */
  onClose: () => void;
};

export function TopicCompletionDialog({
  open,
  topicTitle,
  topicId,
  onNextTopic,
  onKeepAsking,
  onClose,
}: TopicCompletionDialogProps) {
  const handleSuppress = useCallback(() => {
    // Persist + dismiss for the session in one motion. The next time
    // this topic saturates, the flag short-circuits the parent's
    // "should I open the dialog?" check.
    suppressCompletionDialog(topicId);
    onClose();
  }, [topicId, onClose]);

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={t("topic_completion_dialog.title")}
      width={480}
      // No primaryAction / secondaryAction — we render our own action
      // row in the body so the Don't-show-again text-link can sit
      // visually distinct from Next/Keep.
    >
      <p className="dlg__lede" style={ledeStyle}>
        {t("topic_completion_dialog.body", { topic_title: topicTitle })}
      </p>
      <div className="dlg__actions" style={actionsRowStyle}>
        <button
          type="button"
          className="dlg__btn dlg__btn--secondary"
          onClick={onKeepAsking}
        >
          {t("topic_completion_dialog.keep_asking_cta")}
        </button>
        <button
          type="button"
          className="dlg__btn dlg__btn--primary"
          onClick={onNextTopic}
        >
          {t("topic_completion_dialog.next_topic_cta")}
        </button>
      </div>
      <div style={tertiaryRowStyle}>
        <button
          type="button"
          className="dlg__textlink"
          onClick={handleSuppress}
          style={textLinkStyle}
        >
          {t("topic_completion_dialog.dont_show_again")}
        </button>
      </div>
    </Dialog>
  );
}

const ledeStyle: React.CSSProperties = {
  fontFamily: "var(--ff-serif)",
  fontStyle: "italic",
  fontSize: 14,
  color: "var(--ink-2)",
  margin: "0 0 6px 0",
};

const actionsRowStyle: React.CSSProperties = {
  display: "flex",
  justifyContent: "flex-end",
  gap: 8,
  marginTop: 16,
  paddingTop: 16,
  borderTop: "1px solid var(--paper-edge)",
};

// Tertiary row sits flush-left below the pills so it reads as
// "an option, but a smaller one" — tertiary in the visual hierarchy.
const tertiaryRowStyle: React.CSSProperties = {
  display: "flex",
  justifyContent: "flex-start",
  marginTop: 10,
};

const textLinkStyle: React.CSSProperties = {
  background: "transparent",
  border: "none",
  padding: 0,
  fontFamily: "inherit",
  fontSize: 12,
  color: "var(--ink-3)",
  textDecoration: "underline",
  cursor: "pointer",
  letterSpacing: "0.01em",
};
