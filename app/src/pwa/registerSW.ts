/**
 * Inspira service-worker registration.
 *
 * Registered in production only (dev serves unbuilt ES modules that the SW
 * runtime can't meaningfully cache). Wires an optional `onUpdateAvailable`
 * callback so a future toast in InspiraApp can say
 * "New version ready — reload to apply."
 */

export type RegisterSWOptions = {
  /** Called when a new service worker is installed and waiting to activate. */
  onUpdateAvailable?: (reg: ServiceWorkerRegistration) => void;
  /** Called the first time an SW controls the page (fresh install). */
  onReady?: (reg: ServiceWorkerRegistration) => void;
  /** Called if registration fails. */
  onError?: (err: unknown) => void;
};

/**
 * Ask a waiting service worker to activate immediately.
 * Safe to call with `null` (no-op).
 */
export function skipWaiting(reg: ServiceWorkerRegistration | null): void {
  reg?.waiting?.postMessage({ type: "SKIP_WAITING" });
}

export function registerSW(options: RegisterSWOptions = {}): void {
  if (typeof window === "undefined") return;
  if (!("serviceWorker" in navigator)) return;

  // Dev gate: never register a service worker against the Vite dev server.
  // import.meta.env.PROD is injected by Vite at build time.
  if (!import.meta.env.PROD) return;

  window.addEventListener("load", () => {
    navigator.serviceWorker
      .register("/sw.js", { scope: "/" })
      .then((reg) => {
        // A worker is already waiting from a previous session.
        if (reg.waiting && navigator.serviceWorker.controller) {
          options.onUpdateAvailable?.(reg);
        }

        // New install in progress — watch state transitions.
        reg.addEventListener("updatefound", () => {
          const installing = reg.installing;
          if (!installing) return;
          installing.addEventListener("statechange", () => {
            if (installing.state !== "installed") return;
            if (navigator.serviceWorker.controller) {
              // Old worker was controlling — this one is an update.
              options.onUpdateAvailable?.(reg);
            } else {
              // Nothing was controlling — this is the first install.
              options.onReady?.(reg);
            }
          });
        });
      })
      .catch((err) => {
        options.onError?.(err);
      });

    // Reload once when a new worker takes control, so the page runs the
    // newest bundle it just cached.
    let reloading = false;
    navigator.serviceWorker.addEventListener("controllerchange", () => {
      if (reloading) return;
      reloading = true;
      window.location.reload();
    });
  });
}
