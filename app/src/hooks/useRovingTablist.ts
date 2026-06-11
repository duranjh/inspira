import { useCallback, useRef, type KeyboardEvent } from "react";

/**
 * Roving-tabindex helper for ARIA tablists (closes #133).
 *
 * Given an ordered list of unique tab IDs, an active ID, and a select
 * callback, returns:
 *   - tabIndex(id) -> 0 for the active tab, -1 for inactive (so the
 *     tablist as a whole is one stop in the Tab order)
 *   - onKeyDown(id) -> handles ArrowLeft/Right (cycles with wrap),
 *     Home/End (jumps to first/last). Calls onSelect with the new ID
 *     and focuses that tab's DOM node.
 *   - registerRef(id, el) -> ref callback that stashes each tab button
 *     so onKeyDown can call focus() on it.
 *
 * Native Space/Enter activation is left to the underlying <button>;
 * roving tabindex doesn't need to override it.
 *
 * Per WAI-ARIA Authoring Practices (tabs pattern, automatic activation).
 */
export function useRovingTablist<Id extends string>(opts: {
  ids: readonly Id[];
  activeId: Id | null | undefined;
  onSelect: (id: Id) => void;
}): {
  tabIndex: (id: Id) => 0 | -1;
  onKeyDown: (id: Id) => (e: KeyboardEvent<HTMLElement>) => void;
  registerRef: (id: Id) => (el: HTMLElement | null) => void;
} {
  const { ids, activeId, onSelect } = opts;
  // Stale entries clean themselves up: the ref callback fires with
  // null when a tab unmounts, deleting its slot.
  const refs = useRef<Partial<Record<Id, HTMLElement | null>>>({});

  const registerRef = useCallback(
    (id: Id) => (el: HTMLElement | null) => {
      if (el === null) delete refs.current[id];
      else refs.current[id] = el;
    },
    [],
  );

  const tabIndex = useCallback(
    (id: Id): 0 | -1 => (id === activeId ? 0 : -1),
    [activeId],
  );

  const focusAndSelect = useCallback(
    (id: Id) => {
      onSelect(id);
      // Defer focus to the next frame so the consumer's onSelect has
      // a chance to update aria-selected before the focus shift.
      requestAnimationFrame(() => {
        refs.current[id]?.focus();
      });
    },
    [onSelect],
  );

  const onKeyDown = useCallback(
    (id: Id) => (e: KeyboardEvent<HTMLElement>) => {
      const count = ids.length;
      if (count === 0) return;
      const idx = ids.indexOf(id);
      if (idx === -1) return;
      let target: Id | null = null;
      switch (e.key) {
        case "ArrowLeft":
        case "ArrowUp":
          target = ids[(idx - 1 + count) % count];
          break;
        case "ArrowRight":
        case "ArrowDown":
          target = ids[(idx + 1) % count];
          break;
        case "Home":
          target = ids[0];
          break;
        case "End":
          target = ids[count - 1];
          break;
        default:
          return;
      }
      e.preventDefault();
      e.stopPropagation();
      if (target !== null) focusAndSelect(target);
    },
    [ids, focusAndSelect],
  );

  return { tabIndex, onKeyDown, registerRef };
}
