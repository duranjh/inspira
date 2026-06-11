import { useDroppable } from "@dnd-kit/core";
import {
  SortableContext,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable";

import type { KanbanColumn as ColumnId, V2Project } from "../inspira/api";
import { KanbanCard } from "./KanbanCard";

export type KanbanColumnProps = {
  id: ColumnId;
  /** Display name shown in the header. */
  name: string;
  /** Italic-serif one-liner under the title. */
  subtitle: string;
  /** Empty-state copy when ``cards`` is empty. */
  emptyMsg: string;
  /** Optional CTA label rendered as an outlined sage pill. */
  emptyCta?: string;
  /** Click handler for the empty-state CTA. Required when ``emptyCta``
   * is set; lifted up so the navigate call lives inside Router context
   * (KanbanColumnView is unit-tested without a Router wrapper). */
  onEmptyCtaClick?: () => void;
  /** Color treatment for the count chip — maps to a CSS modifier. */
  chipColor: "sage" | "gold" | "rust" | "sage-filled" | "muted";
  cards: V2Project[];
  /** Disable drag wiring so the test harness can render columns
   * without a DndContext. Defaults to ``true`` in production. */
  draggable?: boolean;
  /** Optional bulk-select state — when ``onToggleChecked`` is set,
   * each card renders a leading checkbox, and the column also opts
   * into the WorkspaceKanban-level bulk action bar. */
  checkedIds?: ReadonlySet<string>;
  onToggleChecked?: (projectId: string) => void;
  /** When true and ``cards`` is empty, render skeleton placeholder
   * cards instead of the empty-state copy — gives partners visual
   * feedback during the initial board fetch instead of the column
   * looking instantly empty + then re-flowing once data arrives. */
  loading?: boolean;
};

/**
 * One Kanban column. Header sticks to the top of the scrollable
 * region; cards stack below. Empty state mirrors the design's
 * italic-serif voice.
 *
 * Drag wiring (W5): each column registers as a droppable so empty
 * columns still accept cross-column drops (the cards' SortableContext
 * only triggers on card-over-card drops, not on the bare container).
 * The ``thinking`` column refuses drops because the AI thinking state
 * is system-managed (state=pending_review + metadata flag, which the
 * manual-override path can't set in this slice).
 */
export function KanbanColumnView(props: KanbanColumnProps) {
  const {
    id, name, subtitle, emptyMsg, emptyCta, onEmptyCtaClick, chipColor, cards,
    draggable = true,
    checkedIds,
    onToggleChecked,
    loading = false,
  } = props;
  // "In Progress" accepts drops from Queue — drop triggers a sub-agent
  // spawn via the WorkspaceKanban onDragEnd handler. The column itself
  // doesn't need to refuse drops.
  const droppable = useDroppable({
    id: `column:${id}`,
    data: { column: id, type: "column" },
    disabled: !draggable,
  });
  return (
    <div
      className="kb-col"
      data-column-id={id}
      data-drop-active={
        draggable && droppable.isOver ? "true" : "false"
      }
      data-drop-blocked="false"
    >
      <div className="kb-col__header">
        <div className="kb-col__header-top">
          <h3 className="kb-col__name">{name}</h3>
          <span
            className={`kb-col-chip kb-col-chip--${chipColor}`}
            aria-label={`${cards.length} cards`}
          >
            {cards.length}
          </span>
        </div>
        <p className="kb-col__subtitle">{subtitle}</p>
      </div>
      <div
        className="kb-col__cards"
        ref={draggable ? droppable.setNodeRef : undefined}
      >
        <SortableContext
          items={cards.map((c) => c.project_id)}
          strategy={verticalListSortingStrategy}
        >
          {cards.length === 0 ? (
            loading ? (
              <>
                <div
                  className="kb-card kb-card--skeleton"
                  aria-hidden="true"
                />
                <div
                  className="kb-card kb-card--skeleton"
                  aria-hidden="true"
                />
              </>
            ) : (
              <div className="kb-col__empty">
                <p>{emptyMsg}</p>
                {emptyCta && (
                  <button
                    type="button"
                    className="kb-col__empty-cta"
                    onClick={onEmptyCtaClick}
                  >
                    {emptyCta}
                  </button>
                )}
              </div>
            )
          ) : (
            cards.map((card, idx) => (
              <div key={card.project_id}>
                {idx > 0 && (
                  <div className="kb-divider" aria-hidden="true" />
                )}
                <KanbanCard
                  project={card}
                  column={id}
                  draggable={draggable}
                  checked={checkedIds?.has(card.project_id)}
                  onToggleChecked={
                    onToggleChecked
                      ? () => onToggleChecked(card.project_id)
                      : undefined
                  }
                />
              </div>
            ))
          )}
        </SortableContext>
      </div>
    </div>
  );
}
