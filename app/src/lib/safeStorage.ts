// Thin wrapper around localStorage that swallows exceptions.
//
// Raw `localStorage.getItem` / `setItem` can throw:
//   - Safari in private-browsing mode (quota-exceeded on write, rare read errors)
//   - Hardened Firefox / privacy-mode browsers (security error on any access)
//   - Embedded WebViews where storage is explicitly disabled
//   - Quota-exceeded in normal browsing when the tab's bucket is full
//
// Callers that use the raw API need a try/catch wrapper every time, and most
// call sites forget — a single SecurityError from a strict browser setting
// then blows up the entire React tree. This helper centralizes the guard so
// feature code can stay short and read-only by default.
//
// All reads return `null` on failure (same shape as a missing key). All
// writes return a boolean so callers can surface a toast or degrade
// gracefully when a quota is hit. Removes never throw.
//
// The JSON helpers (`getJSON` / `setJSON`) add a typed layer on top so
// callers can skip parse/stringify boilerplate. A malformed stored value
// is treated the same as a missing one — returns `null` rather than
// throwing a SyntaxError that would bubble up into a render.

export const safeStorage = {
  getItem(key: string): string | null {
    try {
      return localStorage.getItem(key);
    } catch {
      return null;
    }
  },
  setItem(key: string, value: string): boolean {
    try {
      localStorage.setItem(key, value);
      return true;
    } catch {
      return false;
    }
  },
  removeItem(key: string): void {
    try {
      localStorage.removeItem(key);
    } catch {
      /* swallowed */
    }
  },
  getJSON<T>(key: string): T | null {
    const raw = this.getItem(key);
    if (raw == null) return null;
    try {
      return JSON.parse(raw) as T;
    } catch {
      return null;
    }
  },
  setJSON(key: string, value: unknown): boolean {
    try {
      return this.setItem(key, JSON.stringify(value));
    } catch {
      return false;
    }
  },
};
