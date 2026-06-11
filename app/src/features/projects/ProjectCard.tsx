// ProjectCard — a single project tile in the Projects list grid.
//
// Rendering shape: paper card (min 280x180, warm cream), two-line clamped
// serif title, italic "updated N units ago", and — when counts are supplied
// — a small row of monospace pills ("3 topics · 12 decisions"). The card
// itself is a <button> so it's keyboard-focusable and Enter-to-open.
//
// Overflow: a hover-revealed kebab in the top-right opens a tiny menu with
// Rename, Duplicate, Archive, and Delete in that order. All use native
// prompt/confirm for v1 — a dedicated Dialogs agent will replace the
// destructive ones later. Duplicate calls the backend deep-copy endpoint
// directly and refreshes the page so the new project appears without
// parent wiring. The menu closes on outside click and on Escape, and
// does not propagate click-through to the card.
//
// Archived-view variant: when ``archived`` is true the Archive action is
// replaced with Restore, and the card gets a muted presentation +
// ARCHIVED watermark. The parent (ProjectsListPage) toggles this based
// on which list is on screen.
//
// No emojis. Warm editorial only.

import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "../inspira/api";
import type { Shelf, V2Project } from "../inspira/api";
import { toast } from "../../components/ToastProvider";
import {
  MoveToShelfDialog,
  RenameProjectDialog,
} from "../../components/dialogs";
import { t, useLocale, formatRelativeTime } from "../../i18n";

export type ProjectCardProps = {
  project: V2Project;
  topicsCount?: number;
  decisionsCount?: number;
  onOpen: () => void;
  onRename: (newTitle: string) => Promise<void>;
  onDelete: () => Promise<void>;
  // Optional archive-lifecycle handlers. When ``archived`` is false the
  // card shows an "Archive" action (calls onArchive). When ``archived`` is
  // true the card shows "Restore" (calls onUnarchive) and applies the
  // muted/watermarked presentation. If neither handler is wired the menu
  // item is omitted entirely — callers that haven't adopted archiving yet
  // fall through the same code path unchanged.
  archived?: boolean;
  onArchive?: () => Promise<void>;
  onUnarchive?: () => Promise<void>;
  // L6 (#038) — Move-to-shelf wiring. When BOTH `shelves` and
  // `onMoveToShelf` are present, the card renders a "Move to…"
  // menu item that opens a small picker dialog. When either is
  // absent, the menu item is omitted (back-compat for legacy
  // callers + the archived-view code path that doesn't expose
  // shelf membership at all).
  shelves?: Shelf[];
  onMoveToShelf?: (shelfIdOrNull: string | null) => Promise<void>;
};

export function ProjectCard({
  project,
  topicsCount,
  decisionsCount,
  onOpen,
  onRename,
  onDelete,
  archived = false,
  onArchive,
  onUnarchive,
  shelves,
  onMoveToShelf,
}: ProjectCardProps) {
  // Subscribe to locale changes so relative timestamps re-render on swap.
  useLocale();
  const [menuOpen, setMenuOpen] = useState(false);
  const [renameOpen, setRenameOpen] = useState(false);
  // L6 — move-to-shelf picker dialog state.
  const [moveDialogOpen, setMoveDialogOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);

  // Close on outside click + Escape. Only attach the listeners when the
  // menu is actually open so we don't sprinkle no-op handlers everywhere.
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
    // Open the warm-editorial RenameProjectDialog instead of the
    // native window.prompt — the latter broke the visual language and
    // was the last native-OS dialog leaking into the product surface.
    setMenuOpen(false);
    setRenameOpen(true);
  }, []);

  const handleRenameSubmit = useCallback(
    async (nextTitle: string) => {
      try {
        await onRename(nextTitle);
        setRenameOpen(false);
      } catch (err) {
        console.error("[ProjectCard] rename failed", err);
        // RenameProjectDialog surfaces its own error state on throw, so
        // re-raise to let it paint the error message inline.
        throw err;
      }
    },
    [onRename],
  );

  const handleDelete = useCallback(async () => {
    // Just signal the intent; the parent (ProjectsListPage) opens the
    // warm-editorial DeleteConfirmDialog and runs the real delete on
    // confirm. Using window.confirm() here was leaking the browser's
    // native OS dialog into a product that otherwise has its own
    // design language.
    setMenuOpen(false);
    try {
      await onDelete();
    } catch (err) {
      console.error("[ProjectCard] delete request failed", err);
    }
  }, [onDelete]);

  // L6 — Open the move-to-shelf picker. Same as Rename: dismiss the
  // kebab menu first so the dialog's focus trap doesn't fight with
  // the still-open menu's outside-click handler.
  const handleMoveClick = useCallback(() => {
    setMenuOpen(false);
    setMoveDialogOpen(true);
  }, []);

  const handleMoveSubmit = useCallback(
    async (shelfIdOrNull: string | null) => {
      if (!onMoveToShelf) return;
      try {
        await onMoveToShelf(shelfIdOrNull);
        // Dialog auto-closes on success; the parent updates list
        // state via api.moveProjectToShelf and a toast confirms.
      } catch (err) {
        console.error("[ProjectCard] move-to-shelf failed", err);
        // Re-throw so the dialog paints its inline error state.
        throw err;
      }
    },
    [onMoveToShelf],
  );

  // Archive / Restore share a code path — the parent hands back whichever
  // handler is relevant for the current view. Both are fire-and-forget
  // from the card's perspective; the parent updates its list state and
  // toasts the outcome.
  const handleArchive = useCallback(async () => {
    if (!onArchive) return;
    setMenuOpen(false);
    try {
      await onArchive();
    } catch (err) {
      console.error("[ProjectCard] archive request failed", err);
    }
  }, [onArchive]);

  const handleUnarchive = useCallback(async () => {
    if (!onUnarchive) return;
    setMenuOpen(false);
    try {
      await onUnarchive();
    } catch (err) {
      console.error("[ProjectCard] unarchive request failed", err);
    }
  }, [onUnarchive]);

  // Deep-clones this project on the backend. The store copies topics
  // (with positions), relationships, decisions, open questions, risks,
  // and the full Q&A history; the title gains a " (copy)" suffix. Shelf
  // membership and share tokens are intentionally NOT copied. We reload
  // the page after success so the new card appears in the grid without
  // having to plumb a refresh callback through every enclosing component.
  const [duplicating, setDuplicating] = useState(false);
  const handleDuplicate = useCallback(async () => {
    setMenuOpen(false);
    if (duplicating) return;
    setDuplicating(true);
    try {
      await api.duplicateProject(project.project_id);
      toast.success(t("projects.duplicate.toast", { title: project.title }));
      // A small tick lets the toast render before the page rehydrates,
      // so the user sees the success message in the refreshed view.
      setTimeout(() => {
        window.location.reload();
      }, 50);
    } catch (err) {
      console.error("[ProjectCard] duplicate failed", err);
      toast.error(t("projects.duplicate.failed"));
      setDuplicating(false);
    }
  }, [duplicating, project.project_id, project.title]);

  const showCounts = topicsCount != null || decisionsCount != null;
  const updatedLabel =
    formatRelativeTime(project.updated_at || project.created_at) ||
    t("projects.card.updated_recently");
  // Founder-requested: surface the creation date alongside the
  // updated date so older vs newer projects are distinguishable at a
  // glance. We hide the line when the project is < 24h old (where
  // "created" and "updated" would both read as "just now" — no signal).
  const createdLabel = project.created_at
    ? formatRelativeTime(project.created_at)
    : null;
  const showCreatedLine = (() => {
    if (!project.created_at) return false;
    const createdMs = new Date(project.created_at).getTime();
    if (Number.isNaN(createdMs)) return false;
    const ageMs = Date.now() - createdMs;
    return ageMs >= 24 * 60 * 60 * 1000; // ≥ 24 hours
  })();

  // Menu visibility: the archive/restore item is present only when the
  // matching handler was wired AND the view state matches. Archive view
  // swaps the action rather than showing both — a single entry point
  // into the lifecycle each direction is clearer than a row of twins.
  const showArchiveItem = !archived && typeof onArchive === "function";
  const showRestoreItem = archived && typeof onUnarchive === "function";

  const cardClassName = archived
    ? "project-card project-card--archived"
    : "project-card";

  return (
    <>
    <button
      type="button"
      className={cardClassName}
      onClick={onOpen}
      aria-label={t("projects.card.open_aria", { title: project.title })}
    >
      <h3 className="project-card__title" title={project.title}>
        {project.title}
      </h3>
      <p className="project-card__updated">{t("projects.card.updated", { when: updatedLabel })}</p>
      {showCreatedLine && createdLabel ? (
        <p className="project-card__created">
          {t("projects.card.created", { when: createdLabel })}
        </p>
      ) : null}
      <div className="project-card__spacer" />
      {showCounts ? (
        <div className="project-card__meta" aria-hidden={false}>
          {topicsCount != null ? (
            <span className="project-card__pill">
              {topicsCount === 1
                ? t("projects.card.topic_one")
                : t("projects.card.topic_many", { count: String(topicsCount) })}
            </span>
          ) : null}
          {decisionsCount != null ? (
            <span className="project-card__pill">
              {decisionsCount === 1
                ? t("projects.card.decision_one")
                : t("projects.card.decision_many", { count: String(decisionsCount) })}
            </span>
          ) : null}
        </div>
      ) : null}

      {archived ? (
        <span
          className="project-card__archived-watermark"
          aria-hidden="true"
        >
          ARCHIVED
        </span>
      ) : null}

      <div
        className="project-card__menu-wrap"
        // Stop the container's click/keydown from bubbling to the parent
        // card button — we don't want opening the kebab menu or clicking
        // an item inside it to also fire onOpen().
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => e.stopPropagation()}
      >
        <button
          ref={triggerRef}
          type="button"
          className="project-card__menu-trigger"
          aria-haspopup="menu"
          aria-expanded={menuOpen}
          aria-label={t("projects.card.options_aria", { title: project.title })}
          onClick={(e) => {
            e.stopPropagation();
            setMenuOpen((v) => !v);
          }}
        >
          <span aria-hidden="true">{"\u22EF"}</span>
        </button>
        {menuOpen ? (
          <div ref={menuRef} className="project-card__menu" role="menu">
            <button
              type="button"
              role="menuitem"
              className="project-card__menu-item"
              onClick={handleRenameClick}
            >
              {t("projects.card.rename")}
            </button>
            {/* L6 (#038) — Move to shelf. Renders only when both
                `shelves` and `onMoveToShelf` are wired. The archived
                view doesn't expose shelf membership so it omits the
                item by passing neither. */}
            {onMoveToShelf && shelves ? (
              <button
                type="button"
                role="menuitem"
                className="project-card__menu-item"
                onClick={handleMoveClick}
              >
                {t("projects.card.move_to_shelf")}
              </button>
            ) : null}
            {showArchiveItem ? (
              <button
                type="button"
                role="menuitem"
                className="project-card__menu-item"
                onClick={handleArchive}
              >
                <span
                  className="project-card__menu-icon"
                  aria-hidden="true"
                >
                  {/* Tiny archive-box glyph — outline + lid, 14px.   */}
                  <svg
                    width="14"
                    height="14"
                    viewBox="0 0 14 14"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="1.2"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  >
                    <rect x="1.5" y="2" width="11" height="3" rx="0.5" />
                    <path d="M2.5 5v5.5a1 1 0 0 0 1 1h7a1 1 0 0 0 1-1V5" />
                    <line x1="5.5" y1="7.5" x2="8.5" y2="7.5" />
                  </svg>
                </span>
                {t("projects.archive.action")}
              </button>
            ) : null}
            {showRestoreItem ? (
              <button
                type="button"
                role="menuitem"
                className="project-card__menu-item"
                onClick={handleUnarchive}
              >
                {t("projects.archive.restore")}
              </button>
            ) : null}
            <button
              type="button"
              role="menuitem"
              className="project-card__menu-item"
              onClick={handleDuplicate}
              disabled={duplicating}
            >
              {/* Two-card overlay icon: a back card offset up-left, a
                  front card offset down-right. Warm editorial — stroke
                  only, no fill — matches the kebab and the other
                  toolbar glyphs. */}
              <svg
                className="project-card__menu-icon"
                width="14"
                height="14"
                viewBox="0 0 16 16"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.25"
                strokeLinejoin="round"
                aria-hidden="true"
              >
                <rect x="3" y="1.5" width="8" height="10" rx="1.25" />
                <rect x="5.5" y="4.5" width="8" height="10" rx="1.25" />
              </svg>
              {t("projects.duplicate.action")}
            </button>
            <button
              type="button"
              role="menuitem"
              className="project-card__menu-item project-card__menu-item--danger"
              onClick={handleDelete}
            >
              {t("projects.card.delete")}
            </button>
          </div>
        ) : null}
      </div>
    </button>
    <RenameProjectDialog
      open={renameOpen}
      currentTitle={project.title}
      onSubmit={handleRenameSubmit}
      onClose={() => setRenameOpen(false)}
    />
    {/* L6 — Move-to-shelf dialog. Mount unconditionally so a closed
        dialog isn't a render cost — the inner Dialog gates its DOM
        on `open`. shelves can be empty; the dialog still surfaces
        the Unfiled option. */}
    {onMoveToShelf ? (
      <MoveToShelfDialog
        open={moveDialogOpen}
        shelves={shelves ?? []}
        currentShelfId={project.shelf_id ?? null}
        projectTitle={project.title}
        onMove={handleMoveSubmit}
        onClose={() => setMoveDialogOpen(false)}
      />
    ) : null}
    </>
  );
}

// Skeleton variant — rendered by the loading state in ProjectsListPage.
// Shares the card footprint so the grid doesn't reflow when real data lands.
export function ProjectCardSkeleton() {
  return (
    <div className="project-card project-card--skeleton" aria-hidden="true">
      <div className="skeleton skeleton--line project-card__skel-title" />
      <div className="skeleton skeleton--line project-card__skel-meta" />
      <div className="project-card__spacer" />
      <div className="skeleton skeleton--line project-card__skel-pills" />
    </div>
  );
}
