// B2.3 — single row in the Promote dialog's topic-seeds list.
//
// Drag handle (⠿) + name + desc + remove ×. HTML5 native drag-drop
// (zero deps). Inputs and the × button stop pointer propagation so the
// drag only initiates from the drag handle / non-interactive row area.
// Removed rows are non-draggable (visual line-through).
//
// Keyboard reorder (closes #132): the handle is a focusable button.
// ArrowUp / ArrowDown / Home / End on the handle reorder the seed via
// onReorderKey; the parent re-focuses the handle on the moved seed
// using the handleRef callback so the user can chain moves.

import type {
  CSSProperties,
  DragEvent,
  KeyboardEvent,
  MouseEvent,
} from "react";

import type { TopicSeed } from "./seedsFixture";

export type SeedReorderKey = "up" | "down" | "first" | "last";

export interface TopicSeedRowProps {
  seed: TopicSeed;
  dragOverBefore: boolean | null; // null = no indicator, true = above, false = below
  isDragging: boolean;
  /** 1-based position among ACTIVE seeds (removed seeds skipped).
   *  Surfaced in the handle's aria-label so screen readers announce
   *  "Reorder seed 'foo', position 2 of 5". null for removed seeds. */
  position: number | null;
  /** Count of active (non-removed) seeds — pairs with ``position`` in
   *  the aria-label. */
  totalActive: number;
  onNameChange: (id: string, name: string) => void;
  onDescChange: (id: string, desc: string) => void;
  onToggleRemove: (id: string) => void;
  onDragStart: (e: DragEvent<HTMLLIElement>, id: string) => void;
  onDragOver: (e: DragEvent<HTMLLIElement>, id: string) => void;
  onDragLeave: (e: DragEvent<HTMLLIElement>, id: string) => void;
  onDrop: (e: DragEvent<HTMLLIElement>, id: string) => void;
  onDragEnd: () => void;
  /** Keyboard reorder request — parent maps to setSeeds + announces. */
  onReorderKey: (id: string, direction: SeedReorderKey) => void;
  /** Stash the handle's DOM node so the parent can refocus after a
   *  keyboard reorder lands. ``el`` is null on unmount. */
  registerHandleRef: (id: string, el: HTMLButtonElement | null) => void;
}

export function TopicSeedRow({
  seed,
  dragOverBefore,
  isDragging,
  position,
  totalActive,
  onNameChange,
  onDescChange,
  onToggleRemove,
  onDragStart,
  onDragOver,
  onDragLeave,
  onDrop,
  onDragEnd,
  onReorderKey,
  registerHandleRef,
}: TopicSeedRowProps) {
  const stop = (e: MouseEvent | DragEvent) => e.stopPropagation();
  const dragIndicator =
    dragOverBefore === true
      ? "before"
      : dragOverBefore === false
        ? "after"
        : null;

  const className =
    "pm-topic" +
    (seed.removed ? " pm-topic--removed" : "") +
    (seed.added ? " pm-topic--added" : "") +
    (isDragging ? " pm-topic--dragging" : "");

  const style: CSSProperties = {
    opacity: isDragging ? 0.5 : undefined,
  };

  const handleHandleKeyDown = (e: KeyboardEvent<HTMLButtonElement>) => {
    if (seed.removed) return;
    switch (e.key) {
      case "ArrowUp":
        e.preventDefault();
        onReorderKey(seed.id, "up");
        break;
      case "ArrowDown":
        e.preventDefault();
        onReorderKey(seed.id, "down");
        break;
      case "Home":
        e.preventDefault();
        onReorderKey(seed.id, "first");
        break;
      case "End":
        e.preventDefault();
        onReorderKey(seed.id, "last");
        break;
      default:
        break;
    }
  };

  const handleAriaLabel =
    position != null && !seed.removed
      ? `Reorder topic ${seed.name ? `'${seed.name}'` : "seed"}, position ${position} of ${totalActive}. Use arrow keys, Home, or End.`
      : "Reorder topic (disabled — restore the seed first).";

  return (
    <li
      className={className}
      draggable={!seed.removed}
      data-drag-over={dragIndicator}
      onDragStart={(e) => onDragStart(e, seed.id)}
      onDragOver={(e) => onDragOver(e, seed.id)}
      onDragLeave={(e) => onDragLeave(e, seed.id)}
      onDrop={(e) => onDrop(e, seed.id)}
      onDragEnd={onDragEnd}
      style={style}
    >
      <button
        type="button"
        ref={(el) => registerHandleRef(seed.id, el)}
        className="pm-topic__handle"
        aria-label={handleAriaLabel}
        title="Drag, or use arrow keys to reorder"
        onKeyDown={handleHandleKeyDown}
        onMouseDown={stop}
        disabled={seed.removed}
      >
        <span aria-hidden="true">⠿</span>
      </button>
      <div className="pm-topic__body">
        <input
          type="text"
          className="pm-topic__name"
          value={seed.name}
          onChange={(e) => onNameChange(seed.id, e.target.value)}
          onMouseDown={stop}
          disabled={seed.removed}
          aria-label="Topic name"
        />
        <input
          type="text"
          className="pm-topic__desc"
          value={seed.desc}
          onChange={(e) => onDescChange(seed.id, e.target.value)}
          onMouseDown={stop}
          disabled={seed.removed}
          aria-label="Topic description"
          placeholder="Describe this topic"
        />
      </div>
      <button
        type="button"
        className="pm-topic__remove"
        onClick={() => onToggleRemove(seed.id)}
        onMouseDown={stop}
        aria-label={seed.removed ? "Restore topic" : "Remove topic"}
      >
        {seed.removed ? "↺" : "×"}
      </button>
    </li>
  );
}
