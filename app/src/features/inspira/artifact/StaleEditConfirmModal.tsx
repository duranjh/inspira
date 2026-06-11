import { type ReactElement } from "react";

import type { PrOverlayStalenessResponse } from "../api";
import { Dialog } from "../../../components/dialogs/Dialog";

type StaleEditConfirmModalProps = {
  open: boolean;
  staleness: PrOverlayStalenessResponse | null;
  filePath: string | null;
  onConfirm: () => void;
  onCancel: () => void;
  /** Wave F.6 — fires when the partner clicks "Refresh PR with
   *  Inspira" from inside the modal. The parent dismisses this modal
   *  and routes to ``useRefreshPr.startRefresh``. Optional so legacy
   *  callers degrade to the F.5 disabled state. */
  onRefreshClick?: () => void;
  refreshing?: boolean;
};

/**
 * Wave F.5 — soft edit-block modal that fires when the partner tries
 * to enter edit mode on a file in a stale PR overlay.
 *
 * Composes the shared ``Dialog`` shell — focus trap, dismiss-on-Esc,
 * dismiss-on-backdrop, body scroll lock, and primary/secondary button
 * styling for free.
 *
 * Wave F.6 — the inline "Refresh PR with Inspira" CTA is now live
 * (when ``onRefreshClick`` is supplied). The primary still encodes
 * "Edit anyway"; refresh is offered as a second path so the partner
 * can either accept the friction or let Inspira redraft on top of
 * the new main.
 */
export function StaleEditConfirmModal({
  open,
  staleness,
  filePath,
  onConfirm,
  onCancel,
  onRefreshClick,
  refreshing = false,
}: StaleEditConfirmModalProps): ReactElement {
  const movedAt = staleness?.main_moved_at;
  const relativeMoved = movedAt ? formatRelative(movedAt) : null;
  const filename = filePath ? filePath.split("/").pop() ?? filePath : null;
  const ctaEnabled = Boolean(onRefreshClick) && !refreshing;

  return (
    <Dialog
      open={open}
      onClose={onCancel}
      title="Main has moved since this was drafted"
      primaryAction={{
        label: "Edit anyway, I'll deal with it",
        onClick: onConfirm,
      }}
      secondaryAction={{
        label: "Cancel",
        onClick: onCancel,
      }}
    >
      <p className="dlg__stale-edit-body">
        {relativeMoved
          ? `Main moved ${relativeMoved}. `
          : "Main has advanced since Inspira drafted this. "}
        Inspira drafted{" "}
        {filename ? <strong>{filename}</strong> : "this file"} against an
        older main commit. Editing now means you'll need to rebase later
        to land cleanly.
      </p>
      <div className="dlg__stale-edit-refresh">
        <button
          type="button"
          className="dlg__stale-edit-refresh-cta"
          disabled={!ctaEnabled}
          aria-disabled={!ctaEnabled}
          onClick={onRefreshClick}
          title={
            onRefreshClick
              ? undefined
              : "Refresh is unavailable in this build."
          }
        >
          {refreshing ? "Refreshing…" : "Refresh PR with Inspira"}
        </button>
        <span className="dlg__stale-edit-refresh-hint">
          {refreshing
            ? "Inspira is redrafting against the latest main…"
            : "Let Inspira redraft this on top of the latest main + your edits."}
        </span>
      </div>
    </Dialog>
  );
}

/**
 * Tiny relative-time formatter — keeps the modal self-contained instead
 * of pulling in a dayjs/luxon dep just for one string. Falls back to
 * the ISO timestamp if parsing fails.
 */
function formatRelative(iso: string): string {
  const parsed = Date.parse(iso);
  if (Number.isNaN(parsed)) return iso;
  const delta = Math.max(0, Date.now() - parsed);
  const minutes = Math.floor(delta / 60_000);
  if (minutes < 1) return "moments ago";
  if (minutes < 60) return `${minutes} minute${minutes === 1 ? "" : "s"} ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} hour${hours === 1 ? "" : "s"} ago`;
  const days = Math.floor(hours / 24);
  return `${days} day${days === 1 ? "" : "s"} ago`;
}
