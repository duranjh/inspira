// Approval chip for the Artifact Viewer top-bar.
//
// Per founder direction (2026-05-04), approval moved off the canvas
// onto the code surface — the canvas is now an editing surface only.
// Three states the partner sees:
//
//   Draft       → "Request review"   (start_review)
//   In Review   → "Cancel review"    (manualStateOverride to pending_review)
//                  + (reviewer)      "Approve"  (approve)
//   Approved    → "Edit again"       (manualStateOverride to pending_review)
//
// Backend state-name mapping (kept stable for backward-compat):
//   pending_review / rejected / summary_ready  →  "Draft" UX
//   in_review                                   →  "In Review" UX
//   approved                                    →  "Approved" UX
//
// Member assignment on Request review is a v2 follow-up — for now
// the action just flips the state.

import { ReactElement, useCallback, useState } from "react";

import { api, type ProjectState } from "../api";

export interface ApprovalChipProps {
  projectId: string;
  /** Current backend state. Updates flow through onStateChange so the
   *  parent (which fetches the artifact) can refetch / re-render. */
  state: ProjectState;
  onStateChange: (next: ProjectState) => void;
}

export type ApprovalUxState = "draft" | "in_review" | "approved";

export function projectStateToUx(s: ProjectState): ApprovalUxState {
  if (s === "in_review") return "in_review";
  if (s === "approved") return "approved";
  return "draft";
}

export function ApprovalChip({
  projectId,
  state,
  onStateChange,
}: ApprovalChipProps): ReactElement {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const ux = projectStateToUx(state);

  const requestReview = useCallback(async () => {
    setBusy("request_review");
    setError(null);
    try {
      const { project } = await api.transitionProjectState(
        projectId,
        "start_review",
      );
      onStateChange(
        (project.project_state ?? "in_review") as ProjectState,
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't request review.");
    } finally {
      setBusy(null);
    }
  }, [projectId, onStateChange]);

  const cancelReview = useCallback(async () => {
    setBusy("cancel_review");
    setError(null);
    try {
      const { project } = await api.manualStateOverrideProject(
        projectId,
        "pending_review",
        "Reviewer cancelled — back to draft for edits.",
      );
      onStateChange(
        (project.project_state ?? "pending_review") as ProjectState,
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't cancel review.");
    } finally {
      setBusy(null);
    }
  }, [projectId, onStateChange]);

  const approve = useCallback(async () => {
    setBusy("approve");
    setError(null);
    try {
      const { project } = await api.transitionProjectState(
        projectId,
        "approve",
      );
      onStateChange(
        (project.project_state ?? "approved") as ProjectState,
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't approve.");
    } finally {
      setBusy(null);
    }
  }, [projectId, onStateChange]);

  const editAgain = useCallback(async () => {
    setBusy("edit_again");
    setError(null);
    try {
      const { project } = await api.manualStateOverrideProject(
        projectId,
        "pending_review",
        "Re-opened for edits.",
      );
      onStateChange(
        (project.project_state ?? "pending_review") as ProjectState,
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't re-open for edits.");
    } finally {
      setBusy(null);
    }
  }, [projectId, onStateChange]);

  return (
    <div className="approval-chip">
      <span
        className={
          "approval-chip__badge approval-chip__badge--" + ux
        }
      >
        <span className="approval-chip__dot" aria-hidden="true" />
        {ux === "draft"
          ? "Draft"
          : ux === "in_review"
            ? "In Review"
            : "Approved"}
      </span>
      {ux === "draft" ? (
        <button
          type="button"
          className="approval-chip__btn approval-chip__btn--primary"
          onClick={requestReview}
          disabled={!!busy}
        >
          {busy === "request_review" ? "Requesting…" : "Request review"}
        </button>
      ) : null}
      {ux === "in_review" ? (
        <>
          <button
            type="button"
            className="approval-chip__btn approval-chip__btn--ghost"
            onClick={cancelReview}
            disabled={!!busy}
          >
            {busy === "cancel_review" ? "Cancelling…" : "Cancel review"}
          </button>
          <button
            type="button"
            className="approval-chip__btn approval-chip__btn--primary"
            onClick={approve}
            disabled={!!busy}
          >
            {busy === "approve" ? "Approving…" : "Approve"}
          </button>
        </>
      ) : null}
      {ux === "approved" ? (
        <button
          type="button"
          className="approval-chip__btn approval-chip__btn--ghost"
          onClick={editAgain}
          disabled={!!busy}
        >
          {busy === "edit_again" ? "Re-opening…" : "Edit again"}
        </button>
      ) : null}
      {error ? (
        <span className="approval-chip__error" role="alert">
          {error}
        </span>
      ) : null}
    </div>
  );
}
