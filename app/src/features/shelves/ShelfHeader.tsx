// ShelfHeader — the label row that sits above each ShelfRow.
//
// Rendering shape: a small chevron + serif display shelf name + monospace
// "N projects" count chip + trailing kebab menu. The kebab exposes Rename
// and Delete; collapse toggles via the chevron itself.
//
// Rename uses window.prompt for v1 — warm editorial replacement landed on
// projects via a Dialogs effort; we reuse DeleteConfirmDialog for delete
// so the copy stays consistent with the rest of the app. A future pass can
// fold in a dedicated RenameShelfDialog if the prompt becomes a wart.
//
// No emojis. Warm editorial only.

import { useCallback, useEffect, useRef, useState } from "react";

import { RenameProjectDialog, DeleteConfirmDialog } from "../../components/dialogs";
import { t } from "../../i18n";

export type ShelfHeaderProps = {
  name: string;
  projectCount: number;
  collapsed: boolean;
  onToggleCollapsed: () => void;
  onRename?: (nextName: string) => Promise<void> | void;
  onDelete?: () => Promise<void> | void;
  // Controls the type-label next to the count. The implicit "Unfiled"
  // shelf is not user-deletable / renamable, so we omit the kebab when
  // this flag is true.
  isUnfiled?: boolean;
};

export function ShelfHeader({
  name,
  projectCount,
  collapsed,
  onToggleCollapsed,
  onRename,
  onDelete,
  isUnfiled = false,
}: ShelfHeaderProps) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [renameOpen, setRenameOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);

  // Close on outside click + Escape. Mirrors the ProjectCard kebab pattern
  // so the two menus feel identical.
  useEffect(() => {
    if (!menuOpen) return;
    const onDocClick = (e: MouseEvent) => {
      const target = e.target as Node | null;
      if (
        menuRef.current &&
        target &&
        !menuRef.current.contains(target) &&
        !triggerRef.current?.contains(target)
      ) {
        setMenuOpen(false);
      }
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setMenuOpen(false);
        triggerRef.current?.focus();
      }
    };
    document.addEventListener("mousedown", onDocClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDocClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [menuOpen]);

  const handleRenameClick = useCallback(() => {
    setMenuOpen(false);
    if (!onRename) return;
    setRenameOpen(true);
  }, [onRename]);

  const handleRenameSubmit = useCallback(
    async (nextName: string) => {
      if (!onRename) return;
      try {
        await onRename(nextName);
        setRenameOpen(false);
      } catch (err) {
        console.error("[ShelfHeader] rename failed", err);
        throw err; // let RenameProjectDialog paint its inline error state
      }
    },
    [onRename],
  );

  const handleDeleteClick = useCallback(() => {
    setMenuOpen(false);
    if (!onDelete) return;
    setDeleteOpen(true);
  }, [onDelete]);

  const handleDeleteConfirm = useCallback(async () => {
    if (!onDelete) return;
    try {
      await onDelete();
      setDeleteOpen(false);
    } catch (err) {
      console.error("[ShelfHeader] delete failed", err);
      throw err; // let DeleteConfirmDialog paint its inline error
    }
  }, [onDelete]);

  const countLabel = projectCount === 1
    ? t("shelves.header.count_one")
    : t("shelves.header.count_many", { count: String(projectCount) });

  return (
    <div className="shelf-row__header">
      <button
        type="button"
        className={
          "shelf-row__chevron" +
          (collapsed ? " shelf-row__chevron--collapsed" : "")
        }
        onClick={onToggleCollapsed}
        aria-label={collapsed ? t("shelves.header.expand_aria", { name }) : t("shelves.header.collapse_aria", { name })}
        aria-expanded={!collapsed}
      >
        <span aria-hidden="true">{"\u25BE"}</span>
      </button>
      <h2 className="shelf-row__name" title={name}>
        {name}
      </h2>
      <span className="shelf-row__count" aria-label={countLabel}>
        {countLabel}
      </span>
      {!isUnfiled && (onRename || onDelete) ? (
        <div
          className="shelf-row__menu-wrap"
          onClick={(e) => e.stopPropagation()}
          onKeyDown={(e) => e.stopPropagation()}
        >
          <button
            ref={triggerRef}
            type="button"
            className="shelf-row__menu-trigger"
            aria-haspopup="menu"
            aria-expanded={menuOpen}
            aria-label={t("shelves.header.options_aria", { name })}
            onClick={(e) => {
              e.stopPropagation();
              setMenuOpen((v) => !v);
            }}
          >
            <span aria-hidden="true">{"\u22EF"}</span>
          </button>
          {menuOpen ? (
            <div ref={menuRef} className="shelf-row__menu" role="menu">
              {onRename ? (
                <button
                  type="button"
                  role="menuitem"
                  className="shelf-row__menu-item"
                  onClick={handleRenameClick}
                >
                  {t("shelves.header.rename")}
                </button>
              ) : null}
              {onDelete ? (
                <button
                  type="button"
                  role="menuitem"
                  className="shelf-row__menu-item shelf-row__menu-item--danger"
                  onClick={handleDeleteClick}
                >
                  {t("shelves.header.delete")}
                </button>
              ) : null}
            </div>
          ) : null}
        </div>
      ) : null}
      {onRename ? (
        <RenameProjectDialog
          open={renameOpen}
          currentTitle={name}
          onSubmit={handleRenameSubmit}
          onClose={() => setRenameOpen(false)}
          titleOverride={t("shelves.header.rename_prompt_title")}
          labelOverride={t("shelves.header.rename_label")}
          placeholderOverride={t("shelves.header.rename_placeholder")}
          hintOverride={t("shelves.header.rename_hint")}
        />
      ) : null}
      {onDelete ? (
        <DeleteConfirmDialog
          open={deleteOpen}
          itemType={t("shelves.header.item_type")}
          itemName={name}
          consequences={t("shelves.header.delete_consequences")}
          onConfirm={handleDeleteConfirm}
          onClose={() => setDeleteOpen(false)}
        />
      ) : null}
    </div>
  );
}
