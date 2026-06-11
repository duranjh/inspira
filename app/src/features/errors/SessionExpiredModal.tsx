// Inspira — session-expired modal.
//
// Modal overlay that surfaces when the user's auth session has
// expired. Two paths out: sign back in, or keep browsing as a guest.
// The tone is not alarmist — "signed out" is a neutral statement, and
// the italic-serif body explains in Inspira's voice that their session
// expired.
//
// Behavior notes:
//  - Fully controlled: parent owns `open` and both callbacks. When the
//    modal closes it's the parent's job to flip `open` back to false.
//  - Escape dismisses the modal (treated as "keep browsing as a
//    guest" — user declined the prompt).
//  - On open, the "Sign back in" button gets focus so the happy path
//    is a single Enter press for keyboard users.
//  - Click on the backdrop also dismisses. Click on the card body
//    does NOT (we stop propagation on the card itself).
//  - No focus trap — this is a blocking overlay with two choices and
//    no other focusable elements worth trapping. Tabbing cycles
//    through the two pills naturally.
//
// The animation (fade + gentle rise) is suppressed via the global
// `prefers-reduced-motion` rule in errors.css.

import { useEffect, useRef } from "react";
import type { JSX } from "react";

import { t } from "../../i18n";

import "./errors.css";

export interface SessionExpiredModalProps {
  /** Whether the modal is currently open. Fully controlled. */
  open: boolean;
  /** Fired when the "Sign back in" pill is clicked. */
  onSignIn: () => void;
  /**
   * Fired when the user dismisses the modal — either via the "Keep
   * browsing as a guest" pill, the Escape key, or a backdrop click.
   */
  onDismiss: () => void;
}

export function SessionExpiredModal({
  open,
  onSignIn,
  onDismiss,
}: SessionExpiredModalProps): JSX.Element | null {
  const signInRef = useRef<HTMLButtonElement | null>(null);

  // Focus the primary action on open. useEffect (not useLayoutEffect)
  // is fine — the modal is animated in, the focus lands a tick later
  // which is imperceptible to the user but ensures the element is
  // actually mounted and focusable.
  useEffect(() => {
    if (!open) return;
    signInRef.current?.focus();
  }, [open]);

  // Escape-to-dismiss. Attached only while open so we don't leave a
  // listener on the window for a hidden modal.
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent): void => {
      if (event.key === "Escape") {
        event.preventDefault();
        onDismiss();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
    };
  }, [open, onDismiss]);

  if (!open) return null;

  return (
    <div
      className="session-expired"
      role="dialog"
      aria-modal="true"
      aria-labelledby="session-expired-heading"
      aria-describedby="session-expired-body"
      onClick={onDismiss}
    >
      <div
        className="session-expired__card"
        onClick={(event) => event.stopPropagation()}
      >
        <h1 id="session-expired-heading" className="session-expired__heading">
          {t("error.session_expired_title")}
        </h1>
        <p id="session-expired-body" className="session-expired__body">
          <em>{t("error.session_expired_body")}</em>
        </p>
        <div className="session-expired__actions">
          <button
            ref={signInRef}
            type="button"
            className="error-page__pill error-page__pill--primary"
            onClick={onSignIn}
          >
            {t("error.session_expired_sign_in")}
          </button>
          <button
            type="button"
            className="error-page__pill error-page__pill--secondary"
            onClick={onDismiss}
          >
            {t("error.session_expired_keep_guest")}
          </button>
        </div>
      </div>
    </div>
  );
}

export default SessionExpiredModal;
