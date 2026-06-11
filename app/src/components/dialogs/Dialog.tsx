// Inspira — base dialog shell.
//
// Warm-editorial replacement for window.prompt / window.confirm. Every
// specialized dialog (rename / delete / share / export) composes this
// component and passes body content + action config.
//
// Responsibilities:
//   * Backdrop + centered paper card (fade + slide-up entrance).
//   * Body scroll lock while open.
//   * Esc closes (unless the primary action is busy).
//   * Backdrop click closes (configurable via `dismissOnBackdrop`).
//   * Focus the first focusable element on open; restore focus to the
//     invoker on close.
//   * Tab / Shift+Tab cycling is trapped inside the card.
//   * Action row renders primary + secondary buttons with ink / rust /
//     paper-edge variants and a busy state for async primary actions.
//
// Focus + dismiss behavior is sourced from the shared useFocusTrap and
// useDismissOn hooks; the same hooks back the four other modals in
// app/src/features/ so every dialog-shaped surface shares one a11y
// promise (Item C #131).
//
// Not in scope: portal rendering. The dialog appends itself to the normal
// React tree — z-index on the backdrop (3000) is high enough to sit above
// every other surface in the app.
//
// Accessibility:
//   * role="dialog", aria-modal="true".
//   * aria-labelledby points at the title.
//   * aria-describedby optional (wire-up by child via props if needed;
//     currently unused).

import {
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
  type ReactNode,
} from "react";

import "./dialogs.css";

import { useDismissOn } from "../../hooks/useDismissOn";
import { useFocusTrap } from "../../hooks/useFocusTrap";
import { t } from "../../i18n";

export type DialogPrimaryAction = {
  label: string;
  onClick: () => void | Promise<void>;
  disabled?: boolean;
  busy?: boolean;
  variant?: "default" | "danger";
};

export type DialogSecondaryAction = {
  label: string;
  onClick: () => void;
};

export type DialogProps = {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  primaryAction?: DialogPrimaryAction;
  secondaryAction?: DialogSecondaryAction;
  /** Default true. When false, backdrop clicks are ignored. */
  dismissOnBackdrop?: boolean;
  /** Max-width of the paper card in pixels. Default 480. */
  width?: number;
};

export function Dialog({
  open,
  onClose,
  title,
  children,
  primaryAction,
  secondaryAction,
  dismissOnBackdrop = true,
  width = 480,
}: DialogProps) {
  const cardRef = useRef<HTMLDivElement | null>(null);
  const titleId = useId();

  // `closing` drives the exit animation. We render the dialog DOM for a
  // brief window after `open` flips to false so the fade-out has time to
  // play, then unmount.
  const [mounted, setMounted] = useState<boolean>(open);
  const [closing, setClosing] = useState<boolean>(false);

  useEffect(() => {
    if (open) {
      setMounted(true);
      setClosing(false);
      return;
    }
    if (!mounted) return;
    // Detect reduced-motion — if set, skip the exit delay entirely.
    const reduced =
      typeof window !== "undefined" &&
      window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    if (reduced) {
      setMounted(false);
      setClosing(false);
      return;
    }
    setClosing(true);
    const timer = window.setTimeout(() => {
      setMounted(false);
      setClosing(false);
    }, 160);
    return () => window.clearTimeout(timer);
  }, [open, mounted]);

  // Lock body scroll while the dialog is mounted (including during the
  // exit animation).
  useEffect(() => {
    if (!mounted) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, [mounted]);

  const { onKeyDown: focusTrapKeyDown } = useFocusTrap(cardRef, {
    enabled: open,
  });

  // When the primary action is mid-flight, leave Esc alone — match the
  // pre-hook behavior of letting upstream listeners + browser defaults
  // see the keystroke. Trap stays armed so Tab cycling still works.
  useDismissOn({
    enabled: open && !primaryAction?.busy,
    onDismiss: onClose,
  });

  const handleBackdropPointerDown = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      if (!dismissOnBackdrop) return;
      if (primaryAction?.busy) return;
      if (e.target === e.currentTarget) onClose();
    },
    [dismissOnBackdrop, onClose, primaryAction?.busy],
  );

  const handlePrimaryClick = useCallback(() => {
    if (!primaryAction) return;
    if (primaryAction.disabled || primaryAction.busy) return;
    // Callers manage the busy flag via state, so we just invoke. A returned
    // Promise is allowed — we don't await it here; the caller controls the
    // busy/closed lifecycle itself.
    void primaryAction.onClick();
  }, [primaryAction]);

  if (!mounted) return null;

  const primaryVariant = primaryAction?.variant ?? "default";
  const primaryBtnClass =
    primaryVariant === "danger"
      ? "dlg__btn dlg__btn--danger"
      : "dlg__btn dlg__btn--primary";

  return (
    <div
      className="dlg__backdrop"
      data-state={closing ? "closing" : "open"}
      onPointerDown={handleBackdropPointerDown}
      role="presentation"
    >
      <div
        ref={cardRef}
        className="dlg__card"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        style={{ maxWidth: width }}
        onKeyDown={focusTrapKeyDown}
      >
        <header className="dlg__header">
          <h2 id={titleId} className="dlg__title">
            {title}
          </h2>
          <button
            type="button"
            className="dlg__close"
            onClick={onClose}
            aria-label={t("dialog.close_aria")}
            disabled={primaryAction?.busy}
          >
            {"×"}
          </button>
        </header>

        <div className="dlg__body">{children}</div>

        {(primaryAction || secondaryAction) && (
          <div className="dlg__actions">
            {secondaryAction && (
              <button
                type="button"
                className="dlg__btn dlg__btn--secondary"
                onClick={secondaryAction.onClick}
                disabled={primaryAction?.busy}
              >
                {secondaryAction.label}
              </button>
            )}
            {primaryAction && (
              <button
                type="button"
                className={
                  primaryBtnClass + (primaryAction.busy ? " dlg__btn--busy" : "")
                }
                onClick={handlePrimaryClick}
                disabled={primaryAction.disabled || primaryAction.busy}
                aria-busy={primaryAction.busy || undefined}
              >
                {primaryAction.busy && (
                  <span
                    className="dlg__btn-spinner"
                    aria-hidden="true"
                  />
                )}
                {primaryAction.label}
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
