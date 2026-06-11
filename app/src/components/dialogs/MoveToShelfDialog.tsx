// Inspira — move-to-shelf picker dialog (L6, #038).
//
// Opened from the ⋯ kebab menu on a ProjectCard's "Move to…" item.
// Renders a vertical list of the user's shelves plus an "Unfiled"
// option that un-shelves the project. The currently-selected shelf is
// highlighted but not disabled — the user can still pick it (no-op,
// the dialog closes without firing the API call to avoid a wasted
// round-trip).
//
// Why a dialog and not a submenu? Two reasons:
//   1. Submenus inside a kebab popover are awkward on touch — the
//      first-level menu has tap targets at the top-right edge of the
//      card, and a horizontal submenu would overflow the viewport on
//      many phone widths.
//   2. The shelf list can grow longer than the kebab popover height.
//      A scrollable dialog body handles 1+ shelves uniformly.
//
// The drag-to-shelf path (ShelfRow's drop handler) covers the
// keyboard-averse desktop user; this dialog covers the touch user
// AND any user whose drop zone discoverability is poor.

import { useCallback, useState } from "react";

import { Dialog } from "./Dialog";

import type { Shelf } from "../../features/inspira/api";
import { t } from "../../i18n";

export type MoveToShelfDialogProps = {
  open: boolean;
  /** All shelves owned by the current user (typically from `useShelves()` /
   * the parent's shelves state). May be empty — the dialog still renders
   * the Unfiled option in that case (lets the user un-shelf an
   * already-shelved project even if all shelves were just deleted). */
  shelves: Shelf[];
  /** The project's current shelf, or null if it's currently Unfiled.
   * Used to highlight the active option in the list. */
  currentShelfId: string | null;
  /** Title of the project being moved — for the dialog's contextual
   * subline so the user knows which project they're moving. */
  projectTitle: string;
  /** Called with the chosen shelfId (or null for Unfiled) AFTER the
   * user picks. The parent's promise drives the dialog's busy state.
   * Throws → dialog stays open with inline error. */
  onMove: (shelfIdOrNull: string | null) => Promise<void>;
  onClose: () => void;
};

export function MoveToShelfDialog({
  open,
  shelves,
  currentShelfId,
  projectTitle,
  onMove,
  onClose,
}: MoveToShelfDialogProps) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handlePick = useCallback(
    async (shelfId: string | null) => {
      if (busy) return;
      // Picking the currently-active shelf is a no-op — close without
      // firing the API call.
      if (shelfId === currentShelfId) {
        onClose();
        return;
      }
      setBusy(true);
      setError(null);
      try {
        await onMove(shelfId);
        onClose();
      } catch (err) {
        console.error("[Inspira] move-to-shelf failed", err);
        setError(t("move_to_shelf_dialog.error"));
        setBusy(false);
      }
    },
    [busy, currentShelfId, onMove, onClose],
  );

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={t("move_to_shelf_dialog.title")}
      width={400}
      // No primaryAction / secondaryAction — picking a shelf IS the
      // primary action, and there's nothing to "save" beyond a click.
    >
      <p className="dlg__lede" style={ledeStyle}>
        {t("move_to_shelf_dialog.subtitle", { project_title: projectTitle })}
      </p>
      <div role="listbox" style={listStyle}>
        {/* Unfiled — first option. Always available; clicking moves
            the project to the implicit "no shelf" bucket. */}
        <button
          type="button"
          role="option"
          aria-selected={currentShelfId === null}
          className={
            "dlg__btn dlg__btn--secondary" +
            (currentShelfId === null ? " dlg__btn--active" : "")
          }
          style={
            currentShelfId === null
              ? { ...optionStyle, ...optionActiveStyle }
              : optionStyle
          }
          onClick={() => handlePick(null)}
          disabled={busy}
        >
          {t("move_to_shelf_dialog.unfiled")}
          {currentShelfId === null ? (
            <span style={badgeStyle}>{t("move_to_shelf_dialog.current")}</span>
          ) : null}
        </button>
        {shelves.map((shelf) => (
          <button
            key={shelf.shelf_id}
            type="button"
            role="option"
            aria-selected={currentShelfId === shelf.shelf_id}
            className={
              "dlg__btn dlg__btn--secondary" +
              (currentShelfId === shelf.shelf_id ? " dlg__btn--active" : "")
            }
            style={
              currentShelfId === shelf.shelf_id
                ? { ...optionStyle, ...optionActiveStyle }
                : optionStyle
            }
            onClick={() => handlePick(shelf.shelf_id)}
            disabled={busy}
          >
            <span style={shelfNameStyle}>{shelf.name}</span>
            {currentShelfId === shelf.shelf_id ? (
              <span style={badgeStyle}>
                {t("move_to_shelf_dialog.current")}
              </span>
            ) : null}
          </button>
        ))}
      </div>
      {error ? <div className="dlg__share-error">{error}</div> : null}
    </Dialog>
  );
}

const ledeStyle: React.CSSProperties = {
  fontFamily: "var(--ff-serif)",
  fontStyle: "italic",
  fontSize: 13,
  color: "var(--ink-3)",
  margin: "0 0 14px 0",
};

const listStyle: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 6,
  maxHeight: 320,
  overflowY: "auto",
  marginTop: 4,
};

const optionStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  width: "100%",
  textAlign: "left" as const,
  fontWeight: 500,
};

const optionActiveStyle: React.CSSProperties = {
  // Subtle highlight for the currently-active shelf.
  background: "var(--paper-2)",
  borderColor: "var(--ink-3)",
};

const shelfNameStyle: React.CSSProperties = {
  flex: 1,
  textAlign: "left" as const,
  whiteSpace: "nowrap",
  overflow: "hidden",
  textOverflow: "ellipsis",
};

const badgeStyle: React.CSSProperties = {
  fontFamily: "var(--ff-mono)",
  fontSize: 10,
  color: "var(--ink-3)",
  textTransform: "uppercase" as const,
  letterSpacing: "0.05em",
  marginLeft: 8,
};
