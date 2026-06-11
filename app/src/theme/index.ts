// Inspira — theme module.
//
// Manages two independent axes:
//   style — "bookworm" (warm editorial, default) | "modern" (clean, precise)
//   mode  — "light" | "dark" | "system"
//
// Architecture mirrors the i18n module: module-scoped state + React context
// layer on top. The setters write `data-style` and `data-theme` attributes
// directly onto <html>, matching the CSS selectors in App.css.
//
// Storage keys:
//   inspira_theme_style  — "bookworm" | "modern"
//   inspira_theme_mode   — "light" | "dark" | "system"
//   inspira_theme        — legacy key written by ThemeSection; we read it
//                          as an initial-mode fallback so existing users keep
//                          their saved light/dark preference.

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

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type ThemeStyle = "bookworm" | "modern";
export type ThemeMode = "light" | "dark" | "system";

type ThemeContextValue = {
  style: ThemeStyle;
  mode: ThemeMode;
  setStyle: (s: ThemeStyle) => void;
  setMode: (m: ThemeMode) => void;
};

// ---------------------------------------------------------------------------
// Storage keys
// ---------------------------------------------------------------------------

const STYLE_KEY = "inspira_theme_style";
const MODE_KEY = "inspira_theme_mode";
const LEGACY_MODE_KEY = "inspira_theme";

// ---------------------------------------------------------------------------
// Module-scoped state (allows non-React usage)
// ---------------------------------------------------------------------------

let currentStyle: ThemeStyle = "bookworm";
// Default mode: "light". Previously "system" which caused first-time
// visitors on dark-mode phones to land on a dark Inspira before they'd
// seen the brand. The default aesthetic is Bookworm Light (cream paper
// + warm serif); shipping that to every unauthenticated visitor is a
// better first impression. Users who explicitly set "system" from
// Account Settings still keep that preference — we only override the
// initial default, not a saved one.
let currentMode: ThemeMode = "light";

const styleListeners = new Set<(s: ThemeStyle) => void>();
const modeListeners = new Set<(m: ThemeMode) => void>();

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function safeLocalGet(key: string): string | null {
  try {
    return typeof window !== "undefined"
      ? window.localStorage.getItem(key)
      : null;
  } catch {
    return null;
  }
}

function safeLocalSet(key: string, value: string): void {
  try {
    if (typeof window !== "undefined") {
      window.localStorage.setItem(key, value);
    }
  } catch {
    /* storage disabled — in-memory state still applies */
  }
}

function resolveEffectiveMode(mode: ThemeMode): "light" | "dark" {
  if (mode === "light" || mode === "dark") return mode;
  if (typeof window !== "undefined" && typeof window.matchMedia === "function") {
    return window.matchMedia("(prefers-color-scheme: dark)").matches
      ? "dark"
      : "light";
  }
  return "light";
}

function applyToDOM(style: ThemeStyle, mode: ThemeMode): void {
  if (typeof document === "undefined") return;
  const root = document.documentElement;
  // Style axis
  root.setAttribute("data-style", style);
  // Mode axis — always write `data-theme="light" | "dark"` based on the
  // resolved mode rather than leaving the attribute absent for light.
  // The old "remove when light" branch forced every CSS rule wanting to
  // target light-mode-specifically to use `:root:not([data-theme=dark])`
  // or similar negation selectors, which were brittle (e.g. server-side
  // hydration flashes where the attribute was briefly missing rendered
  // as dark-styled-on-light). Writing both values explicitly makes the
  // style selector `:root[data-theme=light]` viable and removes a
  // category of flash-of-wrong-theme bugs. Dark rules using
  // `[data-theme=dark]` keep working unchanged.
  const effective = resolveEffectiveMode(mode);
  root.setAttribute("data-theme", effective);
}

// ---------------------------------------------------------------------------
// Auto-detect on first import
// ---------------------------------------------------------------------------

(function detectInitialTheme(): void {
  if (typeof window === "undefined") return;

  // Style
  const savedStyle = safeLocalGet(STYLE_KEY);
  if (savedStyle === "bookworm" || savedStyle === "modern") {
    currentStyle = savedStyle;
  }

  // Mode — prefer new key, fall back to legacy key written by ThemeSection
  const savedMode = safeLocalGet(MODE_KEY);
  if (savedMode === "light" || savedMode === "dark" || savedMode === "system") {
    currentMode = savedMode;
  } else {
    const legacyMode = safeLocalGet(LEGACY_MODE_KEY);
    if (legacyMode === "light" || legacyMode === "dark" || legacyMode === "system") {
      currentMode = legacyMode as ThemeMode;
    }
    // Else default stays "system"
  }

  applyToDOM(currentStyle, currentMode);
})();

// ---------------------------------------------------------------------------
// Public getters / setters
// ---------------------------------------------------------------------------

export function getThemeStyle(): ThemeStyle {
  return currentStyle;
}

export function getThemeMode(): ThemeMode {
  return currentMode;
}

export function setThemeStyle(style: ThemeStyle): void {
  if (style === currentStyle) return;
  currentStyle = style;
  safeLocalSet(STYLE_KEY, style);
  applyToDOM(currentStyle, currentMode);
  for (const fn of styleListeners) fn(style);
}

export function setThemeMode(mode: ThemeMode): void {
  if (mode === currentMode) return;
  currentMode = mode;
  safeLocalSet(MODE_KEY, mode);
  applyToDOM(currentStyle, currentMode);
  for (const fn of modeListeners) fn(mode);
}

// ---------------------------------------------------------------------------
// Media-query listener for "system" mode
// ---------------------------------------------------------------------------

(function attachSystemModeListener(): void {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
    return;
  }
  const mql = window.matchMedia("(prefers-color-scheme: dark)");
  const onChange = () => {
    // Only re-apply when currently in system mode
    if (currentMode === "system") {
      applyToDOM(currentStyle, currentMode);
    }
  };
  if (typeof mql.addEventListener === "function") {
    mql.addEventListener("change", onChange);
  } else if (typeof mql.addListener === "function") {
    // Safari <14 fallback
    (mql as MediaQueryList & { addListener: (fn: () => void) => void }).addListener(onChange);
  }
})();

// ---------------------------------------------------------------------------
// React context
// ---------------------------------------------------------------------------

const ThemeContext = createContext<ThemeContextValue>({
  style: currentStyle,
  mode: currentMode,
  setStyle: setThemeStyle,
  setMode: setThemeMode,
});

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [style, setStyleState] = useState<ThemeStyle>(() => currentStyle);
  const [mode, setModeState] = useState<ThemeMode>(() => currentMode);

  // Subscribe to module-scope changes (e.g. from non-React code or other
  // provider instances — there should only ever be one, but keep it safe).
  useEffect(() => {
    const onStyle = (s: ThemeStyle) => setStyleState(s);
    const onMode = (m: ThemeMode) => setModeState(m);
    styleListeners.add(onStyle);
    modeListeners.add(onMode);
    return () => {
      styleListeners.delete(onStyle);
      modeListeners.delete(onMode);
    };
  }, []);

  const handleSetStyle = useCallback((s: ThemeStyle) => {
    setThemeStyle(s);
  }, []);

  const handleSetMode = useCallback((m: ThemeMode) => {
    setThemeMode(m);
  }, []);

  const value = useMemo<ThemeContextValue>(
    () => ({ style, mode, setStyle: handleSetStyle, setMode: handleSetMode }),
    [style, mode, handleSetStyle, handleSetMode],
  );

  return createElement(ThemeContext.Provider, { value }, children);
}

export function useTheme(): ThemeContextValue {
  return useContext(ThemeContext);
}
