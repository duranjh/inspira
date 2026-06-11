// Modal shown to a user immediately after they save a decision that
// an LLM judged to contradict another user's earlier decision on the
// same project. Two resolutions:
//
//   - "Overwrite theirs"  → retracts the conflicting earlier decision.
//   - "Cancel mine"       → retracts the decision the user just saved.
//
// Shipped via the realtime WebSocket push. If the WS is down, the
// decision-create HTTP response also carries a `contradiction_hint`
// payload so the modal can still fire.

import { useCallback, useState } from "react";

import { Dialog } from "../../components/dialogs/Dialog";
import { toast } from "../../components/ToastProvider";
import { api } from "./api";
import type { ContradictionPayload } from "./realtime";

export type ContradictionDialogProps = {
  event: ContradictionPayload;
  onResolved: () => void;
};

function relativeFrom(iso: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const secs = Math.max(1, Math.round((Date.now() - d.getTime()) / 1000));
  if (secs < 60) return `${secs}s ago`;
  const mins = Math.round(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.round(hrs / 24);
  return `${days}d ago`;
}

export function ContradictionDialog({
  event,
  onResolved,
}: ContradictionDialogProps) {
  const [busy, setBusy] = useState<"overwrite" | "cancel" | null>(null);

  const handleOverwrite = useCallback(async () => {
    setBusy("overwrite");
    try {
      await api.deleteDecision(event.conflictingDecisionId);
      toast.success(
        `Replaced ${event.conflictingAuthorDisplayName}'s earlier decision.`,
      );
      onResolved();
    } catch (err) {
      console.error("[contradiction] overwrite failed", err);
      toast.error("Couldn't overwrite — please try again.");
      setBusy(null);
    }
  }, [event, onResolved]);

  const handleCancel = useCallback(async () => {
    setBusy("cancel");
    try {
      await api.deleteDecision(event.decisionId);
      toast.success("Reverted — your decision was not kept.");
      onResolved();
    } catch (err) {
      console.error("[contradiction] cancel failed", err);
      toast.error("Couldn't revert — please try again.");
      setBusy(null);
    }
  }, [event, onResolved]);

  return (
    <Dialog
      open
      onClose={onResolved}
      title="This contradicts an earlier decision"
      primaryAction={{
        label: "Overwrite theirs",
        onClick: handleOverwrite,
        disabled: busy !== null,
        busy: busy === "overwrite",
      }}
      secondaryAction={{
        label: "Cancel mine",
        onClick: handleCancel,
      }}
    >
      <p
        style={{
          fontFamily: "var(--ff-serif)",
          fontSize: 14,
          lineHeight: 1.55,
          color: "var(--ink-2)",
          margin: "0 0 12px",
        }}
      >
        <strong
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 6,
          }}
        >
          <span
            aria-hidden="true"
            style={{
              display: "inline-block",
              width: 10,
              height: 10,
              borderRadius: "50%",
              background: event.conflictingAuthorColor || "var(--ink-3)",
              verticalAlign: "middle",
            }}
          />
          {event.conflictingAuthorDisplayName}
        </strong>{" "}
        made a decision{" "}
        <em style={{ fontStyle: "italic" }}>
          {relativeFrom(event.conflictingCreatedAt)}
        </em>
        {" "}that your new decision directly contradicts:
      </p>
      <blockquote
        style={{
          margin: "0 0 12px",
          padding: "10px 14px",
          borderLeft: `3px solid ${
            event.conflictingAuthorColor || "var(--ink-5)"
          }`,
          background: "var(--paper-2, #efe8d8)",
          color: "var(--ink-1)",
          fontFamily: "var(--ff-serif)",
          fontSize: 13.5,
          lineHeight: 1.5,
          fontStyle: "italic",
        }}
      >
        {event.conflictingStatement}
      </blockquote>
      {event.reason ? (
        <p
          style={{
            fontFamily: "var(--ff-sans)",
            fontSize: 12,
            color: "var(--ink-3)",
            margin: "0 0 4px",
          }}
        >
          <strong>Why this clashes:</strong> {event.reason}
        </p>
      ) : null}
      <p
        style={{
          fontFamily: "var(--ff-sans)",
          fontSize: 12,
          color: "var(--ink-3)",
          margin: 0,
        }}
      >
        <strong>Overwrite theirs</strong> retracts {event.conflictingAuthorDisplayName}'s
        decision and keeps yours. <strong>Cancel mine</strong> keeps theirs
        and drops the one you just saved.
      </p>
    </Dialog>
  );
}
