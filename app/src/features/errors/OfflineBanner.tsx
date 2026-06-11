// Inspira — offline ribbon.
//
// A slim warm-gold band that pins itself to the top of the viewport
// when the browser reports that the connection has dropped. The copy
// is reassuring (changes will try to save when we're back online) and
// dismissable for the session.
//
// The component is fully self-contained: it owns its own state via
// the `useOnlineStatus` hook and a dismiss flag, so mounting it once
// at the app shell is all that's needed. When connectivity returns
// we reset the dismiss flag so the next outage surfaces again.
//
// Renders null when:
//  - the user is online, OR
//  - they've dismissed this outage for the session.
//
// No props — the banner is reactive to the environment, not to parent
// state. If a future screen wants to suppress it globally, gate the
// render site instead.

import { useEffect, useState } from "react";
import type { JSX } from "react";

import { useOnlineStatus } from "../../hooks/useOnlineStatus";
import { t } from "../../i18n";

import "./errors.css";

export function OfflineBanner(): JSX.Element | null {
  const online = useOnlineStatus();
  const [dismissed, setDismissed] = useState<boolean>(false);

  // When connection returns, reset the dismiss flag so the next outage
  // gets a fresh banner. (Auto-hide behavior: if the user never
  // dismissed it, the !online check below hides it on reconnect
  // anyway; this just ensures a subsequent drop isn't pre-dismissed.)
  useEffect(() => {
    if (online && dismissed) {
      setDismissed(false);
    }
  }, [online, dismissed]);

  if (online) return null;
  if (dismissed) return null;

  return (
    <div
      className="offline-banner"
      role="status"
      aria-live="polite"
      aria-atomic="true"
    >
      <p className="offline-banner__message">
        <em>{t("offline.banner")}</em>
      </p>
      <button
        type="button"
        className="offline-banner__dismiss"
        aria-label={t("offline.dismiss_aria")}
        onClick={() => setDismissed(true)}
      >
        &times;
      </button>
    </div>
  );
}

export default OfflineBanner;
