// ShelvesView — warm-editorial replacement for the flat project grid
// when the user has at least one shelf.
//
// Shape: hero header (serif "Your projects." + italic subtext + pill
// buttons for New shelf / New project), then one ShelfRow per user
// shelf in sort_order, then a plain "Unfiled" section (permanent
// header, not a shelf) as the catch-all at the bottom. The
// NewShelfDialog opens from the pill in the header.
//
// Drag-and-drop: each ProjectCard inside a ShelfRow is a native HTML5
// drag source; each ShelfRow AND the unfiled section are drop targets.
// On drop the view:
//   1. Optimistically updates local `projects` state (moves the card
//      to the target shelf_id synchronously).
//   2. Marks the moved project_id in `recentlyMoved` so the row paints
//      the sage "just moved" dot; clears the mark after 1.5s.
//   3. Calls `onMoveProjectToShelf` (parent-owned fetch).
//   4. Rolls the optimistic move back if the parent's Promise rejects.
//
// No emojis. Warm editorial only.

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type DragEvent,
} from "react";

import "./shelves.css";
import { NewShelfDialog } from "./NewShelfDialog";
import { ShelfRow } from "./ShelfRow";
import { ProjectCard } from "../projects/ProjectCard";
import type { Shelf, V2Project } from "../inspira/api";
import { ErrorBoundary } from "../../components/ErrorBoundary";
import { toast } from "../../components/ToastProvider";
import { t } from "../../i18n";

// Must match ShelfRow's MIME types so a card dragged out of any shelf
// row lands here as a valid drop.
const DRAG_TYPE = "application/x-inspira-project-id";
const DRAG_FALLBACK = "text/plain";

export type ShelvesViewProps = {
  projects: V2Project[];
  shelves: Shelf[];
  topicsCountByProject?: Map<string, number>;
  decisionsCountByProject?: Map<string, number>;
  onOpenProject: (projectId: string) => void;
  onCreateNewProject: () => void;
  onCreateNewShelf: (name: string) => Promise<void>;
  onRenameShelf: (shelfId: string, nextName: string) => Promise<void>;
  onDeleteShelf: (shelfId: string) => Promise<void>;
  // Parent owns the server round-trip. If the returned Promise rejects
  // we roll the optimistic move back.
  onMoveProjectToShelf: (
    projectId: string,
    shelfIdOrNull: string | null,
  ) => Promise<void>;
  onRenameProject: (projectId: string, newTitle: string) => Promise<void>;
  onDeleteProject: (projectId: string) => Promise<void>;
  // Optional archive handler — when wired, each ProjectCard's kebab
  // menu exposes "Archive" alongside Rename/Duplicate/Delete, matching
  // the flat-grid behaviour. Without this wire-through, archive
  // silently disappears from the menu on the shelves view — a UX gap.
  onArchiveProject?: (projectId: string) => Promise<void>;
  // Optional footer-nav handlers. When provided, render the
  // "Archived projects → / Recently deleted →" link pair below the
  // shelves list. Without them the footer is silently omitted so older
  // callers stay unchanged.
  onViewArchived?: () => void;
  onViewRecentlyDeleted?: () => void;
};

// Synthetic shelf_id used for the catch-all "Unfiled" row. Not a real id
// — it never leaves the component. The move handler translates this
// sentinel to `null` when calling `onMoveProjectToShelf`.
const UNFILED_SHELF_ID = "__unfiled__";

// How long the sage "just moved" dot stays on a card after a successful
// drop. 1.5s is long enough for the user to notice without being sticky.
const MOVED_DOT_TTL_MS = 1500;

export function ShelvesView({
  projects,
  shelves,
  topicsCountByProject,
  decisionsCountByProject,
  onOpenProject,
  onCreateNewProject,
  onCreateNewShelf,
  onRenameShelf,
  onDeleteShelf,
  onMoveProjectToShelf,
  onRenameProject,
  onDeleteProject,
  onArchiveProject,
  onViewArchived,
  onViewRecentlyDeleted,
}: ShelvesViewProps) {
  // Local mirror of the `shelf_id` assignment so drag-drop can update the
  // UI synchronously while the backend call flies. Keyed by project_id.
  // When the parent-owned `projects` array changes (new data from a
  // refetch) we reseed this map from the parent; the optimistic override
  // only wins briefly.
  const [localShelfId, setLocalShelfId] = useState<Map<string, string | null>>(
    () => new Map(),
  );
  const [recentlyMoved, setRecentlyMoved] = useState<Set<string>>(
    () => new Set(),
  );
  const [showNewShelfDialog, setShowNewShelfDialog] = useState(false);
  const movedTimers = useRef<Map<string, number>>(new Map());

  // Clear movedTimers on unmount so a late tick doesn't try to setState
  // on a dead component.
  useEffect(() => {
    const timers = movedTimers.current;
    return () => {
      for (const id of timers.values()) window.clearTimeout(id);
      timers.clear();
    };
  }, []);

  // Effective shelf_id for a project: local override if present, else the
  // server-side value. Missing / empty string normalise to null.
  const effectiveShelfId = useCallback(
    (p: V2Project): string | null => {
      if (localShelfId.has(p.project_id)) {
        return localShelfId.get(p.project_id) ?? null;
      }
      return p.shelf_id ?? null;
    },
    [localShelfId],
  );

  // Bucket projects by shelf. We render unknown shelf_ids (a server row
  // whose shelf_id has since been deleted) into the Unfiled bucket; the
  // store clears those on delete_shelf but a stale client may still have
  // them until the next listShelves.
  const bucketed = useMemo(() => {
    const byShelf = new Map<string, V2Project[]>();
    const validShelfIds = new Set(shelves.map((s) => s.shelf_id));
    for (const p of projects) {
      const sid = effectiveShelfId(p);
      const key = sid && validShelfIds.has(sid) ? sid : UNFILED_SHELF_ID;
      const list = byShelf.get(key) ?? [];
      list.push(p);
      byShelf.set(key, list);
    }
    return byShelf;
  }, [projects, shelves, effectiveShelfId]);

  const orderedShelves = useMemo(() => {
    return shelves
      .slice()
      .sort((a, b) => {
        if (a.sort_order !== b.sort_order) return a.sort_order - b.sort_order;
        return a.name.localeCompare(b.name, undefined, {
          sensitivity: "base",
        });
      });
  }, [shelves]);

  const unfiledProjects = bucketed.get(UNFILED_SHELF_ID) ?? [];

  // "N projects on K shelves". Unfiled is no longer counted as a shelf
  // — it's a plain permanent section below the shelf rows, not a shelf.
  const totalProjects = projects.length;
  const shelvesCountLabel = (() => {
    const effectiveShelves = shelves.length;
    const shelfWord = effectiveShelves === 1
      ? t("shelves.view.shelf_one")
      : t("shelves.view.shelf_many");
    const projectWord = totalProjects === 1
      ? t("shelves.view.project_one")
      : t("shelves.view.project_many");
    return t("shelves.view.count", {
      projects: String(totalProjects),
      project_word: projectWord,
      shelves: String(effectiveShelves),
      shelf_word: shelfWord,
    });
  })();

  // Drag-drop: move the project, mark it as recently moved, then call
  // the parent. Rollback on failure. Updating the localShelfId map
  // instantly gives the UI a crisp feel even with a slow network.
  const markRecentlyMoved = useCallback((projectId: string) => {
    setRecentlyMoved((prev) => {
      const next = new Set(prev);
      next.add(projectId);
      return next;
    });
    const existing = movedTimers.current.get(projectId);
    if (existing) window.clearTimeout(existing);
    const timer = window.setTimeout(() => {
      setRecentlyMoved((prev) => {
        const next = new Set(prev);
        next.delete(projectId);
        return next;
      });
      movedTimers.current.delete(projectId);
    }, MOVED_DOT_TTL_MS);
    movedTimers.current.set(projectId, timer);
  }, []);

  const handleMove = useCallback(
    (projectId: string, targetShelfId: string | null) => {
      // Snapshot previous shelf so we can roll back on server failure.
      const prevProject = projects.find((p) => p.project_id === projectId);
      const prevShelfId = prevProject ? prevProject.shelf_id ?? null : null;

      setLocalShelfId((prev) => {
        const next = new Map(prev);
        next.set(projectId, targetShelfId);
        return next;
      });
      markRecentlyMoved(projectId);

      void onMoveProjectToShelf(projectId, targetShelfId).catch((err) => {
        console.error("[ShelvesView] move failed, rolling back", err);
        setLocalShelfId((prev) => {
          const next = new Map(prev);
          next.set(projectId, prevShelfId);
          return next;
        });
      });
    },
    [projects, onMoveProjectToShelf, markRecentlyMoved],
  );

  // When the parent hands us a fresh `projects` array whose shelf_id
  // matches our optimistic value, drop the override — the server has
  // caught up. Done in a useEffect so we don't fight with a drop that
  // landed in the same render pass.
  useEffect(() => {
    if (localShelfId.size === 0) return;
    setLocalShelfId((prev) => {
      const next = new Map(prev);
      for (const p of projects) {
        if (!next.has(p.project_id)) continue;
        const serverShelf = p.shelf_id ?? null;
        if (next.get(p.project_id) === serverShelf) {
          next.delete(p.project_id);
        }
      }
      return next;
    });
    // Intentionally omit `localShelfId` from deps — we're reading inside
    // a setter so we always see fresh state, and including it would
    // loop on every setLocalShelfId call.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projects]);

  const handleCreateShelfFromDialog = useCallback(
    async (name: string) => {
      try {
        await onCreateNewShelf(name);
        setShowNewShelfDialog(false);
      } catch (err) {
        // Rethrow so the dialog's inline error surfaces, but log + toast
        // so a silent backend failure leaves a trace for the user and
        // Sentry/console.
        console.error("[ShelvesView] shelf creation failed", err);
        toast.error(t("shelves.create_error_toast"));
        throw err;
      }
    },
    [onCreateNewShelf],
  );

  const handleRenameShelfById = useCallback(
    (shelfId: string) => (nextName: string) => onRenameShelf(shelfId, nextName),
    [onRenameShelf],
  );

  const handleDeleteShelfById = useCallback(
    (shelfId: string) => () => onDeleteShelf(shelfId),
    [onDeleteShelf],
  );

  // Drop handlers for the unfiled catch-all section. Mirrors ShelfRow's
  // logic but stays inline here because the section isn't a shelf.
  const [unfiledDragOver, setUnfiledDragOver] = useState<boolean>(false);

  const handleUnfiledDragOver = useCallback((e: DragEvent<HTMLElement>) => {
    e.preventDefault();
    if (e.dataTransfer) {
      e.dataTransfer.dropEffect = "move";
    }
    setUnfiledDragOver(true);
  }, []);

  const handleUnfiledDragLeave = useCallback((e: DragEvent<HTMLElement>) => {
    const related = e.relatedTarget as Node | null;
    if (related && e.currentTarget.contains(related)) return;
    setUnfiledDragOver(false);
  }, []);

  const handleUnfiledDrop = useCallback(
    (e: DragEvent<HTMLElement>) => {
      e.preventDefault();
      setUnfiledDragOver(false);
      const data =
        e.dataTransfer.getData(DRAG_TYPE) ||
        e.dataTransfer.getData(DRAG_FALLBACK);
      if (!data) return;
      // Short-circuit if the project is already unfiled — saves a
      // round-trip.
      const alreadyUnfiled = unfiledProjects.some(
        (p) => p.project_id === data,
      );
      if (alreadyUnfiled) return;
      handleMove(data, null);
    },
    [handleMove, unfiledProjects],
  );

  return (
    <div className="shelves-view">
      <div className="shelves-view__inner">
        <header className="shelves-view__header">
          <div className="shelves-view__header-text">
            <h1 className="shelves-view__title">{t("shelves.view.title")}</h1>
            <p className="shelves-view__subtext">{shelvesCountLabel}</p>
          </div>
          <div className="shelves-view__header-actions">
            <button
              type="button"
              className="shelves-view__new-shelf-btn"
              onClick={() => setShowNewShelfDialog(true)}
            >
              {t("shelves.view.new_shelf")}
            </button>
            <button
              type="button"
              className="shelves-view__new-project-btn"
              onClick={onCreateNewProject}
            >
              {t("shelves.view.new_project")}
            </button>
          </div>
        </header>

        <div className="shelves-view__rows">
          {orderedShelves.map((shelf) => (
            <ShelfRow
              key={shelf.shelf_id}
              shelfId={shelf.shelf_id}
              name={shelf.name}
              projects={bucketed.get(shelf.shelf_id) ?? []}
              topicsCountByProject={topicsCountByProject}
              decisionsCountByProject={decisionsCountByProject}
              recentlyMovedProjectIds={recentlyMoved}
              onOpenProject={onOpenProject}
              onRenameProject={onRenameProject}
              onDeleteProject={onDeleteProject}
              onArchiveProject={onArchiveProject}
              onRenameShelf={handleRenameShelfById(shelf.shelf_id)}
              onDeleteShelf={handleDeleteShelfById(shelf.shelf_id)}
              onMoveProjectToShelf={handleMove}
              allShelves={shelves}
            />
          ))}
          {/* Unfiled catch-all. Not a shelf — a plain permanent section
              with a heading above a project grid. Stays a drop target
              so a user can drag a card out of any shelf to un-shelve
              it, but has no kebab, no collapse, no "empty state" card. */}
          <section
            className={
              "shelves-view__unfiled" +
              (unfiledDragOver ? " shelves-view__unfiled--dragover" : "")
            }
            onDragOver={handleUnfiledDragOver}
            onDragLeave={handleUnfiledDragLeave}
            onDrop={handleUnfiledDrop}
            aria-label={t("shelves.row.aria", {
              name: t("shelves.view.unfiled_name"),
            })}
          >
            {/* The section itself is always a drop target (so a user can
                drag a card out of any shelf to unshelve it), but we only
                render the "Unfiled" heading when there's at least one
                unfiled project. An empty heading just looks broken. The
                drag-over CSS hint still gives feedback mid-drag. */}
            {unfiledProjects.length > 0 ? (
              <h2 className="shelves-view__unfiled-heading">
                {t("shelves.view.unfiled_name")}
              </h2>
            ) : null}
            {unfiledProjects.length > 0 ? (
              <div className="shelves-view__unfiled-grid">
                {unfiledProjects.map((p) => (
                  <div
                    key={p.project_id}
                    className={
                      "shelf-row__card-wrap" +
                      (recentlyMoved.has(p.project_id)
                        ? " shelf-row__card-wrap--moved"
                        : "")
                    }
                    draggable
                    onDragStart={(e) => {
                      e.dataTransfer.effectAllowed = "move";
                      e.dataTransfer.setData(DRAG_TYPE, p.project_id);
                      e.dataTransfer.setData(DRAG_FALLBACK, p.project_id);
                    }}
                  >
                    <ProjectCard
                      project={p}
                      topicsCount={topicsCountByProject?.get(p.project_id)}
                      decisionsCount={decisionsCountByProject?.get(
                        p.project_id,
                      )}
                      onOpen={() => onOpenProject(p.project_id)}
                      onRename={(next) =>
                        onRenameProject(p.project_id, next)
                      }
                      onDelete={() => onDeleteProject(p.project_id)}
                      onArchive={
                        onArchiveProject
                          ? () => onArchiveProject(p.project_id)
                          : undefined
                      }
                      shelves={shelves}
                      onMoveToShelf={async (shelfIdOrNull) => {
                        // Same handler the row's drop event uses.
                        handleMove(p.project_id, shelfIdOrNull);
                      }}
                    />
                  </div>
                ))}
              </div>
            ) : null}
          </section>

          {/* Footer nav — mirror of the flat-grid footer so a user with
              shelves can still reach archived + recently-deleted views.
              Rendered only when the parent wires the handlers, so the
              shelves view stays functional if the footer is opted-out. */}
          {(onViewArchived || onViewRecentlyDeleted) ? (
            <div className="projects-list__archived-footer">
              {onViewArchived ? (
                <button
                  type="button"
                  className="projects-list__archived-link"
                  onClick={onViewArchived}
                >
                  {t("projects.archive.view_archived")}
                </button>
              ) : null}
              {onViewRecentlyDeleted ? (
                <button
                  type="button"
                  className="projects-list__archived-link"
                  onClick={onViewRecentlyDeleted}
                >
                  {t("projects.recently_deleted.view")}
                </button>
              ) : null}
            </div>
          ) : null}
        </div>
      </div>

      {/* Local ErrorBoundary: a crash inside the shelf-creation dialog must
          not take down the whole homepage. If the dialog throws during a
          render pass we silently dismiss it and surface a toast, letting
          the user continue using the shelves grid. */}
      <ErrorBoundary
        fallback={({ reset }) => {
          setTimeout(() => {
            setShowNewShelfDialog(false);
            reset();
            toast.error(t("shelves.create_error_toast"));
          }, 0);
          return null;
        }}
        onError={(err) => {
          console.error("[ShelvesView] shelf dialog crashed", err);
        }}
      >
        <NewShelfDialog
          open={showNewShelfDialog}
          onSubmit={handleCreateShelfFromDialog}
          onClose={() => setShowNewShelfDialog(false)}
        />
      </ErrorBoundary>
    </div>
  );
}
