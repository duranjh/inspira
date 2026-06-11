// ShelfRow — a horizontal strip of ProjectCards under a ShelfHeader.
//
// Shape: ShelfHeader on the top (serif shelf name + count chip + kebab),
// a collapsible body that renders a ProjectCard grid. Each card is a
// native HTML5 drag source; the row itself is a drop target that
// highlights with a sage-dashed border on dragover. On drop the parent
// view receives the project_id + target shelf_id and decides the fetch.
//
// Collapse state persists per-shelf in localStorage so a user's "closed"
// shelf stays closed across reloads.

import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type DragEvent,
} from "react";

import { ProjectCard } from "../projects/ProjectCard";
import { ShelfHeader } from "./ShelfHeader";
import type { Shelf, V2Project } from "../inspira/api";
import { t } from "../../i18n";

export type ShelfRowProps = {
  shelfId: string; // the synthetic "unfiled" row uses "__unfiled__"
  name: string;
  projects: V2Project[];
  topicsCountByProject?: Map<string, number>;
  decisionsCountByProject?: Map<string, number>;
  // Set of project_ids to highlight with a sage "just moved" dot — the
  // parent owns this set and clears it after ~1.5s so the dot fades.
  recentlyMovedProjectIds?: ReadonlySet<string>;
  onOpenProject: (projectId: string) => void;
  onRenameProject: (projectId: string, newTitle: string) => Promise<void>;
  onDeleteProject: (projectId: string) => Promise<void>;
  // Optional — when wired, each card's kebab menu exposes Archive.
  onArchiveProject?: (projectId: string) => Promise<void>;
  // Shelf-level ops. Omit when the row is the implicit "Unfiled" shelf.
  onRenameShelf?: (nextName: string) => Promise<void> | void;
  onDeleteShelf?: () => Promise<void> | void;
  // Drop handler — fires when the user drops a project on this row. The
  // parent is responsible for both the optimistic update and the server
  // call; we only hand back the dragged project_id + our shelf_id.
  onMoveProjectToShelf: (projectId: string, shelfId: string | null) => void;
  // L6 — full list of the user's shelves. Threaded into each
  // ProjectCard so the new "Move to…" menu item's picker dialog can
  // render the full shelf list. Optional for back-compat; if absent
  // (e.g. test fixtures), the menu item is omitted.
  allShelves?: Shelf[];
  // True for the implicit "Unfiled" row — disables the kebab menu and
  // changes the empty placeholder copy. The row's `shelfId` for
  // onMoveProjectToShelf is always null when `isUnfiled`.
  isUnfiled?: boolean;
};

// DataTransfer MIME type used to carry the dragged project_id. Custom
// types require text/plain fallback for maximal browser compatibility;
// we stash the id in both so `getData("text/plain")` works in tests.
const DRAG_TYPE = "application/x-inspira-project-id";
const DRAG_FALLBACK = "text/plain";

const COLLAPSE_KEY_PREFIX = "inspira:shelf-collapsed:";

function readCollapsed(shelfId: string): boolean {
  if (typeof window === "undefined") return false;
  try {
    return window.localStorage.getItem(COLLAPSE_KEY_PREFIX + shelfId) === "1";
  } catch {
    return false;
  }
}

function writeCollapsed(shelfId: string, value: boolean): void {
  if (typeof window === "undefined") return;
  try {
    if (value) {
      window.localStorage.setItem(COLLAPSE_KEY_PREFIX + shelfId, "1");
    } else {
      window.localStorage.removeItem(COLLAPSE_KEY_PREFIX + shelfId);
    }
  } catch {
    /* localStorage full / disabled — collapse is cosmetic, ignore. */
  }
}

export function ShelfRow({
  shelfId,
  name,
  projects,
  topicsCountByProject,
  decisionsCountByProject,
  recentlyMovedProjectIds,
  onOpenProject,
  onRenameProject,
  onDeleteProject,
  onArchiveProject,
  onRenameShelf,
  onDeleteShelf,
  onMoveProjectToShelf,
  allShelves,
  isUnfiled = false,
}: ShelfRowProps) {
  // Local collapse state, seeded from localStorage. `useState` lazy init
  // keeps the read off the render hot path after the first paint.
  const [collapsed, setCollapsed] = useState<boolean>(() =>
    readCollapsed(shelfId),
  );
  const [isDragOver, setIsDragOver] = useState<boolean>(false);

  // Persist collapse state whenever it flips.
  useEffect(() => {
    writeCollapsed(shelfId, collapsed);
  }, [shelfId, collapsed]);

  const handleToggleCollapsed = useCallback(() => {
    setCollapsed((v) => !v);
  }, []);

  // Native HTML5 DnD handlers. We accept only drags whose data includes
  // our custom type, so random file drops or text selections don't
  // trigger a fake "move" call.
  const handleDragOver = useCallback((e: DragEvent<HTMLDivElement>) => {
    // preventDefault is REQUIRED to mark this element as a drop target.
    e.preventDefault();
    if (e.dataTransfer) {
      e.dataTransfer.dropEffect = "move";
    }
    setIsDragOver(true);
  }, []);

  const handleDragLeave = useCallback((e: DragEvent<HTMLDivElement>) => {
    // `dragleave` fires for every child; ignore if the pointer is still
    // inside the row (the relatedTarget is a descendant).
    const related = e.relatedTarget as Node | null;
    if (related && e.currentTarget.contains(related)) return;
    setIsDragOver(false);
  }, []);

  const targetShelfId = isUnfiled ? null : shelfId;

  const handleDrop = useCallback(
    (e: DragEvent<HTMLDivElement>) => {
      e.preventDefault();
      setIsDragOver(false);
      const data =
        e.dataTransfer.getData(DRAG_TYPE) ||
        e.dataTransfer.getData(DRAG_FALLBACK);
      if (!data) return;
      // Already on this shelf? Still fine to call — the parent can
      // dedupe. But we short-circuit here to save a round-trip.
      const alreadyHere = projects.some((p) => p.project_id === data);
      if (alreadyHere) return;
      onMoveProjectToShelf(data, targetShelfId);
    },
    [onMoveProjectToShelf, targetShelfId, projects],
  );

  // Sort projects by most-recently-updated first inside a shelf — matches
  // the "recent" default on the flat grid so users have a familiar order.
  const sortedProjects = useMemo(() => {
    return projects.slice().sort((a, b) => {
      const bt = Date.parse(b.updated_at || "") || 0;
      const at = Date.parse(a.updated_at || "") || 0;
      return bt - at;
    });
  }, [projects]);

  const isEmpty = sortedProjects.length === 0;

  return (
    <section
      className={
        "shelf-row" +
        (isDragOver ? " shelf-row--dragover" : "") +
        (collapsed ? " shelf-row--collapsed" : "")
      }
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
      aria-label={t("shelves.row.aria", { name })}
    >
      <ShelfHeader
        name={name}
        projectCount={sortedProjects.length}
        collapsed={collapsed}
        onToggleCollapsed={handleToggleCollapsed}
        onRename={onRenameShelf}
        onDelete={onDeleteShelf}
        isUnfiled={isUnfiled}
      />
      {!collapsed ? (
        <div className="shelf-row__body">
          {isEmpty ? (
            <div
              className="shelf-row__empty"
              role="note"
              aria-live="polite"
            >
              <p className="shelf-row__empty-text">
                {isUnfiled
                  ? t("shelves.row.empty_unfiled")
                  : t("shelves.row.empty_drop_hint")}
              </p>
              {isUnfiled ? (
                <p className="shelf-row__empty-hint">
                  {t("shelves.row.empty_unfiled_hint")}
                </p>
              ) : null}
            </div>
          ) : (
            <div className="shelf-row__grid">
              {sortedProjects.map((p) => (
                <div
                  key={p.project_id}
                  className={
                    "shelf-row__card-wrap" +
                    (recentlyMovedProjectIds?.has(p.project_id)
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
                    onRename={(next) => onRenameProject(p.project_id, next)}
                    onDelete={() => onDeleteProject(p.project_id)}
                    onArchive={
                      onArchiveProject
                        ? () => onArchiveProject(p.project_id)
                        : undefined
                    }
                    shelves={allShelves}
                    onMoveToShelf={async (shelfIdOrNull) => {
                      // Funnel through the same drop-handler the parent
                      // already wired — keeps the optimistic update +
                      // toast logic in one place.
                      onMoveProjectToShelf(p.project_id, shelfIdOrNull);
                    }}
                  />
                </div>
              ))}
            </div>
          )}
        </div>
      ) : null}
    </section>
  );
}
