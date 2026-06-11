// Inspira — online-status hook.
//
// Returns a boolean that tracks `navigator.onLine` and the window
// `online`/`offline` events. Used by OfflineBanner (and anything else
// that wants to react to connectivity changes) to render "you're
// offline" UI without each consumer wiring the listeners itself.
//
// Notes:
//  - SSR-safe: if `window` or `navigator` isn't defined at import time
//    or on the first render, we default to `true` (online). The client
//    effect then reconciles with the real state on mount.
//  - `navigator.onLine` is a best-effort signal from the browser — it
//    tells us when the OS thinks we're offline, not when our API
//    specifically is unreachable. Good enough for a banner.
//  - Listeners attach to `window`, and we clean them up on unmount.
//
// Intentionally no new dependencies.
//
// Usage:
//   const online = useOnlineStatus();
//   if (!online) return <OfflineBanner />;

import { useEffect, useState } from "react";

function getInitialStatus(): boolean {
  if (typeof navigator === "undefined") return true;
  // `navigator.onLine` is defined on every modern browser; guard anyway
  // because some exotic embedded runtimes (CI, tests) don't set it.
  return typeof navigator.onLine === "boolean" ? navigator.onLine : true;
}

export function useOnlineStatus(): boolean {
  const [online, setOnline] = useState<boolean>(getInitialStatus);

  useEffect(() => {
    if (typeof window === "undefined") return;

    const handleOnline = (): void => setOnline(true);
    const handleOffline = (): void => setOnline(false);

    // Reconcile once on mount in case the status changed between
    // module-init and effect-run (common on slow hydration).
    setOnline(getInitialStatus());

    window.addEventListener("online", handleOnline);
    window.addEventListener("offline", handleOffline);

    return () => {
      window.removeEventListener("online", handleOnline);
      window.removeEventListener("offline", handleOffline);
    };
  }, []);

  return online;
}
