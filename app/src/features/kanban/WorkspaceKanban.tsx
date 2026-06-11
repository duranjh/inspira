import {
  DndContext,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
  type Announcements,
  type DragEndEvent,
  type DragStartEvent,
} from "@dnd-kit/core";
import { sortableKeyboardCoordinates } from "@dnd-kit/sortable";
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import { toast } from "../../components/ToastProvider";
import { WorkspaceTour } from "../onboarding/WorkspaceTour";
import { api } from "../inspira/api";
import type { V2Project } from "../inspira/api";
import type { KanbanColumn as ColumnId } from "../inspira/api";
import { KanbanColumnView } from "./KanbanColumn";
import { ManualOverrideDialog } from "./ManualOverrideDialog";
import {
  COLUMN_TARGET_STATE,
  useKanbanData,
  type Board,
} from "./useKanbanData";
import "./WorkspaceKanban.css";

export type WorkspaceKanbanProps = {
  /** Resolved from the user's ``default_workspace_id`` (W0). When
   * absent the parent renders ``ProjectsListPage`` instead — never
   * pass an empty string here. */
  workspaceId: string;
};

/** Static column metadata — copy ported from
 * /tmp/inspira-v12/kanban-data.jsx, rebound to the v4 state machine
 * mapping in useKanbanData.columnFor. The order is the visual
 * left-to-right order. */
const COLUMNS: ReadonlyArray<{
  id: ColumnId;
  name: string;
  subtitle: string;
  emptyMsg: string;
  emptyCta?: string;
  chipColor: "sage" | "gold" | "rust" | "sage-filled" | "muted";
}> = [
  {
    id: "queue",
    name: "Queue",
    subtitle: "Issues that haven't been picked up yet.",
    emptyMsg:
      "Inspira is quiet. Connect a feedback channel and Inspira will start clustering.",
    emptyCta: "Connect feedback →",
    chipColor: "sage",
  },
  {
    id: "in_progress",
    name: "In Progress",
    subtitle: "AI is drafting, or canvas is in Draft.",
    emptyMsg: "Nothing in progress. New work shows up here as the AI picks it up.",
    chipColor: "gold",
  },
  {
    id: "in_review",
    name: "In Review",
    subtitle: "Canvas sent for human review.",
    emptyMsg: "Nothing in review yet. Approve from a Draft to move it here.",
    chipColor: "rust",
  },
  {
    id: "approved",
    name: "Approved",
    subtitle: "Approved. Ready to push to GitHub.",
    emptyMsg: "Nothing approved yet.",
    chipColor: "sage-filled",
  },
  {
    id: "shipped",
    name: "Shipped",
    subtitle: "Pushed to GitHub.",
    emptyMsg: "Nothing shipped yet.",
    chipColor: "muted",
  },
];

/**
 * Workspace Home — the v4 default post-login surface for workspace
 * accounts. Replaces the legacy ProjectsListPage when the user has
 * a ``default_workspace_id``; the parent (InspiraApp) does the
 * branch in Wave 6.
 *
 * Wave 4 ships render-only: 5 columns, ROI-sorted from server, no
 * drag, no swim-lane toggle yet (the toggle button stays for visual
 * parity but does nothing — Wave 5 wires it).
 */
export function WorkspaceKanban(props: WorkspaceKanbanProps) {
  const { workspaceId } = props;
  const navigate = useNavigate();
  const {
    board, loading, error, refetch, mutateState, mutatePriority,
    bulkMutatePriority,
  } = useKanbanData(workspaceId);
  const [pendingOverride, setPendingOverride] =
    useState<PendingOverride | null>(null);
  // Tag filter (founder direction 2026-05-04). Filters the Kanban
  // to a single dominant_category — Bug / Complaint / Feature.
  // ``null`` = show all eligible categories (default).
  const [tagFilter, setTagFilter] = useState<string | null>(null);
  // Bulk selection — checkboxes on every card opt into the bulk
  // action bar. Reset on successful delete.
  const [checked, setChecked] = useState<Set<string>>(new Set());
  const [deleting, setDeleting] = useState(false);

  function toggleChecked(projectId: string) {
    setChecked((prev) => {
      const next = new Set(prev);
      if (next.has(projectId)) {
        next.delete(projectId);
      } else {
        next.add(projectId);
      }
      return next;
    });
  }

  async function handleBulkDelete() {
    if (checked.size === 0 || deleting) return;
    const ids = Array.from(checked);
    setDeleting(true);
    try {
      const result = await api.bulkDeleteV2Projects(ids);
      setChecked(new Set());
      toast.success(
        `Deleted ${result.deleted} issue${result.deleted === 1 ? "" : "s"}.`,
      );
      void refetch();
    } catch (exc) {
      toast.error(
        exc instanceof Error
          ? `Delete failed: ${exc.message}`
          : "Delete failed.",
      );
    } finally {
      setDeleting(false);
    }
  }

  // Autonomous flow (founder direction 2026-05-04): Inspira should
  // automatically pick the top 3 in-queue auto-promoted Drafts and
  // spawn the orchestrator on each — no manual click required. The
  // ``In queue`` column is already ROI-sorted by the server, so
  // taking the top 3 honors prioritization. ``attemptedRef`` records
  // every project_id we've POSTed start-canvas for in this workspace
  // session, persisted in sessionStorage so tab-switch remounts of
  // WorkspaceKanban don't re-fire the same spawn. The cache NEVER
  // removes entries (even on failure) so a 5xx storm can't burn the
  // autospawn budget in a polling loop — partners reload the tab to
  // retry a stuck spawn.
  const sessionKey = `inspira_autospawn_${workspaceId}`;
  function readAttempted(): Set<string> {
    try {
      const raw = sessionStorage.getItem(sessionKey);
      return new Set(raw ? (JSON.parse(raw) as string[]) : []);
    } catch {
      return new Set();
    }
  }
  function writeAttempted(set: Set<string>): void {
    try {
      sessionStorage.setItem(sessionKey, JSON.stringify([...set]));
    } catch {
      /* storage quota / disabled — fall back to in-mount behaviour */
    }
  }
  useEffect(() => {
    if (loading) return;
    const attempted = readAttempted();
    const candidates = board.queue
      .filter((p) => {
        if (attempted.has(p.project_id)) return false;
        const md = (p.metadata ?? {}) as Record<string, unknown>;
        if (md["orchestrator_run_id"]) return false;
        if (md["auto_promoted"] !== true) return false;
        return true;
      })
      .slice(0, 3);
    if (candidates.length === 0) return;
    candidates.forEach((p) => {
      attempted.add(p.project_id);
      void api.startProjectCanvas(p.project_id).catch(() => {
        // Intentionally NO removal from the cache — the catch would
        // reopen the project for re-firing on the next board refresh,
        // and a sustained 5xx (e.g., orchestrator deploy restart)
        // would loop dozens of POSTs per page load.
      });
    });
    writeAttempted(attempted);
    // Fire a refetch shortly so the freshly-spawned orchestrator
    // runs land their ``ai_review_in_progress`` flag visibly in the
    // Kanban (transitions card to AI Thinking column).
    const timer = window.setTimeout(() => refetch(), 1500);
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loading, board.queue, refetch, workspaceId]);

  // Continuous queue worker (founder direction 2026-05-04): poll
  // every 30s so cards transition through the columns visibly
  // (queue → AI thinking → Review needed) without the user having
  // to reload, AND so newly-added cards (paste-feedback batch
  // mid-session) get auto-spawned without a manual refresh. The
  // attemptedRef cache above means each project_id is start-canvas'd
  // at most once per mount, so the poll itself never re-fires the
  // same spawn — it just keeps board.queue fresh so the auto-spawn
  // effect sees new candidates as they emerge.
  useEffect(() => {
    if (loading) return;
    const interval = window.setInterval(() => refetch(), 30_000);
    return () => window.clearInterval(interval);
  }, [loading, refetch]);

  // Apply tag filter to every column. The board itself stays the
  // server's source of truth (so server-side mutations like Refresh
  // re-pull the full picture); we just narrow what we render.
  const filteredBoard: Board = useMemo(() => {
    if (!tagFilter) return board;
    const match = (p: V2Project) => {
      const md = (p.metadata ?? {}) as Record<string, unknown>;
      const cat = md["dominant_category"];
      return typeof cat === "string" && cat.toLowerCase() === tagFilter;
    };
    return {
      queue: board.queue.filter(match),
      in_progress: board.in_progress.filter(match),
      in_review: board.in_review.filter(match),
      approved: board.approved.filter(match),
      shipped: board.shipped.filter(match),
    };
  }, [board, tagFilter]);

  // Pointer sensor with a small activation threshold so a click on
  // the card body still registers as a click — only a true drag
  // initiates the @dnd-kit lifecycle. KeyboardSensor (closes #132)
  // gives Tab/Space/Arrow/Esc operability on the board for keyboard
  // and screen-reader users — sortableKeyboardCoordinates knows how
  // to navigate between SortableContext siblings.
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );

  // Friendly screen-reader narration. dnd-kit auto-renders an
  // aria-live region inside DndContext; these callbacks supply the
  // copy. Falls back to dnd-kit's defaults when a callback returns
  // undefined.
  const a11y = useMemo<{ announcements: Announcements }>(() => {
    const cardLabel = (id: string | number) => {
      const card =
        board.queue.find((p) => p.project_id === id) ??
        board.in_progress.find((p) => p.project_id === id) ??
        board.in_review.find((p) => p.project_id === id) ??
        board.approved.find((p) => p.project_id === id) ??
        board.shipped.find((p) => p.project_id === id);
      return card?.title?.trim() || `card ${id}`;
    };
    const colLabel = (over: { id: string | number } | null): string => {
      if (!over) return "an empty area";
      const overId = String(over.id);
      const col = overId.startsWith("column:")
        ? (overId.slice("column:".length) as ColumnId)
        : findColumnContainingCard(board, overId);
      if (!col) return "an empty area";
      return COLUMN_LABEL[col] ?? col;
    };
    return {
      announcements: {
        onDragStart: ({ active }) =>
          `Picked up card '${cardLabel(active.id)}'. Use arrow keys to move, space to drop, escape to cancel.`,
        onDragOver: ({ active, over }) =>
          `Card '${cardLabel(active.id)}' is over ${colLabel(over)}.`,
        onDragEnd: ({ active, over }) =>
          over
            ? `Dropped card '${cardLabel(active.id)}' on ${colLabel(over)}.`
            : `Cancelled moving card '${cardLabel(active.id)}'.`,
        onDragCancel: ({ active }) =>
          `Cancelled moving card '${cardLabel(active.id)}'.`,
      },
    };
  }, [board]);

  function onDragEnd(event: DragEndEvent) {
    const { active, over } = event;
    if (!over) return;
    const fromColumn = (active.data.current?.column as ColumnId | undefined) ??
      findColumnContainingCard(board, active.id as string);
    if (!fromColumn) return;
    const toColumn = resolveDropColumn(board, over.id as string);
    if (!toColumn) return;
    if (fromColumn === toColumn) {
      // Same-column reorder. Compute the new index in the post-move
      // list and stamp ``priority_order`` so the column ranks the
      // dragged card at that position.
      if (active.id === over.id) return;
      const list = board[fromColumn];
      const fromIdx = list.findIndex((p) => p.project_id === active.id);
      let toIdx = list.findIndex((p) => p.project_id === over.id);
      if (fromIdx === -1 || toIdx === -1) return;
      // arrayMove-style adjustment: when moving down, the post-move
      // index is the over.id's CURRENT index; when moving up, it's
      // toIdx (the over card stays put visually).
      if (fromIdx < toIdx) {
        // Moving down — the dragged card lands AT toIdx after the splice
      } else {
        toIdx = Math.max(0, toIdx);
      }
      // Build the post-move id order (splice the dragged card out of
      // its current slot and re-insert at toIdx).
      const postMoveIds = list.map((p) => p.project_id);
      postMoveIds.splice(fromIdx, 1);
      postMoveIds.splice(toIdx, 0, active.id as string);
      // If ANY card in the column has a null ``priority_order``, a
      // single-card stamp would put the dragged card above all the
      // nulls (priority_order ASC NULLS LAST), so the drop visually
      // "always lands at the top" — which is exactly the bug
      // reported. Bootstrap the whole column instead.
      const needsBootstrap = list.some(
        (p) => p.priority_order === null || p.priority_order === undefined,
      );
      if (needsBootstrap) {
        void bulkMutatePriority({
          columnId: fromColumn,
          orderedProjectIds: postMoveIds,
        }).then((ok) => {
          if (!ok) toast.error("Couldn't reorder — refreshing.");
        });
        return;
      }
      // Fast path: every card already has a stamped priority — a
      // single-card mutation is enough.
      const newPriority = (toIdx + 1) * 1024;
      void mutatePriority({
        projectId: active.id as string,
        columnId: fromColumn,
        priorityOrder: newPriority,
      }).then((ok) => {
        if (!ok) {
          toast.error("Couldn't reorder — refreshing.");
        }
      });
      return;
    }
    // Cross-column drop.
    const project = board[fromColumn].find(
      (p) => p.project_id === active.id,
    );
    if (!project) return;
    // The thinking column refuses other drops; our KanbanColumn
    // already disables the droppable, so this branch shouldn't fire
    // for it — but guard anyway for safety.
    if (!COLUMN_TARGET_STATE[toColumn]) {
      toast.warning(`The "${toColumn}" column is system-managed.`);
      return;
    }
    // "Has the project been worked on by AI yet?" Tracked via the
    // orchestrator_run_id stamped into metadata when the canvas spawns
    // (issue #173 references). Used below to gate which drag flows
    // pop the override dialog vs silently move + auto-spawn.
    const md = (project.metadata ?? {}) as Record<string, unknown>;
    const hasCanvas =
      typeof md["orchestrator_run_id"] === "string" &&
      md["orchestrator_run_id"] !== "";
    // Founder direction 2026-05-05: the override dialog ONLY fires
    // for cross-column moves *into* In Progress AND only when the
    // project already has a canvas. Other paths skip the dialog:
    //   - {anything} → {non-In-Progress}: silent state change.
    //   - Queue → In Progress + no canvas yet: auto-spawn (existing).
    if (toColumn !== "in_progress") {
      // Silent move — no dialog, no friction. Audit row still
      // captures actor_user_id from the auth context. The bulk-drag
      // path also flows through here when the destination isn't
      // In Progress, but it loops mutateState per checked card so
      // we just inline the same fan-out shape.
      const targets: V2Project[] = (() => {
        if (!checked.has(project.project_id) || checked.size <= 1) {
          return [project];
        }
        const extras: V2Project[] = [];
        for (const id of checked) {
          if (id === project.project_id) continue;
          const fromCol = findColumnContainingCard(board, id);
          if (!fromCol) continue;
          const found = board[fromCol].find((p) => p.project_id === id);
          if (found) extras.push(found);
        }
        return [project, ...extras];
      })();
      void (async () => {
        for (const card of targets) {
          const cardFrom = findColumnContainingCard(board, card.project_id);
          if (!cardFrom || cardFrom === toColumn) continue;
          await mutateState({
            projectId: card.project_id,
            fromColumn: cardFrom,
            toColumn,
            note: "",
          }).catch(() => undefined);
        }
        if (targets.length > 1) setChecked(new Set());
      })();
      return;
    }
    // toColumn === "in_progress"
    // Special case: Queue → In Progress for a project that doesn't
    // yet have a canvas → silently auto-spawn. This is the canonical
    // "let the AI start working on this" gesture; popping a dialog
    // here would be friction. The backend enforces a per-tier
    // concurrent sub-agent cap and returns 429 when full.
    if (fromColumn === "queue" && !hasCanvas) {
      void api
        .startProjectCanvas(project.project_id)
        .then(() => {
          toast.success(`Spawning AI sub-agent for '${project.title}'.`);
          void refetch();
        })
        .catch((err) => {
          const detail =
            (err && (err as { detail?: { message?: string } }).detail) ||
            null;
          if (detail && detail.message) {
            toast.error(detail.message);
          } else if (err instanceof Error) {
            toast.error(err.message);
          } else {
            toast.error("Couldn't start the sub-agent.");
          }
        });
      return;
    }
    // Anything else dropping into In Progress → pop the override
    // dialog with the rerun toggle. Default toggle position depends
    // on the source: dragging back from Queue = "I want this re-done"
    // (toggle ON); dragging back from In Review/Approved/Shipped =
    // "I'm just moving this for organization" (toggle OFF). Bulk
    // drag bundles the checked set as before.
    let batch: V2Project[] = [project];
    if (checked.has(project.project_id) && checked.size > 1) {
      const extras: V2Project[] = [];
      for (const id of checked) {
        if (id === project.project_id) continue;
        const fromCol = findColumnContainingCard(board, id);
        if (!fromCol) continue;
        const found = board[fromCol].find((p) => p.project_id === id);
        if (found) extras.push(found);
      }
      batch = [project, ...extras];
    }
    setPendingOverride({
      project,
      fromColumn,
      toColumn,
      batch,
      rerunDefault: fromColumn === "queue",
    });
  }

  function onDragStart(_event: DragStartEvent) {
    // Reserved for future feedback (e.g. row preview / haptic). The
    // sortable hook handles the visual drag indicator on its own.
  }

  function confirmOverride(note: string, rerun: boolean) {
    if (!pendingOverride) return;
    const { project, toColumn, batch } = pendingOverride;
    setPendingOverride(null);
    // Helper: when rerun is on, kick the orchestrator with the partner's
    // correction note as context. The BE reads metadata.correction_note
    // and threads it into the sub-agent prompt. Best-effort — a failed
    // spawn doesn't roll back the state move (partner can re-trigger
    // via the canvas Re-run button).
    const maybeRerun = (projectId: string) => {
      if (!rerun) return;
      void api
        .startProjectCanvas(projectId, { correctionNote: note })
        .catch((err) => {
          console.warn("[Kanban] rerun spawn failed", err);
        });
    };
    if (batch.length <= 1) {
      // Single-card path.
      const fromColumn = pendingOverride.fromColumn;
      void mutateState({
        projectId: project.project_id,
        fromColumn,
        toColumn,
        note,
      }).then((ok) => {
        if (ok) {
          maybeRerun(project.project_id);
          toast.success(
            rerun
              ? `Moved '${project.title}' — Inspira is rerunning.`
              : `Moved '${project.title}'.`,
          );
        } else {
          toast.error("Move failed — restored to original column.");
        }
      });
      return;
    }
    // Bulk path — fan out one mutateState per card. We resolve each
    // card's current column live (some may already be in toColumn,
    // in which case we skip them rather than no-op the BE). Successes
    // and failures are tallied for a single summary toast; the checked
    // set is cleared on completion regardless so the partner doesn't
    // accidentally re-bulk-drag a stale selection.
    void (async () => {
      let succeeded = 0;
      let failed = 0;
      let skipped = 0;
      for (const card of batch) {
        const cardFrom = findColumnContainingCard(board, card.project_id);
        if (!cardFrom) {
          skipped += 1;
          continue;
        }
        if (cardFrom === toColumn) {
          skipped += 1;
          continue;
        }
        try {
          const ok = await mutateState({
            projectId: card.project_id,
            fromColumn: cardFrom,
            toColumn,
            note,
          });
          if (ok) {
            succeeded += 1;
            maybeRerun(card.project_id);
          } else failed += 1;
        } catch {
          failed += 1;
        }
      }
      setChecked(new Set());
      if (failed === 0) {
        toast.success(
          `Moved ${succeeded} issue${succeeded === 1 ? "" : "s"} to ${
            COLUMN_LABEL[toColumn] ?? toColumn
          }.`,
        );
      } else if (succeeded === 0) {
        toast.error(
          `Couldn't move ${failed} issue${failed === 1 ? "" : "s"} — refreshing.`,
        );
      } else {
        toast.warning(
          `Moved ${succeeded}; ${failed} failed${
            skipped > 0 ? ` (${skipped} already in column)` : ""
          }.`,
        );
      }
      void refetch();
    })();
  }

  function cancelOverride() {
    setPendingOverride(null);
  }

  if (error) {
    return (
      <div className="kb-error" role="alert">
        <p>{error}</p>
        <button
          type="button"
          className="kb-topbar__rerun"
          onClick={refetch}
        >
          Retry
        </button>
      </div>
    );
  }

  return (
    <div className="kb-page">
      <WorkspaceTour />
      <div className="kb-header">
        <div>
          <h1>What Inspira is working on</h1>
          <p className="kb-header__sub">
            Inspira pulls feedback from your channels, prioritizes by ROI,
            scopes autonomously, ships for review.
          </p>
        </div>
        <div className="kb-header__actions">
          <button
            type="button"
            className="kb-topbar__rerun"
            onClick={refetch}
            aria-label="Refresh"
          >
            Refresh
          </button>
          <button
            type="button"
            className="kb-header__group-toggle"
            disabled
            title="Swim lanes by shelf — coming in W5"
          >
            Group by shelf ▾
          </button>
        </div>
      </div>
      {checked.size > 0 ? (
        <div className="kb-bulkbar" role="toolbar" aria-label="Bulk actions">
          <span>
            {checked.size} selected
          </span>
          <button
            type="button"
            className="kb-bulkbar__clear"
            onClick={() => setChecked(new Set())}
          >
            Clear
          </button>
          <button
            type="button"
            className="kb-bulkbar__delete"
            onClick={handleBulkDelete}
            disabled={deleting}
          >
            {deleting
              ? "Deleting…"
              : `Delete ${checked.size} issue${checked.size === 1 ? "" : "s"}`}
          </button>
        </div>
      ) : null}
      <div className="kb-tag-filter" role="toolbar" aria-label="Filter by tag">
        <span className="kb-tag-filter__label">Filter</span>
        {([
          { id: null as string | null, label: "All" },
          { id: "bug", label: "Bug" },
          { id: "complaint", label: "Complaint" },
          { id: "feature", label: "Feature" },
        ]).map((p) => {
          const active = tagFilter === p.id;
          return (
            <button
              key={p.label}
              type="button"
              className={
                "kb-tag-pill" + (active ? " kb-tag-pill--active" : "")
              }
              onClick={() => setTagFilter(p.id)}
              aria-pressed={active}
            >
              {p.label}
            </button>
          );
        })}
      </div>

      <DndContext
        sensors={sensors}
        accessibility={a11y}
        onDragStart={onDragStart}
        onDragEnd={onDragEnd}
      >
        <div
          className="kb-board"
          aria-busy={loading ? "true" : "false"}
        >
          {COLUMNS.map((col) => (
            <KanbanColumnView
              key={col.id}
              id={col.id}
              name={col.name}
              subtitle={col.subtitle}
              emptyMsg={col.emptyMsg}
              emptyCta={col.emptyCta}
              onEmptyCtaClick={
                col.emptyCta ? () => navigate("/connectors") : undefined
              }
              chipColor={col.chipColor}
              cards={filteredBoard[col.id]}
              checkedIds={checked}
              onToggleChecked={toggleChecked}
              loading={loading}
            />
          ))}
        </div>
      </DndContext>
      {pendingOverride && (
        <ManualOverrideDialog
          projectTitle={pendingOverride.project.title}
          fromColumn={pendingOverride.fromColumn}
          toColumn={pendingOverride.toColumn}
          batchSize={pendingOverride.batch.length}
          showRerunToggle
          rerunDefault={pendingOverride.rerunDefault}
          onConfirm={confirmOverride}
          onCancel={cancelOverride}
        />
      )}
    </div>
  );
}

const COLUMN_LABEL: Record<ColumnId, string> = {
  queue: "Queue",
  in_progress: "In Progress",
  in_review: "In Review",
  approved: "Approved",
  shipped: "Shipped",
};

type PendingOverride = {
  /** Anchor card the user actually grabbed — drives the dialog
   *  title, the per-card "from column" lookup, and the spinner. */
  project: V2Project;
  /** Source column of the anchor card. */
  fromColumn: ColumnId;
  /** Destination column for every card in this batch. Today only
   *  ever "in_progress" — the dialog only fires for moves into
   *  In Progress per founder direction 2026-05-05. */
  toColumn: ColumnId;
  /** All cards being moved in this drop. Single-card drag → just
   *  [project]. Bulk drag (anchor was part of the checked set) →
   *  the full set, with the anchor first. */
  batch: V2Project[];
  /** Initial state of the dialog's "Have Inspira rerun" toggle.
   *  True when the natural intent of the drag is "redo this"
   *  (Queue → In Progress with an existing canvas); false when
   *  the partner is moving for organization (In Review/Approved
   *  → In Progress) and the existing canvas should stay intact. */
  rerunDefault: boolean;
};

function findColumnContainingCard(
  board: Board, cardId: string,
): ColumnId | null {
  for (const col of (
    ["queue", "in_progress", "in_review", "approved", "shipped"] as ColumnId[]
  )) {
    if (board[col].some((p) => p.project_id === cardId)) return col;
  }
  return null;
}

/**
 * The ``over`` id from @dnd-kit/sortable can be either a card id (when
 * dropping on a sibling) or a column droppable id ``column:<id>``
 * (when dropping on the bare container — important for empty
 * columns). Resolve to a single ColumnId for the dispatcher.
 */
function resolveDropColumn(
  board: Board, overId: string,
): ColumnId | null {
  if (overId.startsWith("column:")) {
    const id = overId.slice("column:".length) as ColumnId;
    return id;
  }
  return findColumnContainingCard(board, overId);
}
