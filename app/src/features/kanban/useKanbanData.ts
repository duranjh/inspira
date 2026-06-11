import { useCallback, useEffect, useState } from "react";

import {
  api,
  type KanbanColumn,
  type ProjectState,
  type V2Project,
} from "../inspira/api";

/**
 * Empty board: every column present, every column empty. Returned
 * before the first fetch resolves so the page can render the column
 * skeletons without juggling ``undefined`` everywhere.
 */
const EMPTY_BOARD: Board = {
  queue: [],
  in_progress: [],
  in_review: [],
  approved: [],
  shipped: [],
};

export type Board = Record<KanbanColumn, V2Project[]>;

export type KanbanDataHook = {
  board: Board;
  loading: boolean;
  error: string | null;
  /** Re-fetch the workspace's projects from the server. */
  refetch: () => void;
  /**
   * Cross-column drag — manual override with a non-empty note. Maps
   * the target column to a canonical project_state. The "thinking"
   * column is system-managed; we expose ``COLUMN_TARGET_STATE`` as
   * a constant so the dialog can pre-fill the UI without re-deriving
   * the mapping. Returns ``true`` on success so the caller can chain
   * a confirmation toast.
   */
  mutateState: (input: MutateStateInput) => Promise<boolean>;
  /**
   * Same-column drag — sparse 1024-step int written to priority_order.
   * Caller computes the new value (midpoint of neighbors); this hook
   * just persists + rolls back on 4xx.
   */
  mutatePriority: (input: MutatePriorityInput) => Promise<boolean>;
  /**
   * Same-column drag — bootstrap path. Stamps every project in the
   * column with sparse 1024-step priorities reflecting the supplied
   * post-move order. Used when the column has any null
   * ``priority_order`` cards: a single-card stamp would put the
   * dragged card above all the nulls (priority_order ASC NULLS LAST),
   * which is the "drop always lands at top" bug. Fires N parallel
   * requests; rolls back on the first failure.
   */
  bulkMutatePriority: (input: BulkMutatePriorityInput) => Promise<boolean>;
};

export type MutateStateInput = {
  projectId: string;
  fromColumn: KanbanColumn;
  toColumn: KanbanColumn;
  note: string;
};

export type MutatePriorityInput = {
  projectId: string;
  columnId: KanbanColumn;
  priorityOrder: number;
};

export type BulkMutatePriorityInput = {
  columnId: KanbanColumn;
  /** Project ids in the desired post-move visual order (top → bottom).
   *  Each will be stamped with priority_order = (index + 1) * 1024. */
  orderedProjectIds: string[];
};

/**
 * Cross-column drag rebinds the project's state via the manual-
 * override endpoint. The mapping is intentionally asymmetric — the
 * "in_progress" column has no clean target state (it's a derived
 * view of pending_review + AI/draft signals) so we refuse drops
 * there. Shipped is also derived (approved + PR pushed) so a drag
 * onto Shipped just maps to approved; the GitHub-push side-effect
 * happens separately via "Push to GitHub".
 */
export const COLUMN_TARGET_STATE: Partial<Record<KanbanColumn, ProjectState>> = {
  queue: "pending_review",
  in_review: "in_review",
  approved: "approved",
  shipped: "approved",
};

/**
 * Group a flat ``V2Project[]`` (server already sorted by priority +
 * ROI + created_at) into the 5 Kanban columns.
 *
 * The mapping is deterministic:
 *
 *   pending_review (no orchestrator_run_id)         → queue
 *   pending_review (canvas drafted) | rejected | summary_ready
 *     | pending_review + ai_review_in_progress      → in_progress
 *   in_review                                       → in_review
 *   approved (no PR pushed)                         → approved
 *   approved (metadata.pr.pr_number set)            → shipped
 *
 * Sort within each column comes from the server response order; this
 * function preserves that order across the buckets.
 *
 * Exported for the Wave 4 unit tests so we can pin the mapping
 * without rendering the full page tree.
 */
// Kanban only surfaces actionable triage categories. Non-actionable
// signals (praise / question / noise / general) live in /inbox and are
// excluded here so the Kanban stays focused on what the team needs to
// decide on. Founder ask 2026-05-04.
const KANBAN_HIDDEN_CATEGORIES = new Set([
  "praise",
  "question",
  "noise",
  "general",
]);

function isKanbanEligible(project: V2Project): boolean {
  const value = project.metadata?.["dominant_category"];
  if (typeof value !== "string") return true; // No category yet — keep
  return !KANBAN_HIDDEN_CATEGORIES.has(value.toLowerCase().trim());
}

export function groupByColumn(projects: V2Project[]): Board {
  const board: Board = {
    queue: [],
    in_progress: [],
    in_review: [],
    approved: [],
    shipped: [],
  };
  for (const project of projects) {
    if (!isKanbanEligible(project)) continue;
    board[columnFor(project)].push(project);
  }
  return board;
}

export function columnFor(project: V2Project): KanbanColumn {
  const state = project.project_state ?? "pending_review";
  // Founder rename 2026-05-04: column semantics simplified.
  //   queue       — fresh shells, no orchestrator run yet
  //   in_progress — AI thinking OR Draft (canvas drafted, not
  //                 yet promoted to in_review). Also catches
  //                 rejected + summary_ready as "still being
  //                 worked on" Drafts.
  //   in_review   — project_state == in_review
  //   approved    — project_state == approved (no PR pushed)
  //   shipped     — approved AND metadata.pr.pr_number set
  if (state === "approved") {
    return prPushed(project) ? "shipped" : "approved";
  }
  if (state === "in_review") return "in_review";
  if (aiInProgress(project)) return "in_progress";
  if (canvasAlreadyDrafted(project)) return "in_progress";
  if (state === "rejected" || state === "summary_ready") {
    return "in_progress";
  }
  return "queue";
}

function aiInProgress(project: V2Project): boolean {
  const flag = project.metadata?.["ai_review_in_progress"];
  return flag === true;
}

function canvasAlreadyDrafted(project: V2Project): boolean {
  const md = project.metadata ?? {};
  return Boolean(md["orchestrator_run_id"] && md["theme_id"]);
}

function prPushed(project: V2Project): boolean {
  const pr = project.metadata?.["pr"];
  return Boolean(
    pr && typeof pr === "object" && (pr as Record<string, unknown>)["pr_number"],
  );
}

/**
 * One-shot fetch + state hook for the workspace Kanban. Mirrors the
 * lazy-fetch pattern from ``ProjectsListPage`` (no react-query
 * dependency this slice).
 *
 * Wave 4 ships read-only data flow: ``refetch`` re-pulls the full
 * board. Optimistic mutate helpers + rollback on 4xx land in Wave 5
 * with the drag-drop wiring.
 */
export function useKanbanData(workspaceId: string): KanbanDataHook {
  const [board, setBoard] = useState<Board>(EMPTY_BOARD);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(
    async (signal?: AbortSignal): Promise<void> => {
      setLoading(true);
      setError(null);
      try {
        const { projects } = await api.listWorkspaceProjects(workspaceId);
        if (signal?.aborted) return;
        setBoard(groupByColumn(projects));
      } catch (err) {
        if (signal?.aborted) return;
        const message =
          err instanceof Error ? err.message : "Failed to load workspace";
        setError(message);
        setBoard(EMPTY_BOARD);
      } finally {
        if (!signal?.aborted) setLoading(false);
      }
    },
    [workspaceId],
  );

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);

  const refetch = useCallback(() => {
    void load();
  }, [load]);

  /**
   * Optimistic cross-column move. Snapshot the current board, apply
   * the move locally, fire the request; on failure restore the
   * snapshot and surface the error so the caller can toast.
   */
  const mutateState = useCallback(
    async (input: MutateStateInput): Promise<boolean> => {
      const targetState = COLUMN_TARGET_STATE[input.toColumn];
      if (!targetState) {
        setError(
          `The "${input.toColumn}" column is system-managed and can't accept drops.`,
        );
        return false;
      }
      // Note is optional — BE captures actor_user_id from the auth
      // context for audit. Trim defensively but allow empty strings
      // through (product decision).
      const note = input.note.trim();
      // Snapshot for rollback.
      const snapshot = cloneBoard(board);
      // Optimistic move — find + remove from source, push to target.
      const optimistic = cloneBoard(board);
      const fromList = optimistic[input.fromColumn];
      const idx = fromList.findIndex((p) => p.project_id === input.projectId);
      if (idx === -1) {
        // The drag start state diverged from the live board — refetch
        // and abort. Avoids stamping a stale move onto the server.
        void load();
        return false;
      }
      const moved: V2Project = {
        ...fromList[idx],
        project_state: targetState,
        priority_order: null,
      };
      fromList.splice(idx, 1);
      optimistic[input.toColumn].push(moved);
      setBoard(optimistic);
      try {
        await api.manualStateOverrideProject(
          input.projectId, targetState, note,
        );
        return true;
      } catch (err) {
        setBoard(snapshot);
        const message =
          err instanceof Error ? err.message : "Move failed";
        setError(message);
        return false;
      }
    },
    [board, load],
  );

  /**
   * Optimistic same-column reorder. The server is the source of
   * truth on the final integer; we update locally with the new
   * value, fire the request, restore on failure. The caller is
   * responsible for picking a midpoint that doesn't collide.
   */
  const mutatePriority = useCallback(
    async (input: MutatePriorityInput): Promise<boolean> => {
      const snapshot = cloneBoard(board);
      const optimistic = cloneBoard(board);
      const list = optimistic[input.columnId];
      const idx = list.findIndex((p) => p.project_id === input.projectId);
      if (idx === -1) {
        void load();
        return false;
      }
      list[idx] = { ...list[idx], priority_order: input.priorityOrder };
      // Re-sort within the column by the same tuple the server uses
      // so the optimistic view matches the post-fetch shape.
      list.sort(compareProjects);
      setBoard(optimistic);
      try {
        await api.manualPriorityOrderProject(
          input.projectId, input.priorityOrder,
        );
        return true;
      } catch (err) {
        setBoard(snapshot);
        const message =
          err instanceof Error ? err.message : "Reorder failed";
        setError(message);
        return false;
      }
    },
    [board, load],
  );

  /**
   * Bootstrap-stamp every project in the column with a sparse
   * priority reflecting the supplied post-move order. See type-level
   * comment on ``KanbanDataHook.bulkMutatePriority``.
   */
  const bulkMutatePriority = useCallback(
    async (input: BulkMutatePriorityInput): Promise<boolean> => {
      const snapshot = cloneBoard(board);
      const optimistic = cloneBoard(board);
      const list = optimistic[input.columnId];
      const byId = new Map(list.map((p) => [p.project_id, p] as const));
      const reordered: V2Project[] = [];
      input.orderedProjectIds.forEach((pid, idx) => {
        const card = byId.get(pid);
        if (!card) return;
        reordered.push({ ...card, priority_order: (idx + 1) * 1024 });
      });
      // Preserve any cards the caller didn't include (defensive — the
      // column may have been mutated since the drag started). Append
      // them at the end with priorities continuing past the last
      // explicit slot.
      list.forEach((card) => {
        if (!input.orderedProjectIds.includes(card.project_id)) {
          reordered.push({
            ...card,
            priority_order:
              (input.orderedProjectIds.length + reordered.length + 1) * 1024,
          });
        }
      });
      optimistic[input.columnId] = reordered;
      setBoard(optimistic);
      try {
        await Promise.all(
          reordered.map((card) =>
            api.manualPriorityOrderProject(
              card.project_id,
              card.priority_order ?? 0,
            ),
          ),
        );
        return true;
      } catch (err) {
        setBoard(snapshot);
        const message =
          err instanceof Error ? err.message : "Reorder failed";
        setError(message);
        return false;
      }
    },
    [board],
  );

  return {
    board,
    loading,
    error,
    refetch,
    mutateState,
    mutatePriority,
    bulkMutatePriority,
  };
}

function cloneBoard(board: Board): Board {
  return {
    queue: [...board.queue],
    in_progress: [...board.in_progress],
    in_review: [...board.in_review],
    approved: [...board.approved],
    shipped: [...board.shipped],
  };
}

/**
 * Same sort tuple the server uses (priority_order ASC NULLS LAST,
 * roi_score DESC, created_at DESC). Exported as a private helper for
 * the optimistic re-sort path so the in-memory view doesn't drift
 * from what the next refetch will return.
 */
function compareProjects(a: V2Project, b: V2Project): number {
  // priority_order ASC NULLS LAST
  const aP = a.priority_order;
  const bP = b.priority_order;
  if (aP !== null && aP !== undefined) {
    if (bP !== null && bP !== undefined) {
      if (aP !== bP) return aP - bP;
    } else {
      return -1;
    }
  } else if (bP !== null && bP !== undefined) {
    return 1;
  }
  // roi_score DESC NULLS LAST
  const aR = a.roi_score;
  const bR = b.roi_score;
  if (aR !== null && aR !== undefined) {
    if (bR !== null && bR !== undefined) {
      if (aR !== bR) return bR - aR;
    } else {
      return -1;
    }
  } else if (bR !== null && bR !== undefined) {
    return 1;
  }
  // created_at DESC
  return (b.created_at ?? "").localeCompare(a.created_at ?? "");
}
