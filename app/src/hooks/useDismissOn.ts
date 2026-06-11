// Document-level Esc + click-outside dismiss helper for modal- and
// popover-shaped surfaces. Replaces the keydown+mousedown useEffect
// pattern that was duplicated across CommentThread, InlineCommentBox,
// DecisionSummaryDrawer, ManualOverrideDialog, and AIStatus.
//
// Two listeners, intentionally:
//   - Esc attaches at the document **capture phase** so it beats any
//     upstream listener that might swallow it (matches Dialog.tsx).
//   - mousedown attaches at the document **bubble phase** to match the
//     existing tests in DecisionSummaryDrawer.test.tsx which dispatch
//     `new MouseEvent("mousedown", { bubbles: true })`.
//
// click-outside is opt-in via `clickOutsideRef`. When omitted (e.g. on
// modals that handle backdrop dismiss inline via a backdrop div's
// onClick), only Esc fires.

import { useEffect, type RefObject } from "react";

export interface UseDismissOnOptions {
  /** When false the hook is fully inert. */
  enabled: boolean;
  /** Called when Esc fires or mousedown lands outside `clickOutsideRef`. */
  onDismiss: () => void;
  /** Default true. Listen for Escape at the document level (capture phase). */
  esc?: boolean;
  /** When provided, mousedown outside this ref calls `onDismiss`. */
  clickOutsideRef?: RefObject<HTMLElement | null>;
}

export function useDismissOn(options: UseDismissOnOptions): void {
  const { enabled, onDismiss, esc = true, clickOutsideRef } = options;

  useEffect(() => {
    if (!enabled || !esc) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        e.preventDefault();
        onDismiss();
      }
    };
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, [enabled, esc, onDismiss]);

  useEffect(() => {
    if (!enabled || !clickOutsideRef) return;
    const onMouseDown = (e: MouseEvent) => {
      const root = clickOutsideRef.current;
      if (!root) return;
      if (e.target instanceof Node && !root.contains(e.target)) {
        onDismiss();
      }
    };
    document.addEventListener("mousedown", onMouseDown);
    return () => document.removeEventListener("mousedown", onMouseDown);
  }, [enabled, clickOutsideRef, onDismiss]);
}
