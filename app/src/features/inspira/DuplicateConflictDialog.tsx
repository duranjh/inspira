// Inspira — proactive duplicate-detection dialog (TΛ.3).
//
// Replaces the old Planner Views > Duplicates tab. Whenever the dedupe
// pass surfaces one or more candidate merges, this dialog pops up at
// the canvas level showing one proposal at a time. The user picks
// "Merge them" or "Keep both", and we either fire the merge endpoint
// or just dismiss the proposal locally.
//
// Trigger sources today:
//   1. Canvas mount: InspiraApp pre-fetches dedupe alongside summary;
//      when proposals arrive, the dialog opens.
//   2. (Future) `inspira:duplicate-detected` window event — fired after
//      a Q&A turn that produces a new decision overlapping an existing
//      one. The listener stays here so any caller can drop a proposal
//      into the queue without InspiraApp wiring.
//
// Queue semantics: proposals are walked one-by-one. After the user
// resolves the current one (accept or reject), we advance to the next.
// When the queue empties, the dialog closes itself.
//
// The "Keep this/that" three-way choice in the original plan was
// dropped for the MVP because the merge endpoint doesn't currently
// take a winning-side parameter — it merges to a suggested combined
// title. If/when the backend supports per-side merging, this dialog
// is the place to add the third button.

import { useCallback, type ReactElement } from "react";

import { Dialog } from "../../components/dialogs/Dialog";
import { t } from "../../i18n";
import type { MergeProposal, TopicStub } from "../llm-modes";

export type DuplicateConflictDialogProps = {
  /** Current proposal to render. When null the dialog is closed. */
  proposal: MergeProposal | null;
  /** 1-based current index into the queue (for the "1 of 3" indicator). */
  currentIndex?: number;
  /** Total queue size. When > 1 the indicator paints. */
  totalCount?: number;
  /** Lookup map for topic title + icon. */
  topicsById: Map<string, TopicStub>;
  /** User accepted the suggested merge — parent fires the merge call
   *  and advances the queue. */
  onMerge: (p: MergeProposal) => Promise<void> | void;
  /** User picked Keep both / Got it — parent dismisses the proposal
   *  and advances the queue. */
  onKeepBoth: (p: MergeProposal) => Promise<void> | void;
  /** Hard close — used by Esc / backdrop / explicit X. Treats the
   *  current proposal as "keep both" and stops the queue. */
  onClose: () => void;
};

export function DuplicateConflictDialog(
  props: DuplicateConflictDialogProps,
): ReactElement | null {
  const {
    proposal,
    currentIndex,
    totalCount,
    topicsById,
    onMerge,
    onKeepBoth,
    onClose,
  } = props;

  const open = proposal !== null;

  const handleMerge = useCallback(() => {
    if (!proposal) return;
    void onMerge(proposal);
  }, [proposal, onMerge]);

  const handleKeepBoth = useCallback(() => {
    if (!proposal) return;
    void onKeepBoth(proposal);
  }, [proposal, onKeepBoth]);

  if (!open || !proposal) {
    return null;
  }

  // The InspiraApp queue filters out `keep_both_but_note` proposals
  // before they reach this dialog (see InspiraApp.tsx — those are
  // informational FYI flags from the planner that don't need user
  // input). So every proposal that gets here is a real `merge`
  // candidate with both Merge them / Keep both options.
  const a = topicsById.get(proposal.topic_a_id);
  const b = topicsById.get(proposal.topic_b_id);
  const showQueue =
    typeof currentIndex === "number" &&
    typeof totalCount === "number" &&
    totalCount > 1;

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={t("duplicate_dialog.title")}
      width={520}
      primaryAction={{
        label: t("duplicate_dialog.action_merge"),
        onClick: handleMerge,
      }}
      secondaryAction={{
        label: t("duplicate_dialog.action_keep_both"),
        onClick: handleKeepBoth,
      }}
    >
      <p
        className="dlg__lede"
        style={{
          fontFamily: "var(--ff-serif)",
          fontSize: 14,
          lineHeight: 1.5,
          color: "var(--ink-2)",
          margin: "0 0 14px 0",
        }}
      >
        {t("duplicate_dialog.body_hint")}
      </p>

      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 12,
          flexWrap: "wrap",
          marginBottom: 14,
        }}
      >
        <span
          className="dedupe-chip"
          style={{ display: "inline-flex", gap: 8, alignItems: "center" }}
        >
          <span className="dedupe-chip__icon">{a?.icon ?? "?"}</span>
          <span className="dedupe-chip__title">
            {a?.title ?? t("dedupe_view.unknown_topic")}
          </span>
        </span>
        <span aria-hidden="true" style={{ color: "var(--ink-3)" }}>
          {"∙∙∙→"}
        </span>
        <span
          className="dedupe-chip"
          style={{ display: "inline-flex", gap: 8, alignItems: "center" }}
        >
          <span className="dedupe-chip__icon">{b?.icon ?? "?"}</span>
          <span className="dedupe-chip__title">
            {b?.title ?? t("dedupe_view.unknown_topic")}
          </span>
        </span>
      </div>

      <p
        style={{
          fontFamily: "var(--ff-serif)",
          fontSize: 13,
          lineHeight: 1.5,
          color: "var(--ink-2)",
          margin: "0 0 14px 0",
          fontStyle: "italic",
        }}
      >
        {proposal.overlap_reason}
      </p>

      {proposal.suggested_merged_title ? (
        <div
          style={{
            padding: "10px 12px",
            background: "var(--paper-2)",
            border: "1px solid var(--paper-edge)",
            borderRadius: 4,
            marginBottom: showQueue ? 14 : 0,
          }}
        >
          <p
            style={{
              fontFamily: "var(--ff-mono)",
              fontSize: 10,
              letterSpacing: "0.18em",
              textTransform: "uppercase",
              color: "var(--ink-3)",
              margin: "0 0 4px 0",
            }}
          >
            {t("dedupe_view.merged_label")}
          </p>
          <p
            style={{
              fontFamily: "var(--ff-serif)",
              fontSize: 14,
              color: "var(--ink-1)",
              margin: 0,
            }}
          >
            {proposal.suggested_merged_title}
          </p>
        </div>
      ) : null}

      {showQueue ? (
        <p
          style={{
            fontFamily: "var(--ff-mono)",
            fontSize: 10,
            letterSpacing: "0.18em",
            textTransform: "uppercase",
            color: "var(--ink-3)",
            margin: "12px 0 0 0",
          }}
        >
          {t("duplicate_dialog.queue_indicator", {
            current: String(currentIndex),
            total: String(totalCount),
          })}
        </p>
      ) : null}
    </Dialog>
  );
}
