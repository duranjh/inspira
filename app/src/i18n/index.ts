// Inspira — tiny in-house i18n shim.
//
// Goals
// -----
// * Zero runtime dependencies (no i18next, no FormatJS). A translator only
//   ever needs to edit a JSON bundle in `locales/`. The plumbing stays
//   out of their way.
// * `t(key, params?)` lookup with `{placeholder}` interpolation. Unknown
//   keys fall through to the `en` bundle, then to the key itself — so a
//   half-translated language never breaks the UI, it just shows English.
// * `useLocale()` hook returns `[locale, setLocale]` like `useState`,
//   backed by a React context so every `t()` call in the tree re-renders
//   when the active locale flips.
// * Active locale persists in `localStorage["inspira_locale"]`. On first
//   load we auto-detect from `navigator.language` if we ship a bundle for
//   it; otherwise we default to English.
//
// Adding a new language
// ---------------------
// 1. Drop a `<code>.json` file in `locales/` with the same keys as
//    `en.json` (see `locales/README.md`).
// 2. Add `<code>` to the `BUNDLES` map below, importing the JSON
//    statically so Vite bundles it with the app.
// 3. That's it — `setLocale("<code>")` will now work.
//
// Static imports keep things synchronous and type-safe; at this app's
// size the payload of 3-4 small JSON bundles is trivial. If we ever want
// lazy loading, swap `BUNDLES` to async-dynamic imports and gate
// `setLocale` on resolution.

import {
  createContext,
  createElement,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

export {
  formatDate,
  formatDateTime,
  formatRelativeTime,
  formatNumber,
} from "./format";

import deDict from "./locales/de.json";
import enDict from "./locales/en.json";
import esDict from "./locales/es.json";
import frDict from "./locales/fr.json";
import itDict from "./locales/it.json";
import jaDict from "./locales/ja.json";
import nlDict from "./locales/nl.json";
import plDict from "./locales/pl.json";
import ptDict from "./locales/pt.json";

// ---------------------------------------------------------------------------
// Types + registry
// ---------------------------------------------------------------------------

export type Dict = Record<string, string>;

/**
 * All locales shipped with the app. Adding a new language = one entry here
 * plus a JSON file. The codes match BCP-47 primary-language subtags so
 * auto-detection from `navigator.language` works by prefix match.
 *
 * Ordered roughly by speaker count so the LocalePicker dropdown reads
 * in a predictable sequence for most visitors; English stays first
 * because it's the source of truth for fallbacks.
 */
const BUNDLES: Record<string, Dict> = {
  en: enDict as Dict,
  es: esDict as Dict,
  pt: ptDict as Dict,
  fr: frDict as Dict,
  de: deDict as Dict,
  it: itDict as Dict,
  nl: nlDict as Dict,
  pl: plDict as Dict,
  ja: jaDict as Dict,
};

const STORAGE_KEY = "inspira_locale";
const DEFAULT_LOCALE = "en";

/** BCP-47 codes we ship translations for. */
export type LocaleCode = keyof typeof BUNDLES & string;

export function availableLocales(): LocaleCode[] {
  return Object.keys(BUNDLES) as LocaleCode[];
}

// ---------------------------------------------------------------------------
// Active-locale state (module-scoped so `t()` can be called outside React)
// ---------------------------------------------------------------------------

let currentLocale: string = DEFAULT_LOCALE;

/**
 * Subscribers for locale change. The React context layers on top of this,
 * but exposing a raw subscribe means non-React code (e.g. imperative
 * helpers, future Tauri IPC wrappers) can still react.
 */
const listeners = new Set<(locale: string) => void>();

function notify(locale: string): void {
  for (const fn of listeners) fn(locale);
}

/**
 * Swap the active locale. Persists to localStorage. If the requested
 * locale isn't in BUNDLES we fall back silently to English — safer than
 * throwing from a UI event handler.
 */
export function setLocale(code: string): void {
  const next = code in BUNDLES ? code : DEFAULT_LOCALE;
  if (next === currentLocale) return;
  currentLocale = next;
  if (typeof window !== "undefined") {
    try {
      window.localStorage.setItem(STORAGE_KEY, next);
    } catch {
      /* storage disabled — in-memory change still applies */
    }
  }
  notify(next);
}

export function getLocale(): string {
  return currentLocale;
}

// ---------------------------------------------------------------------------
// Auto-detect on first import.
// ---------------------------------------------------------------------------
//
// Priority: saved preference > navigator.language prefix > English.
// Wrapped in a try so a bad localStorage (incognito, disabled, SSR) never
// throws at module init.

(function detectInitialLocale(): void {
  if (typeof window === "undefined") return;
  try {
    const saved = window.localStorage.getItem(STORAGE_KEY);
    if (saved && saved in BUNDLES) {
      currentLocale = saved;
      return;
    }
  } catch {
    /* storage disabled — fall through to navigator detection */
  }
  try {
    const nav = window.navigator.language || DEFAULT_LOCALE;
    // BCP-47 is `<lang>-<region>`; we match on the primary subtag so
    // `fr-CA` falls through to `fr`. We don't ship region-specific
    // bundles yet.
    const primary = nav.toLowerCase().split("-")[0];
    if (primary in BUNDLES) {
      currentLocale = primary;
    }
  } catch {
    /* navigator inaccessible — keep default */
  }
})();

// ---------------------------------------------------------------------------
// interpolate — tiny string formatter.
// ---------------------------------------------------------------------------
//
// Replaces every `{name}` in `template` with the matching entry from
// `params`. Missing params render as `{name}` unchanged so a translator
// who accidentally drops a placeholder sees the breakage in the UI rather
// than silently losing data.

function interpolate(
  template: string,
  params?: Record<string, string | number>,
): string {
  if (!params) return template;
  return template.replace(/\{(\w+)\}/g, (_, key: string) => {
    const value = params[key];
    return value === undefined || value === null ? `{${key}}` : String(value);
  });
}

// ---------------------------------------------------------------------------
// t() — the one function every component calls.
// ---------------------------------------------------------------------------
//
// Lookup order:
//   1. Active locale's dictionary.
//   2. English dictionary (for missing keys in a partial translation).
//   3. The key itself (last-resort so a missing key is visually obvious
//      in dev but not a crash).
//
// Params are stringified via `String(value)`. Numbers stay raw — we
// don't do locale-aware number formatting here; if a component needs
// that it should reach for Intl.NumberFormat directly.

export function t(
  key: string,
  params?: Record<string, string | number>,
): string {
  const dict = BUNDLES[currentLocale] ?? BUNDLES[DEFAULT_LOCALE] ?? {};
  const fallback = BUNDLES[DEFAULT_LOCALE] ?? {};
  const raw = dict[key] ?? fallback[key] ?? key;
  return interpolate(raw, params);
}

// ---------------------------------------------------------------------------
// React bindings — context + hook so components re-render on locale swap.
// ---------------------------------------------------------------------------

type LocaleContextValue = {
  locale: string;
  setLocale: (code: string) => void;
};

const LocaleContext = createContext<LocaleContextValue>({
  locale: DEFAULT_LOCALE,
  setLocale,
});

/**
 * Optional provider — mount once near the app root. Without it,
 * `useLocale()` still works (falls back to the module-scoped state), but
 * components won't re-render on swap. Mounting the provider is the
 * recommended setup once we actually ship a locale switcher.
 */
export function LocaleProvider({ children }: { children: ReactNode }) {
  const [locale, setLocaleState] = useState<string>(() => currentLocale);

  useEffect(() => {
    const onChange = (next: string) => setLocaleState(next);
    listeners.add(onChange);
    return () => {
      listeners.delete(onChange);
    };
  }, []);

  // Mirror the active locale onto <html lang="…"> so:
  //  1. Screen readers / assistive tech announce the correct language.
  //  2. The CJK line-height rule in App.css (`html[lang="ja"]`) fires
  //     when Japanese is active, without components having to know.
  //  3. Browser-native spellcheck honors the locale.
  // Runs on mount + every locale swap.
  useEffect(() => {
    if (typeof document !== "undefined") {
      document.documentElement.setAttribute("lang", locale);
    }
  }, [locale]);

  const update = useCallback((code: string) => {
    setLocale(code);
  }, []);

  const value = useMemo<LocaleContextValue>(
    () => ({ locale, setLocale: update }),
    [locale, update],
  );

  return createElement(LocaleContext.Provider, { value }, children);
}

/**
 * Hook mirror of `useState`. Returns the currently-active locale plus a
 * setter. Components that only need `t()` do NOT need to call this —
 * `t()` reads module-scope state — but calling it ensures re-renders on
 * locale change (e.g. a language-picker component).
 */
export function useLocale(): [string, (code: string) => void] {
  const ctx = useContext(LocaleContext);
  return [ctx.locale, ctx.setLocale];
}
