import { useSortable } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { useNavigate } from "react-router-dom";

import type { ProjectState } from "../../components/ProjectStateBadge";
import { api, type KanbanColumn, type V2Project } from "../inspira/api";

export type KanbanCardProps = {
  project: V2Project;
  column: KanbanColumn;
  /** Disable drag wiring — the test harness uses this to render
   * cards without spinning up a DndContext. */
  draggable?: boolean;
  /** Multi-select checkbox — when defined, renders the leading
   * checkbox. Click on the checkbox toggles ``checked`` via
   * ``onToggleChecked`` and the click is suppressed from the card's
   * ``openCanvas`` + drag listeners. */
  checked?: boolean;
  onToggleChecked?: () => void;
};

/**
 * One row of the Kanban — title, severity dot, compact state badge,
 * column-specific footer copy. The thinking column also gets the
 * three-dots loader bar (B1.2 canvas pattern).
 *
 * Wave 5 wires drag via @dnd-kit/sortable. The card is the active
 * draggable; cross-column drops are intercepted in WorkspaceKanban's
 * onDragEnd to open a confirmation dialog before mutating state.
 * Within-column drops fire ``mutatePriority`` immediately (audited,
 * idempotent server-side, optimistic + rollback on 4xx).
 */
export function KanbanCard(props: KanbanCardProps) {
  const {
    project,
    column,
    draggable = true,
    checked,
    onToggleChecked,
  } = props;
  const showCheckbox = onToggleChecked !== undefined;
  const navigate = useNavigate();
  const state: ProjectState = project.project_state ?? "pending_review";
  const subAgents = subAgentsCount(project);
  // The In Progress column now mixes AI-thinking cards with Drafts.
  // Only show the multi-agent dots animation when the orchestrator
  // is genuinely active on this project (matches the global AI
  // status chip's truth).
  const aiActive =
    (project.metadata as Record<string, unknown> | null | undefined)?.[
      "ai_review_in_progress"
    ] === true;
  const sortable = useSortable({
    id: project.project_id,
    // We surface the source column via the data slot so onDragEnd
    // can detect cross-column drops without recomputing the
    // current column from project.project_state (which won't have
    // refreshed yet during an optimistic move).
    data: { column },
    disabled: !draggable,
  });
  const style = {
    transform: CSS.Transform.toString(sortable.transform),
    transition: sortable.transition,
  };
  // Open the project's canvas. PointerSensor uses a 6px activation
  // distance (WorkspaceKanban.tsx:120), so a click without drag
  // movement still fires this — the sortable listeners only consume
  // the event after a drag begins.
  //
  // For auto-promoted Drafts (project_state == pending_review +
  // metadata.auto_promoted), kick off the orchestrator before
  // navigating so the canvas isn't empty when the user lands. The
  // call is idempotent server-side — a second click while
  // already-running returns the existing run_id rather than
  // spawning duplicates.
  const isAutoPromotedDraft =
    state === "pending_review" &&
    project.metadata?.["auto_promoted"] === true &&
    !project.metadata?.["orchestrator_run_id"];
  const openCanvas = () => {
    if (sortable.isDragging) return;
    if (isAutoPromotedDraft) {
      // Fire-and-forget — the canvas page will poll for topics on
      // mount and the Kanban poller will move the card to the
      // AI-thinking column when it sees state flip to in_review.
      void api.startProjectCanvas(project.project_id).catch(() => {
        // Silent fail — partner can re-click. Logging happens in
        // the central api wrapper.
      });
    }
    navigate("/app", { state: { openProject: project.project_id } });
  };
  return (
    <div
      className="kb-card"
      data-project-id={project.project_id}
      data-dragging={sortable.isDragging ? "true" : "false"}
      ref={draggable ? sortable.setNodeRef : undefined}
      style={draggable ? style : undefined}
      {...(draggable ? sortable.attributes : {})}
      {...(draggable ? sortable.listeners : {})}
      onClick={openCanvas}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          openCanvas();
        }
      }}
    >
      {showCheckbox ? (
        <label
          className="kb-card__check"
          onClick={(e) => e.stopPropagation()}
          onPointerDown={(e) => e.stopPropagation()}
          aria-label={`Select: ${project.title}`}
        >
          <input
            type="checkbox"
            checked={!!checked}
            onChange={() => onToggleChecked?.()}
          />
        </label>
      ) : null}
      <div className="kb-card__top">
        <span className="kb-card__title" title={project.title}>
          {project.title}
        </span>
      </div>
      {aiActive ? (
        <div className="kb-card__thinking">
          <span className="thinking-dots" aria-label="AI working">
            <span
              className="thinking-dots__dot"
              style={{ animationDelay: "0ms" }}
            />
            <span
              className="thinking-dots__dot"
              style={{ animationDelay: "200ms" }}
            />
            <span
              className="thinking-dots__dot"
              style={{ animationDelay: "400ms" }}
            />
          </span>
          {subAgents > 0 && (
            <span className="kb-card__agents">
              {subAgents} sub-agents
            </span>
          )}
        </div>
      ) : (
        <p className="kb-card__summary">{summaryFor(project)}</p>
      )}
      {dominantCategory(project) ? (
        <div className="kb-card__footer">
          <span
            className={
              "kb-card__triage kb-card__triage--" + dominantCategory(project)
            }
            title={`Triaged as ${triageLabel(dominantCategory(project)!)}`}
          >
            {triageLabel(dominantCategory(project)!)}
          </span>
        </div>
      ) : null}
    </div>
  );
}

const TRIAGE_CATEGORIES = new Set([
  "bug",
  "feature",
  "complaint",
  "praise",
  "question",
  "general",
]);

/** Pick the v2_project's `dominant_category` (set by the auto-promote
 *  helper at csv_import time). Returns null when missing or unknown
 *  so the chip simply doesn't render rather than emitting a generic
 *  "GENERAL" label that adds visual noise. */
function dominantCategory(project: V2Project): string | null {
  const value = project.metadata?.["dominant_category"];
  if (typeof value !== "string") return null;
  const lower = value.toLowerCase().trim();
  if (!TRIAGE_CATEGORIES.has(lower)) return null;
  return lower;
}

const TRIAGE_LABELS: Record<string, string> = {
  bug: "Bug",
  feature: "Feature",
  complaint: "Complaint",
  praise: "Praise",
  question: "Question",
  general: "General",
};

function triageLabel(category: string): string {
  return TRIAGE_LABELS[category] ?? "General";
}

function subAgentsCount(project: V2Project): number {
  const value = project.metadata?.["sub_agents_active"];
  if (typeof value === "number" && Number.isFinite(value) && value > 0) {
    return value;
  }
  return 0;
}

/**
 * Body text for non-thinking columns. Built from server data in
 * priority order: explicit ``summary`` field on metadata, then a
 * derived "N items · ROI X.X" line if ROI is set, else nothing.
 */
function summaryFor(project: V2Project): string {
  const summary = project.metadata?.["summary"];
  if (typeof summary === "string" && summary.length > 0) return summary;
  const itemsRaw = project.metadata?.["feedback_count"];
  const items = typeof itemsRaw === "number" ? itemsRaw : null;
  const roi = project.roi_score ?? null;
  if (items !== null && roi !== null) {
    return `${items} feedback items · ROI ${(roi / 10).toFixed(1)}/10`;
  }
  if (roi !== null) return `ROI ${(roi / 10).toFixed(1)}/10`;
  return "";
}
