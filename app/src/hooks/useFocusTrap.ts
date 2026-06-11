// Tab-cycle focus trap for modal-shaped surfaces. Lifted from the
// Dialog primitive (app/src/components/dialogs/Dialog.tsx) so any
// dialog-shaped component can share the same a11y promise without
// hand-rolling focusable enumeration, invoker capture, and restoration.
//
// Caller owns the container ref. The hook returns an `onKeyDown`
// handler that the caller spreads on that container — Tab and
// Shift+Tab cycling fires from there, so it only engages when focus
// is already inside the container (correct: if focus is outside, the
// trap has nothing to defend).
//
// Initial focus is moved to the first focusable inside the container
// on engage, or to a caller-provided ref. On disengage focus restores
// to whatever element was active when the hook engaged. Pass
// `autoFocus: false` when the caller manages initial focus itself
// (e.g. DecisionSummaryDrawer's conditional aside-focus that skips
// when the user is mid-typing in an editable).

import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  type KeyboardEvent,
  type RefObject,
} from "react";

export interface UseFocusTrapOptions {
  /** When false the hook is fully inert — no listeners, no focus moves. */
  enabled: boolean;
  /** Override the initial focus target. Default: first focusable inside `ref`. */
  initialFocusRef?: RefObject<HTMLElement | null>;
  /** Default true. Set false when the caller manages initial focus itself. */
  autoFocus?: boolean;
  /** Default true. Set false to skip returning focus to the previously-active element. */
  restoreFocus?: boolean;
}

const FOCUSABLE_SELECTOR = [
  "a[href]",
  "button:not([disabled])",
  "textarea:not([disabled])",
  "input:not([disabled]):not([type='hidden'])",
  "select:not([disabled])",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

function getFocusable(root: HTMLElement): HTMLElement[] {
  // The selector already excludes disabled controls and tabindex=-1.
  // For CSS-hidden descendants (display:none / hidden ancestor),
  // focus() is a no-op in browsers — so including them in the list
  // is harmless. We previously filtered on offsetParent, but jsdom
  // returns null for everything (no layout), breaking tests.
  return Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR));
}

export function useFocusTrap<E extends HTMLElement>(
  ref: RefObject<E | null>,
  options: UseFocusTrapOptions,
): { onKeyDown: (e: KeyboardEvent<E>) => void } {
  const {
    enabled,
    initialFocusRef,
    autoFocus = true,
    restoreFocus = true,
  } = options;

  const invokerRef = useRef<HTMLElement | null>(null);

  // Capture the invoker BEFORE we shift focus into the container, so we
  // can restore it on disengage. useLayoutEffect runs before paint —
  // important because the autoFocus effect below may move focus and we
  // need to snapshot the previous activeElement first.
  useLayoutEffect(() => {
    if (!enabled) return;
    const active = document.activeElement;
    if (active instanceof HTMLElement) {
      invokerRef.current = active;
    }
  }, [enabled]);

  // Move initial focus on engage. Delayed one frame so the container
  // has time to mount any conditionally-rendered descendants before we
  // query for focusables.
  useEffect(() => {
    if (!enabled || !autoFocus) return;
    const raf = window.requestAnimationFrame(() => {
      if (initialFocusRef?.current) {
        initialFocusRef.current.focus();
        return;
      }
      const container = ref.current;
      if (!container) return;
      const focusables = getFocusable(container);
      // Prefer the first non-close focusable so dialogs land on
      // inputs/primary actions, not on the × button.
      const first =
        focusables.find((el) => !el.classList.contains("dlg__close")) ??
        focusables[0] ??
        null;
      first?.focus();
    });
    return () => window.cancelAnimationFrame(raf);
  }, [enabled, autoFocus, ref, initialFocusRef]);

  // Restore focus to the invoker when the trap disengages. Microtask
  // (rAF) lets parent re-renders settle before we move focus back,
  // avoiding a flash of focus on a soon-to-be-unmounted element.
  useEffect(() => {
    if (enabled) return;
    if (!restoreFocus) return;
    const invoker = invokerRef.current;
    if (invoker && document.contains(invoker)) {
      const raf = window.requestAnimationFrame(() => invoker.focus());
      return () => window.cancelAnimationFrame(raf);
    }
  }, [enabled, restoreFocus]);

  // Tab trap — keep focus inside the container. Attached as onKeyDown
  // on the container so it only fires when focus is already inside;
  // events bubble from the focused element through the container.
  const onKeyDown = useCallback(
    (e: KeyboardEvent<E>) => {
      if (!enabled) return;
      if (e.key !== "Tab") return;
      const container = ref.current;
      if (!container) return;
      const focusables = getFocusable(container);
      if (focusables.length === 0) {
        e.preventDefault();
        return;
      }
      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      const active = document.activeElement as HTMLElement | null;
      if (e.shiftKey) {
        if (active === first || !container.contains(active)) {
          e.preventDefault();
          last.focus();
        }
      } else {
        if (active === last || !container.contains(active)) {
          e.preventDefault();
          first.focus();
        }
      }
    },
    [enabled, ref],
  );

  return { onKeyDown };
}
