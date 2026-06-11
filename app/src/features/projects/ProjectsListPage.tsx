// ProjectsListPage — the "all your projects" grid.
//
// Layout (below the 60px top-bar that InspiraApp still owns):
//   - Header: serif display title + italic subtext + "New project" pill.
//   - Toolbar: local filter input, sort dropdown, result count.
//   - Grid: responsive auto-fill of ProjectCards (minmax 280px, 1fr).
//   - Empty state (no projects at all): centered paper card + big CTA.
//   - No-matches state (filter has no hits): italic inline note.
//   - Loading: six ProjectCardSkeletons in the same grid.
//
// When `shelves.length > 0` we hand off to the ShelvesView (warm editorial
// layered layout with horizontal per-shelf rows). A user with zero shelves
// still gets the familiar flat grid. Callers that haven't wired shelves
// yet can omit the shelves props entirely — the flat grid falls through.
//
// This page does NOT render the top bar. Caller wiring (InspiraApp) is
// responsible for rendering the brand and user-menu chrome above this
// page.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import "./projects.css";
import { ProjectCard, ProjectCardSkeleton } from "./ProjectCard";
import { NewShelfDialog, ShelvesView } from "../shelves";
import { ShelfErrorBoundary } from "../../components/ShelfErrorBoundary";
import { DeleteConfirmDialog } from "../../components/dialogs";
import { Coachmark, type CoachmarkStep } from "../../components/Coachmark";
import { ErrorBoundary } from "../../components/ErrorBoundary";
import { toast } from "../../components/ToastProvider";
import { safeStorage } from "../../lib/safeStorage";
import { api } from "../inspira/api";
import type { Shelf, V2Project } from "../inspira/api";
import { t, useLocale, formatRelativeTime } from "../../i18n";

// ---- Homepage coachmark steps ----------------------------------------------

const HOME_STEPS: CoachmarkStep[] = [
  {
    id: "home-project-card",
    targetSelector: ".projects-list__grid .project-card",
    title: t("home_onboard.1.title"),
    body: t("home_onboard.1.body"),
    placement: "bottom",
  },
  {
    id: "home-new-project",
    targetSelector: ".projects-list__new-btn",
    title: t("home_onboard.2.title"),
    body: t("home_onboard.2.body"),
    placement: "bottom",
  },
  {
    id: "home-new-shelf",
    // Silently skipped by Coachmark if not visible.
    targetSelector: ".projects-list__new-shelf-btn",
    title: t("home_onboard.3.title"),
    body: t("home_onboard.3.body"),
    placement: "bottom",
  },
];

export type ProjectsListPageProps = {
  projects: V2Project[];
  decisionsCountByProject?: Map<string, number>;
  topicsCountByProject?: Map<string, number>;
  onOpenProject: (projectId: string) => void;
  onCreateNew: () => void;
  onRename: (projectId: string, newTitle: string) => Promise<void>;
  onDelete: (projectId: string) => Promise<void>;
  loading?: boolean;
  // Optional callback to pre-fill kickoff with a suggestion text.
  onSuggestStart?: (idea: string) => void;
  // Shelves wiring. All five props are optional — if `shelves` is absent
  // or empty the page falls through to the flat grid unchanged.
  shelves?: Shelf[];
  onCreateNewShelf?: (name: string) => Promise<void>;
  onRenameShelf?: (shelfId: string, nextName: string) => Promise<void>;
  onDeleteShelf?: (shelfId: string) => Promise<void>;
  onMoveProjectToShelf?: (
    projectId: string,
    shelfIdOrNull: string | null,
  ) => Promise<void>;
};

type SortKey = "recent" | "alpha" | "oldest";

const SORT_VALUES: readonly SortKey[] = ["recent", "alpha", "oldest"];

// Comparator helpers. We treat missing timestamps as the epoch so they sort
// to the bottom of "recent" and the top of "oldest" — matches the intuition
// that a project with no updated_at is effectively brand-new metadata.
function cmpRecent(a: V2Project, b: V2Project): number {
  const bt = Date.parse(b.updated_at || "") || 0;
  const at = Date.parse(a.updated_at || "") || 0;
  return bt - at;
}

function cmpOldest(a: V2Project, b: V2Project): number {
  const at = Date.parse(a.created_at || "") || 0;
  const bt = Date.parse(b.created_at || "") || 0;
  return at - bt;
}

function cmpAlpha(a: V2Project, b: V2Project): number {
  return a.title.localeCompare(b.title, undefined, { sensitivity: "base" });
}

// Human-readable "last edit N ago" for the header subtext. Finds the most
// recent timestamp across all projects and delegates to formatRelativeTime.
function describeLastEdit(projects: V2Project[]): string {
  if (projects.length === 0) return t("projects.list.no_edits_yet");
  let latest = 0;
  for (const p of projects) {
    const ts = Date.parse(p.updated_at || p.created_at || "") || 0;
    if (ts > latest) latest = ts;
  }
  if (!latest) return t("projects.list.recently");
  return formatRelativeTime(new Date(latest)) || t("projects.list.recently");
}

export function ProjectsListPage({
  projects,
  topicsCountByProject,
  decisionsCountByProject,
  onOpenProject,
  onCreateNew,
  onRename,
  onDelete,
  loading = false,
  onSuggestStart,
  shelves,
  onCreateNewShelf,
  onRenameShelf,
  onDeleteShelf,
  onMoveProjectToShelf,
}: ProjectsListPageProps) {
  // Subscribe to locale changes so date strings (describeLastEdit) re-render.
  useLocale();
  // `rawQuery` is the input's controlled value — it updates on every
  // keystroke so the caret doesn't jitter. `query` is the debounced copy
  // used for filtering; it trails rawQuery by ~150ms so typing fast
  // doesn't re-filter on every key. Empty string on either path means
  // "show all".
  const [rawQuery, setRawQuery] = useState("");
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<SortKey>("recent");
  const searchInputRef = useRef<HTMLInputElement | null>(null);

  // Debounce rawQuery → query. 150ms matches the spec and still feels
  // instant to the eye. Cleared on unmount so a pending update doesn't
  // setState after the page goes away.
  useEffect(() => {
    const id = setTimeout(() => setQuery(rawQuery), 150);
    return () => clearTimeout(id);
  }, [rawQuery]);

  // Global "/" shortcut — focus the search input. We attach a local
  // window-level listener rather than going through useKeyboardShortcuts
  // because InspiraApp already registers "/" (for the cross-project
  // search overlay) and the shortcut registry only fires one binding per
  // combo. InspiraApp's handler early-returns when `phase.kind !== "canvas"`,
  // but it's registered first so it wins the match and swallows the
  // event. A local listener sidesteps that entirely — and it's only
  // mounted while ProjectsListPage is on screen, so it naturally scopes
  // itself to this page.
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "/") return;
      // Don't hijack "/" when the user is typing somewhere else.
      const target = event.target as Element | null;
      if (target) {
        const tag = target.tagName;
        if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
        if ((target as HTMLElement).isContentEditable) return;
      }
      // Respect in-flight composition (IME) and modifier combos.
      if (event.ctrlKey || event.metaKey || event.altKey) return;
      const input = searchInputRef.current;
      if (!input) return;
      event.preventDefault();
      input.focus();
      input.select();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  // Homepage coachmark — fires only once the user has at least 1 project,
  // so brand-new signups going straight to kickoff never see it.
  // 400ms delay lets the grid cards paint before we spotlight one.
  const [homeCoachActive, setHomeCoachActive] = useState(false);
  useEffect(() => {
    if (loading || projects.length === 0) return;
    const seen = safeStorage.getItem("inspira_onboarded_homepage");
    if (seen !== "true") {
      const id = setTimeout(() => setHomeCoachActive(true), 400);
      return () => clearTimeout(id);
    }
  }, [loading, projects.length]);
  // Warm-editorial delete confirmation — replaces the old window.confirm.
  // Stores the whole project so the dialog can show the real title; null
  // when no dialog is open. The dialog manages its own busy state.
  const [pendingDelete, setPendingDelete] = useState<V2Project | null>(null);
  // Local modal state for the "New shelf" dialog — opens from the
  // flat-grid header when the user has 0 shelves. When they have 1+
  // shelves we hand off to ShelvesView which owns its own dialog.
  const [newShelfOpen, setNewShelfOpen] = useState(false);

  // AI suggestions — fetched once on mount when user has 2+ projects.
  // The row is hidden when suggestions is empty (fetch pending, error, or
  // fewer than 2 projects). This is a non-critical feature.
  const [suggestions, setSuggestions] = useState<string[]>([]);
  useEffect(() => {
    if (loading || projects.length < 2 || !onSuggestStart) return;
    let cancelled = false;
    api.getHomepageSuggestions().then((s) => {
      if (!cancelled) setSuggestions(s);
    });
    return () => { cancelled = true; };
  }, [loading, projects.length, onSuggestStart]);

  // ---- View mode (active / archived / recently_deleted) ------------------
  // Tri-state in-page navigation: clicking the footer links flips this
  // and lazily fetches the relevant list. A route per mode would have
  // required touching InspiraApp which is off-limits in this worktree.
  type ViewMode = "active" | "archived" | "recently_deleted";
  const [viewMode, setViewMode] = useState<ViewMode>("active");
  const viewingArchived = viewMode === "archived";
  const viewingRecentlyDeleted = viewMode === "recently_deleted";
  const [archivedProjects, setArchivedProjects] = useState<V2Project[]>([]);
  const [archivedLoading, setArchivedLoading] = useState(false);
  const [recentlyDeletedProjects, setRecentlyDeletedProjects] = useState<
    V2Project[]
  >([]);
  const [recentlyDeletedLoading, setRecentlyDeletedLoading] = useState(false);

  // Locally-archived ids — projects the user archived from the default
  // view during this session. We hide them immediately so the grid feels
  // snappy; the next parent refetch will take over. Kept in a Set so
  // the filter below stays O(1).
  const [locallyArchivedIds, setLocallyArchivedIds] = useState<Set<string>>(
    () => new Set(),
  );

  const refreshArchived = useCallback(async () => {
    setArchivedLoading(true);
    try {
      const res = await api.listArchivedProjects();
      setArchivedProjects(res.projects ?? []);
    } catch (err) {
      console.error("[ProjectsListPage] archived list fetch failed", err);
      setArchivedProjects([]);
    } finally {
      setArchivedLoading(false);
    }
  }, []);

  // Fetch the archived list whenever the user enters the archived view.
  // No eager fetch on mount — the archive view is cold by default.
  useEffect(() => {
    if (!viewingArchived) return;
    void refreshArchived();
  }, [viewingArchived, refreshArchived]);

  const refreshRecentlyDeleted = useCallback(async () => {
    setRecentlyDeletedLoading(true);
    try {
      const res = await api.listRecentlyDeletedProjects();
      setRecentlyDeletedProjects(res.projects ?? []);
    } catch (err) {
      console.error("[ProjectsListPage] recently-deleted fetch failed", err);
      setRecentlyDeletedProjects([]);
    } finally {
      setRecentlyDeletedLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!viewingRecentlyDeleted) return;
    void refreshRecentlyDeleted();
  }, [viewingRecentlyDeleted, refreshRecentlyDeleted]);

  // Restore a soft-deleted project. Used both from the Recently Deleted
  // view (clicking "Restore" on a card) and from the post-delete toast's
  // Undo action on the active list.
  //
  // ``viewMode`` decides what we do after success:
  //   - On the recently_deleted view we just remove the row in place.
  //   - On the active view (Undo path) we reload the page to pick up the
  //     restored row in the parent's projects state — the parent doesn't
  //     expose a refresh callback, and reload mirrors the existing
  //     "duplicate" flow's UX.
  const handleRestore = useCallback(async (project: V2Project) => {
    try {
      await api.restoreProject(project.project_id);
      setRecentlyDeletedProjects((prev) =>
        prev.filter((p) => p.project_id !== project.project_id),
      );
      toast.success(
        t("projects.recently_deleted.toast_restored", { title: project.title }),
      );
      if (viewMode === "active") {
        // Small delay so the success toast is visible before reload.
        setTimeout(() => {
          window.location.reload();
        }, 60);
      }
    } catch (err) {
      // 410 means the grace window expired between the user opening the
      // page and clicking Restore. Surface a specific message so they
      // understand it's gone for good rather than a transient failure.
      const status = (err as { status?: number } | null)?.status;
      if (status === 410) {
        toast.error(t("projects.recently_deleted.toast_restore_expired"));
      } else {
        toast.error(t("projects.recently_deleted.toast_restore_failed"));
      }
      console.error("[ProjectsListPage] restore failed", err);
    }
  }, [viewMode]);

  // Pending purge confirmation dialog state — separate from pendingDelete
  // (which is the soft-delete confirmation) because the consequences copy
  // and the source list differ.
  const [pendingPurge, setPendingPurge] = useState<V2Project | null>(null);

  const handlePurge = useCallback(async (project: V2Project) => {
    try {
      await api.purgeProject(project.project_id);
      setRecentlyDeletedProjects((prev) =>
        prev.filter((p) => p.project_id !== project.project_id),
      );
      toast.success(
        t("projects.recently_deleted.toast_purged", { title: project.title }),
      );
    } catch (err) {
      toast.error(t("projects.recently_deleted.toast_purge_failed"));
      console.error("[ProjectsListPage] purge failed", err);
      throw err;
    }
  }, []);

  const handleArchive = useCallback(async (project: V2Project) => {
    try {
      await api.archiveProject(project.project_id);
      setLocallyArchivedIds((prev) => {
        const next = new Set(prev);
        next.add(project.project_id);
        return next;
      });
      toast.success(
        t("projects.archive.toast_archived", { title: project.title }),
      );
    } catch (err) {
      console.error("[ProjectsListPage] archive failed", err);
    }
  }, []);

  const handleUnarchive = useCallback(async (project: V2Project) => {
    try {
      await api.unarchiveProject(project.project_id);
      // Remove from the archive view locally.
      setArchivedProjects((prev) =>
        prev.filter((p) => p.project_id !== project.project_id),
      );
      // And clear from the locally-archived-in-session set so a
      // re-archive flows through the normal path.
      setLocallyArchivedIds((prev) => {
        if (!prev.has(project.project_id)) return prev;
        const next = new Set(prev);
        next.delete(project.project_id);
        return next;
      });
      toast.success(
        t("projects.archive.toast_unarchived", { title: project.title }),
      );
    } catch (err) {
      console.error("[ProjectsListPage] unarchive failed", err);
    }
  }, []);

  // Filter first (case-insensitive substring match on title), then sort.
  // Both pass in a stable manner so React Flow / ReactFlow-adjacent pages
  // aren't needed for this list — it's a pure in-memory transform.
  // We read from `query` (the debounced copy) so that every keystroke
  // doesn't re-filter.
  //
  // Also drop any projects the user archived in this session so the grid
  // reflects the action before the parent refetches. Archived rows land
  // in the separate archived view via refreshArchived().
  //
  // IMPORTANT: this useMemo MUST run on every render — it sits above every
  // early-return branch below so hook count stays constant regardless of
  // which branch fires. A prior version had the shelves-view early return
  // ABOVE this hook; when `shelves` arrived asynchronously after first
  // mount (the usual case right after sign-in, because InspiraApp fetches
  // shelves in an effect that resolves post-mount) the hook count would
  // flip from N to N-1 across renders and trigger React error #300
  // ("Rendered fewer hooks than previous render"), throwing every user
  // straight into the ErrorBoundary on sign-in.
  const visible = useMemo(() => {
    const stillActive = locallyArchivedIds.size === 0
      ? projects
      : projects.filter((p) => !locallyArchivedIds.has(p.project_id));
    const needle = query.trim().toLowerCase();
    const filtered = needle
      ? stillActive.filter((p) => p.title.toLowerCase().includes(needle))
      : stillActive.slice();
    const cmp =
      sort === "alpha" ? cmpAlpha : sort === "oldest" ? cmpOldest : cmpRecent;
    filtered.sort(cmp);
    return filtered;
  }, [projects, query, sort, locallyArchivedIds]);

  // Hand off to ShelvesView when the user has at least one shelf AND the
  // caller wired the shelf handlers. Both conditions are load-bearing:
  // - shelves handler gate keeps legacy callers that haven't wired shelves
  //   yet working against the flat grid.
  // - shelves.length > 0 gate keeps the flat grid for users who haven't
  //   created any shelves yet (no surprise UX change for first-time users).
  // We skip this while loading so the skeleton grid still renders.
  const shelvesEnabled =
    !loading &&
    shelves != null &&
    shelves.length > 0 &&
    onCreateNewShelf != null &&
    onRenameShelf != null &&
    onDeleteShelf != null &&
    onMoveProjectToShelf != null;

  // Important: only hand off to ShelvesView when we're in the ACTIVE view
  // mode. The archive + recently-deleted + search-result modes own their
  // own layouts below and must not be short-circuited by shelves; without
  // this guard, clicking "Archived projects →" / "Recently deleted →"
  // from the shelves footer was a no-op.
  if (shelvesEnabled && shelves && viewMode === "active") {
    return (
      <>
        <ShelfErrorBoundary onDismiss={() => setNewShelfOpen(false)}>
          <ShelvesView
            projects={projects}
            shelves={shelves}
            topicsCountByProject={topicsCountByProject}
            decisionsCountByProject={decisionsCountByProject}
            onOpenProject={onOpenProject}
            onCreateNewProject={onCreateNew}
            onCreateNewShelf={onCreateNewShelf!}
            onRenameShelf={onRenameShelf!}
            onDeleteShelf={onDeleteShelf!}
            onMoveProjectToShelf={onMoveProjectToShelf!}
            onRenameProject={onRename}
            // Instead of firing the delete API directly (which skipped
            // confirmation and any UI warning), request the parent's
            // pendingDelete flow — which opens DeleteConfirmDialog.
            // Matches the flat-grid path's contract exactly.
            onDeleteProject={async (projectId: string) => {
              const p = projects.find((x) => x.project_id === projectId);
              if (p) setPendingDelete(p);
            }}
            onArchiveProject={async (projectId: string) => {
              const p = projects.find((x) => x.project_id === projectId);
              if (p) await handleArchive(p);
            }}
            onViewArchived={() => setViewMode("archived")}
            onViewRecentlyDeleted={() => setViewMode("recently_deleted")}
          />
        </ShelfErrorBoundary>
        {/* Dialog lives here too so the shelves path gets the same
            confirm-before-delete experience the flat grid has had. */}
        <DeleteConfirmDialog
          open={pendingDelete !== null}
          itemType="project"
          itemName={pendingDelete?.title ?? ""}
          consequences={t("projects.card.delete_consequences")}
          onClose={() => setPendingDelete(null)}
          onConfirm={async () => {
            if (!pendingDelete) return;
            try {
              const deletedProject = pendingDelete;
              await onDelete(deletedProject.project_id);
              toast.toast({
                message: t("projects.delete.toast_deleted_with_undo"),
                variant: "info",
                durationMs: 6000,
                actionLabel: t("projects.delete.undo"),
                onAction: () => {
                  void handleRestore(deletedProject);
                },
              });
            } catch (err) {
              console.error("[ProjectsListPage] delete failed", err);
            }
            setPendingDelete(null);
          }}
        />
      </>
    );
  }

  // ---- RECENTLY DELETED VIEW --------------------------------------------
  // Soft-delete with grace: deleted projects stay recoverable until the
  // grace window (server-controlled, default 30 days) closes. Each card
  // shows the project title plus an inline "Deleted N days ago, X days
  // remaining" line, with [Restore] and [Delete forever] buttons. Delete
  // forever uses the simplified DeleteConfirmDialog (no typed confirm).
  if (viewingRecentlyDeleted) {
    return (
      <main
        id="main-content"
        tabIndex={-1}
        className="projects-list"
        aria-labelledby="projects-list-title-recently-deleted"
      >
        <div className="projects-list__inner">
          <header className="projects-list__header">
            <div className="projects-list__header-text">
              <h1
                id="projects-list-title-recently-deleted"
                className="projects-list__title"
              >
                {t("projects.recently_deleted.title")}
              </h1>
              <p className="projects-list__subtext">
                {t("projects.recently_deleted.subtext")}
              </p>
            </div>
          </header>

          {recentlyDeletedLoading ? (
            <div className="projects-list__grid" aria-busy="true">
              {Array.from({ length: 3 }).map((_, i) => (
                <ProjectCardSkeleton key={i} />
              ))}
            </div>
          ) : recentlyDeletedProjects.length === 0 ? (
            <div className="projects-list__archived-empty-card">
              <span className="projects-list__archived-empty-eyebrow">
                {t("projects.recently_deleted.empty_eyebrow")}
              </span>
              <h2 className="projects-list__archived-empty-title">
                {t("projects.recently_deleted.empty_heading")}
              </h2>
              <p className="projects-list__archived-empty-body">
                {t("projects.recently_deleted.empty_body")}
              </p>
            </div>
          ) : (
            <div className="projects-list__grid">
              {recentlyDeletedProjects.map((p) => {
                const daysRemaining = p.days_remaining ?? 0;
                const deletedAtMs = Date.parse(p.deleted_at || "") || 0;
                const daysAgoFloat = deletedAtMs
                  ? (Date.now() - deletedAtMs) / 86400000
                  : 0;
                const daysAgo = Math.max(0, Math.floor(daysAgoFloat));
                const deletedLine =
                  daysAgo === 0
                    ? t("projects.recently_deleted.deleted_today")
                    : daysAgo === 1
                    ? t("projects.recently_deleted.deleted_days_ago_one")
                    : t("projects.recently_deleted.deleted_days_ago_many", {
                        count: String(daysAgo),
                      });
                const remainingLine =
                  daysRemaining === 0
                    ? t("projects.recently_deleted.expires_today")
                    : daysRemaining === 1
                    ? t("projects.recently_deleted.days_remaining_one")
                    : t("projects.recently_deleted.days_remaining_many", {
                        count: String(daysRemaining),
                      });
                return (
                  <div
                    key={p.project_id}
                    className="project-card project-card--recently-deleted"
                  >
                    <h3 className="project-card__title" title={p.title}>
                      {p.title}
                    </h3>
                    <p className="project-card__updated">
                      <span>{deletedLine}</span>
                      <span aria-hidden="true"> · </span>
                      <span className="project-card__days-remaining">
                        {remainingLine}
                      </span>
                    </p>
                    <div className="project-card__spacer" />
                    <div className="project-card__rd-actions">
                      <button
                        type="button"
                        className="project-card__rd-restore"
                        onClick={() => handleRestore(p)}
                      >
                        {t("projects.recently_deleted.restore")}
                      </button>
                      <button
                        type="button"
                        className="project-card__rd-purge"
                        onClick={() => setPendingPurge(p)}
                      >
                        {t("projects.recently_deleted.delete_forever")}
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          )}

          <div className="projects-list__archived-footer">
            <button
              type="button"
              className="projects-list__archived-link"
              onClick={() => setViewMode("active")}
            >
              {t("projects.recently_deleted.back_to_active")}
            </button>
          </div>
        </div>
        <DeleteConfirmDialog
          open={pendingPurge !== null}
          itemType="project"
          itemName={pendingPurge?.title ?? ""}
          consequences={t("projects.recently_deleted.delete_consequences")}
          onClose={() => setPendingPurge(null)}
          onConfirm={async () => {
            if (!pendingPurge) return;
            try {
              await handlePurge(pendingPurge);
            } catch {
              // handlePurge already toasted + logged; rethrow for the dialog.
              throw new Error("purge_failed");
            }
            setPendingPurge(null);
          }}
        />
      </main>
    );
  }

  // ---- ARCHIVED VIEW ----------------------------------------------------
  // Separate in-page mode — clicking the footer "Archived projects" link
  // flips this on and fetches the archived list. Each card shows "Restore"
  // instead of "Archive" in its kebab menu; clicking Restore unarchives
  // the project and removes it from this list. No filter/sort toolbar
  // here — the archive is expected to be short, so we just render the
  // flat most-recently-archived-first list the backend returned.
  if (viewingArchived) {
    return (
      <main
        id="main-content"
        tabIndex={-1}
        className="projects-list"
        aria-labelledby="projects-list-title-archived"
      >
        <div className="projects-list__inner">
          <header className="projects-list__header">
            <div className="projects-list__header-text">
              <h1 id="projects-list-title-archived" className="projects-list__title">
                {t("projects.list.title")}
              </h1>
              <p className="projects-list__subtext">
                {t("projects.archive.view_archived")}
              </p>
            </div>
          </header>

          {archivedLoading ? (
            <div className="projects-list__grid" aria-busy="true">
              {Array.from({ length: 3 }).map((_, i) => (
                <ProjectCardSkeleton key={i} />
              ))}
            </div>
          ) : archivedProjects.length === 0 ? (
            <div className="projects-list__archived-empty-card">
              <span className="projects-list__archived-empty-eyebrow">
                {t("projects.archive.empty_eyebrow")}
              </span>
              <h2 className="projects-list__archived-empty-title">
                {t("projects.archive.empty_heading")}
              </h2>
              <p className="projects-list__archived-empty-body">
                {t("projects.archive.empty_body")}
              </p>
              <button
                type="button"
                className="projects-list__archived-empty-cta"
                onClick={() => setViewMode("active")}
              >
                {t("projects.archive.back_to_active")}
              </button>
            </div>
          ) : (
            <div className="projects-list__grid">
              {archivedProjects.map((p) => (
                <ProjectCard
                  key={p.project_id}
                  project={p}
                  topicsCount={topicsCountByProject?.get(p.project_id)}
                  decisionsCount={decisionsCountByProject?.get(p.project_id)}
                  onOpen={() => onOpenProject(p.project_id)}
                  onRename={(next) => onRename(p.project_id, next)}
                  onDelete={async () => {
                    setPendingDelete(p);
                  }}
                  archived
                  onUnarchive={() => handleUnarchive(p)}
                />
              ))}
            </div>
          )}

          <div className="projects-list__archived-footer">
            <button
              type="button"
              className="projects-list__archived-link"
              onClick={() => setViewMode("active")}
            >
              {t("projects.archive.back_to_active")}
            </button>
            <button
              type="button"
              className="projects-list__archived-link"
              onClick={() => setViewMode("recently_deleted")}
            >
              {t("projects.recently_deleted.view")}
            </button>
          </div>
        </div>
        <DeleteConfirmDialog
          open={pendingDelete !== null}
          itemType="project"
          itemName={pendingDelete?.title ?? ""}
          consequences={t("projects.card.delete_consequences")}
          onClose={() => setPendingDelete(null)}
          onConfirm={async () => {
            if (!pendingDelete) return;
            try {
              await onDelete(pendingDelete.project_id);
              // Also drop it from the local archived list.
              setArchivedProjects((prev) =>
                prev.filter(
                  (p) => p.project_id !== pendingDelete.project_id,
                ),
              );
            } catch (err) {
              console.error("[ProjectsListPage] delete failed", err);
              throw err;
            }
            setPendingDelete(null);
          }}
        />
      </main>
    );
  }

  // ---- LOADING ----------------------------------------------------------
  if (loading) {
    return (
      <main
        id="main-content"
        tabIndex={-1}
        className="projects-list"
        aria-labelledby="projects-list-title-loading"
      >
        <div className="projects-list__inner">
          <header className="projects-list__header">
            <div className="projects-list__header-text">
              <h1 id="projects-list-title-loading" className="projects-list__title">{t("projects.list.title")}</h1>
              <p className="projects-list__subtext">
                {t("projects.list.loading_subtext")}
              </p>
            </div>
          </header>
          <div className="projects-list__grid" aria-busy="true">
            {Array.from({ length: 6 }).map((_, i) => (
              <ProjectCardSkeleton key={i} />
            ))}
          </div>
        </div>
      </main>
    );
  }

  // ---- EMPTY (zero projects) -------------------------------------------
  // "Zero active projects" — which can happen either because the user is
  // a brand-new signup OR because they archived every project they had.
  // We leave the empty CTA card as the primary call-to-action and add the
  // footer "Archived projects" link below it so the archive doesn't
  // become unreachable from this state.
  if (projects.length === 0) {
    return (
      <main
        id="main-content"
        tabIndex={-1}
        className="projects-list"
        aria-labelledby="projects-list-title-empty"
      >
        <div className="projects-list__inner">
          <header className="projects-list__header">
            <div className="projects-list__header-text">
              <h1 id="projects-list-title-empty" className="projects-list__title">{t("projects.list.title")}</h1>
              <p className="projects-list__subtext">
                {t("projects.list.empty_subtext")}
              </p>
            </div>
          </header>
          <div className="projects-list__empty">
            <div className="projects-list__empty-card">
              <span className="projects-list__empty-eyebrow">{t("projects.list.empty_eyebrow")}</span>
              <h2 className="projects-list__empty-title">{t("projects.list.empty_heading")}</h2>
              <p className="projects-list__empty-body">
                {t("projects.list.empty_body")}
              </p>
              <button
                type="button"
                className="projects-list__empty-cta"
                onClick={onCreateNew}
              >
                {t("projects.list.empty_cta")}
              </button>
            </div>
          </div>
          <div className="projects-list__archived-footer">
            <button
              type="button"
              className="projects-list__archived-link"
              onClick={() => setViewMode("archived")}
            >
              {t("projects.archive.view_archived")}
            </button>
            <button
              type="button"
              className="projects-list__archived-link"
              onClick={() => setViewMode("recently_deleted")}
            >
              {t("projects.recently_deleted.view")}
            </button>
          </div>
        </div>
      </main>
    );
  }

  // ---- POPULATED --------------------------------------------------------
  const total = projects.length;
  const lastEdit = describeLastEdit(projects);
  const countLabel = total === 1
    ? t("projects.list.count_one")
    : t("projects.list.count_many", { count: String(total) });
  const visibleLabel = (() => {
    if (query.trim() && visible.length !== total) {
      return visible.length === 1
        ? t("projects.list.match_one")
        : t("projects.list.match_many", { count: String(visible.length) });
    }
    return countLabel;
  })();

  return (
    <main
      id="main-content"
      tabIndex={-1}
      className="projects-list"
      aria-labelledby="projects-list-title-main"
    >
      <div className="projects-list__inner">
        <header className="projects-list__header">
          <div className="projects-list__header-text">
            <h1 id="projects-list-title-main" className="projects-list__title">{t("projects.list.title")}</h1>
            <p className="projects-list__subtext">
              {t("projects.list.subtext_last_edit", { count: countLabel, when: lastEdit })}
            </p>
          </div>
          <div className="projects-list__header-actions">
            {onCreateNewShelf ? (
              <button
                type="button"
                className="projects-list__new-shelf-btn"
                onClick={() => setNewShelfOpen(true)}
                title={t("shelves.view.new_shelf")}
              >
                {t("shelves.view.new_shelf")}
              </button>
            ) : null}
            <button
              type="button"
              className="projects-list__new-btn"
              onClick={onCreateNew}
            >
              {t("projects.list.new_project")}
            </button>
          </div>
        </header>
        {onCreateNewShelf ? (
          /* Local ErrorBoundary: a crash inside the NewShelfDialog subtree
             would otherwise escape to the top-level boundary and leave the
             user stuck on a full-screen error page. Shelf creation is a
             secondary affordance — if its render throws we just drop the
             dialog silently (plus a toast) so the main grid stays usable. */
          <ErrorBoundary
            fallback={({ reset }) => {
              // Close the dialog and surface a toast once. Reset clears the
              // boundary state so the user can try again after the next
              // setNewShelfOpen(true).
              setTimeout(() => {
                setNewShelfOpen(false);
                reset();
                toast.error(t("shelves.create_error_toast"));
              }, 0);
              return null;
            }}
            onError={(err) => {
              console.error("[ProjectsListPage] shelf dialog crashed", err);
            }}
          >
            <NewShelfDialog
              open={newShelfOpen}
              onClose={() => setNewShelfOpen(false)}
              onSubmit={async (name) => {
                try {
                  await onCreateNewShelf(name);
                  setNewShelfOpen(false);
                } catch (err) {
                  // NewShelfDialog surfaces the error message via setError
                  // when we throw; rethrow so it can display inline. Also
                  // log + toast so a silent backend failure is traceable.
                  console.error(
                    "[ProjectsListPage] shelf creation failed",
                    err,
                  );
                  toast.error(t("shelves.create_error_toast"));
                  throw err;
                }
              }}
            />
          </ErrorBoundary>
        ) : null}

        {suggestions.length > 0 && onSuggestStart ? (
          <section
            className="projects-list__suggestions"
            aria-label={t("projects.suggestions.eyebrow")}
          >
            <span className="projects-list__suggestions-eyebrow">
              {t("projects.suggestions.eyebrow")}
            </span>
            <div className="projects-list__suggestions-row">
              {suggestions.map((idea, idx) => (
                <button
                  key={`${idx}-${idea.slice(0, 32)}`}
                  type="button"
                  className="projects-list__suggestion-card"
                  aria-label={t("projects.suggestions.start_aria_detailed", { idea: idea.slice(0, 60) })}
                  onClick={() => onSuggestStart(idea)}
                >
                  <span className="projects-list__suggestion-text">{idea}</span>
                  <span
                    className="projects-list__suggestion-cta"
                    aria-hidden="true"
                  >
                    {t("projects.suggestions.start_hover")}
                  </span>
                </button>
              ))}
            </div>
          </section>
        ) : null}

        <div className="projects-list__toolbar" role="toolbar">
          <input
            ref={searchInputRef}
            type="search"
            className="projects-list__search"
            placeholder={t("projects.search.placeholder")}
            aria-label={t("projects.search.aria")}
            value={rawQuery}
            onChange={(e) => setRawQuery(e.target.value)}
            autoComplete="off"
            spellCheck={false}
          />
          <div className="projects-list__sort-wrap">
            <select
              className="projects-list__sort"
              aria-label={t("projects.list.sort_aria")}
              value={sort}
              onChange={(e) => setSort(e.target.value as SortKey)}
            >
              {SORT_VALUES.map((value) => (
                <option key={value} value={value}>
                  {t(`projects.list.sort_${value}`)}
                </option>
              ))}
            </select>
            <span className="projects-list__sort-caret" aria-hidden="true">
              {"\u25BE"}
            </span>
          </div>
          <span className="projects-list__count" aria-live="polite">
            {visibleLabel}
          </span>
        </div>

        <div className="projects-list__grid">
          {visible.length === 0 ? (
            <div className="projects-list__no-matches" role="status">
              <p className="projects-list__no-matches-line">
                {t("projects.search.no_matches", { query: query.trim() })}
              </p>
              <p className="projects-list__no-matches-hint">
                {t("projects.search.no_matches_hint")}
              </p>
              <button
                type="button"
                className="projects-list__clear-search"
                onClick={() => {
                  setRawQuery("");
                  setQuery("");
                  searchInputRef.current?.focus();
                }}
              >
                {t("projects.search.clear")}
              </button>
            </div>
          ) : (
            visible.map((p) => (
              <ProjectCard
                key={p.project_id}
                project={p}
                topicsCount={topicsCountByProject?.get(p.project_id)}
                decisionsCount={decisionsCountByProject?.get(p.project_id)}
                onOpen={() => onOpenProject(p.project_id)}
                onRename={(next) => onRename(p.project_id, next)}
                onDelete={async () => {
                  // Don't actually delete yet — just queue the dialog.
                  // The real call happens on confirm below.
                  setPendingDelete(p);
                }}
                onArchive={() => handleArchive(p)}
                shelves={shelves}
                onMoveToShelf={
                  onMoveProjectToShelf
                    ? (shelfIdOrNull) =>
                        onMoveProjectToShelf(p.project_id, shelfIdOrNull)
                    : undefined
                }
              />
            ))
          )}
        </div>

        <div className="projects-list__archived-footer">
          <button
            type="button"
            className="projects-list__archived-link"
            onClick={() => setViewMode("archived")}
          >
            {t("projects.archive.view_archived")}
          </button>
          <button
            type="button"
            className="projects-list__archived-link"
            onClick={() => setViewMode("recently_deleted")}
          >
            {t("projects.recently_deleted.view")}
          </button>
        </div>
      </div>
      <DeleteConfirmDialog
        open={pendingDelete !== null}
        itemType="project"
        itemName={pendingDelete?.title ?? ""}
        consequences={t("projects.card.delete_consequences")}
        onClose={() => setPendingDelete(null)}
        onConfirm={async () => {
          if (!pendingDelete) return;
          try {
            const deletedProject = pendingDelete;
            await onDelete(deletedProject.project_id);
            // Toast Undo: a 6-second window during which the user can
            // restore the project they just deleted. Behind the scenes
            // soft-delete + restore make this a real reversal — there is
            // no race with the parent refetch because both paths go
            // through the same backend rows.
            toast.toast({
              message: t("projects.delete.toast_deleted_with_undo"),
              variant: "info",
              durationMs: 6000,
              actionLabel: t("projects.delete.undo"),
              onAction: () => {
                void handleRestore(deletedProject);
              },
            });
          } catch (err) {
            console.error("[ProjectsListPage] delete failed", err);
            throw err; // let the dialog surface a retry-able error state
          }
          setPendingDelete(null);
        }}
      />
      <Coachmark
        active={homeCoachActive}
        storageKey="inspira_onboarded_homepage"
        steps={HOME_STEPS}
        onDone={() => setHomeCoachActive(false)}
      />
    </main>
  );
}
