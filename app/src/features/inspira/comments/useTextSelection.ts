// Text selection hook scoped to ``[data-cc-target]`` containers.
//
// Returns a transient ``Selection`` object whenever the user has
// highlighted text inside a CommentTargetWrapper. Returns null when
// the selection is collapsed, outside of any wrapper, or cleared.
//
// Position is computed in viewport space (clientRect) and clamped
// horizontally so the floating button never spills off the edge.

import { useEffect, useState } from "react";

import type { CommentTargetKind } from "./types";

export type SelectionInfo = {
  text: string;
  rect: { top: number; left: number; width: number; height: number };
  target: { kind: CommentTargetKind; id: string };
};

const TARGET_ATTR = "data-cc-target";
const TARGET_KIND_ATTR = "data-cc-target-kind";
const TARGET_ID_ATTR = "data-cc-target-id";
const NO_SELECT_ATTR = "data-cc-no-select";

function _findTargetAncestor(node: Node | null): HTMLElement | null {
  let cur: Node | null = node;
  while (cur) {
    const el = cur.nodeType === Node.ELEMENT_NODE
      ? (cur as HTMLElement)
      : cur.parentElement;
    if (!el) return null;
    if (el.closest(`[${NO_SELECT_ATTR}]`)) return null;
    const wrapper = el.closest(`[${TARGET_ATTR}]`) as HTMLElement | null;
    return wrapper;
  }
  return null;
}

function _readTarget(wrapper: HTMLElement): { kind: CommentTargetKind; id: string } | null {
  const kind = wrapper.getAttribute(TARGET_KIND_ATTR) as CommentTargetKind | null;
  const id = wrapper.getAttribute(TARGET_ID_ATTR);
  if (!kind || !id) return null;
  return { kind, id };
}

export function useTextSelection(): SelectionInfo | null {
  const [info, setInfo] = useState<SelectionInfo | null>(null);

  useEffect(() => {
    function handle(): void {
      const sel = typeof window !== "undefined" ? window.getSelection() : null;
      if (!sel || sel.isCollapsed) {
        setInfo(null);
        return;
      }
      const text = sel.toString().trim();
      if (!text) {
        setInfo(null);
        return;
      }
      const wrapper = _findTargetAncestor(sel.anchorNode);
      if (!wrapper) {
        setInfo(null);
        return;
      }
      // Make sure both ends of the selection are inside the same wrapper —
      // cross-row selection should hide the button.
      const focusWrapper = _findTargetAncestor(sel.focusNode);
      if (focusWrapper !== wrapper) {
        setInfo(null);
        return;
      }
      const target = _readTarget(wrapper);
      if (!target) {
        setInfo(null);
        return;
      }
      const range = sel.getRangeAt(0);
      const rect = range.getBoundingClientRect();
      setInfo({
        text,
        rect: {
          top: rect.top,
          left: rect.left,
          width: rect.width,
          height: rect.height,
        },
        target,
      });
    }

    function clearOnEscape(e: KeyboardEvent): void {
      if (e.key === "Escape") {
        setInfo(null);
        window.getSelection()?.removeAllRanges();
      }
    }

    function clearOnScroll(): void {
      // Selection rect goes stale on scroll — recompute via the same handler.
      handle();
    }

    document.addEventListener("selectionchange", handle);
    document.addEventListener("keydown", clearOnEscape);
    window.addEventListener("scroll", clearOnScroll, { capture: true, passive: true });
    return () => {
      document.removeEventListener("selectionchange", handle);
      document.removeEventListener("keydown", clearOnEscape);
      window.removeEventListener("scroll", clearOnScroll, { capture: true });
    };
  }, []);

  return info;
}

// Compute the floating-button position from a selection rect. Default
// renders ABOVE the selection; flips below if it would clip the top of
// the viewport. Horizontal clamp prevents edge overflow.
export function computeFloatingPosition(
  rect: SelectionInfo["rect"],
  viewport: { width: number; height: number } = {
    width: typeof window !== "undefined" ? window.innerWidth : 1024,
    height: typeof window !== "undefined" ? window.innerHeight : 768,
  },
  buttonWidth = 96,
): { top: number; left: number; placement: "above" | "below" } {
  const minTop = 8;
  const flip = rect.top < 50;
  const placement: "above" | "below" = flip ? "below" : "above";
  const rawLeft = rect.left + rect.width / 2 - buttonWidth / 2;
  const left = Math.max(8, Math.min(rawLeft, viewport.width - buttonWidth - 8));
  const top = flip
    ? rect.top + rect.height + 6
    : Math.max(minTop, rect.top - 32);
  return { top, left, placement };
}
