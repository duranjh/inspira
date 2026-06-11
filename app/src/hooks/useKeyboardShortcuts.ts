// Inspira — global keyboard shortcut layer.
//
// Lets any component register a set of key bindings for the lifetime of
// its mount via `useKeyboardShortcuts(bindings)`. Internally there's a
// module-level registry so `listRegisteredShortcuts()` can feed the help
// overlay a live roster of everything currently wired up.
//
// Behavior notes:
//  - Bindings are matched on a normalized "combo" string (case-insensitive
//    for letter keys). Modifier prefixes recognized: `Ctrl`, `Cmd`, `Meta`,
//    `Alt`, `Shift`, `Mod` (alias: Cmd on mac, Ctrl elsewhere).
//  - If the user is typing in an `<input>`, `<textarea>`, or anything with
//    `contenteditable`, letter-key bindings (those with `disabledWhenTyping`
//    — default true) are skipped. Modifier combos (Ctrl/Cmd+X) and special
//    keys (Esc, arrows) still fire.
//  - The help overlay's `?` binding needs to work even when no shift is
//    held on some keyboards — we match by `event.key === "?"` when the
//    combo is literally `?`, so layout-specific shift mapping doesn't get
//    in the way.
//
// Intentionally no new dependencies. All listeners attach to `window` in
// the capture phase so in-component stopPropagation calls (e.g. React Flow's
// own keydown handlers for Delete/Backspace) can't silently swallow us.

import { useEffect, useMemo } from "react";

export type ShortcutHandler = (event: KeyboardEvent) => void;

export interface ShortcutBinding {
  /**
   * The combo string used for matching AND for display in the help overlay.
   * Examples: "?", "Esc", "n", "/", "Ctrl+K", "Cmd+K", "Mod+K".
   * Parts are joined by "+". Order of modifiers doesn't matter.
   */
  combo: string;
  /** Human-readable description shown in the help overlay. */
  description: string;
  /** Grouping label shown in the help overlay (e.g. "Global", "Canvas"). */
  group?: string;
  /**
   * When true (default for non-modifier letter/punct keys), the binding
   * is suppressed while the user is typing in an input/textarea/editable.
   * Modifier combos default to false — typing Ctrl+K should still open
   * the palette even from a text field.
   */
  disabledWhenTyping?: boolean;
  handler: ShortcutHandler;
}

// ---- Module-level registry ------------------------------------------------
//
// Every active binding lives in this Set for the duration of its owner
// component's mount. We expose a snapshot via `listRegisteredShortcuts()`
// so the help overlay can render a cheat sheet without the registering
// component having to pipe the list manually.

const registry: Set<ShortcutBinding> = new Set();

export function listRegisteredShortcuts(): ShortcutBinding[] {
  return Array.from(registry);
}

// ---- Combo parsing + matching --------------------------------------------

interface ParsedCombo {
  /** Normalized key (single character lowercased, or named key like "Escape"). */
  key: string;
  ctrl: boolean;
  meta: boolean;
  alt: boolean;
  shift: boolean;
  /** True for "Mod+..." — matches Meta on mac, Ctrl elsewhere. */
  mod: boolean;
}

const IS_MAC =
  typeof navigator !== "undefined" &&
  /Mac|iPod|iPhone|iPad/.test(navigator.platform);

/**
 * Normalize the key portion of a combo string. Letters are lowercased;
 * named keys are mapped to their KeyboardEvent.key canonical form.
 */
function normalizeKey(raw: string): string {
  const k = raw.trim();
  if (k.length === 1) return k.toLowerCase();
  const lower = k.toLowerCase();
  switch (lower) {
    case "esc":
    case "escape":
      return "escape";
    case "space":
    case "spacebar":
      return " ";
    case "enter":
    case "return":
      return "enter";
    case "tab":
      return "tab";
    case "up":
      return "arrowup";
    case "down":
      return "arrowdown";
    case "left":
      return "arrowleft";
    case "right":
      return "arrowright";
    default:
      return lower;
  }
}

function parseCombo(combo: string): ParsedCombo {
  const parts = combo.split("+").map((p) => p.trim()).filter(Boolean);
  const parsed: ParsedCombo = {
    key: "",
    ctrl: false,
    meta: false,
    alt: false,
    shift: false,
    mod: false,
  };
  for (const part of parts) {
    const lower = part.toLowerCase();
    if (lower === "ctrl" || lower === "control") parsed.ctrl = true;
    else if (lower === "cmd" || lower === "meta" || lower === "command" || lower === "win")
      parsed.meta = true;
    else if (lower === "alt" || lower === "option" || lower === "opt")
      parsed.alt = true;
    else if (lower === "shift") parsed.shift = true;
    else if (lower === "mod") parsed.mod = true;
    else parsed.key = normalizeKey(part);
  }
  return parsed;
}

function eventMatchesCombo(event: KeyboardEvent, combo: ParsedCombo): boolean {
  // Key match — case-insensitive, and for literal "?" we match on the
  // KeyboardEvent.key directly (which is already "?" when Shift+/ is
  // pressed on a US layout; some layouts produce it without shift).
  const eventKey = event.key.length === 1 ? event.key.toLowerCase() : event.key.toLowerCase();
  // Punctuation special-cases: "?" reads as "?" in event.key regardless
  // of shift state on most layouts — use direct equality without lowering.
  const keyMatch =
    combo.key === "?" ? event.key === "?" : eventKey === combo.key;
  if (!keyMatch) return false;

  // Modifier match. "Mod" expands to Meta on mac, Ctrl elsewhere. Any
  // required modifier must be held; modifiers NOT in the combo must NOT
  // be held, with the exception of Shift when the printed character
  // inherently requires it (e.g. "?" on US).
  const needCtrl = combo.ctrl || (combo.mod && !IS_MAC);
  const needMeta = combo.meta || (combo.mod && IS_MAC);
  const needAlt = combo.alt;
  const needShift = combo.shift;

  if (needCtrl !== event.ctrlKey) return false;
  if (needMeta !== event.metaKey) return false;
  if (needAlt !== event.altKey) return false;
  // For "?" we don't enforce shift state — it's layout-dependent.
  if (combo.key !== "?" && needShift !== event.shiftKey) return false;

  return true;
}

// ---- Input-focus detection -----------------------------------------------

function isTypingTarget(el: Element | null): boolean {
  if (!el) return false;
  const tag = el.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return true;
  const editable = (el as HTMLElement).isContentEditable;
  return Boolean(editable);
}

/**
 * A letter/punct key that types a character when the user is in a text
 * field should default to `disabledWhenTyping = true`. Modifier combos
 * (Ctrl+K, Cmd+K) and named non-printing keys (Escape) default to false.
 */
function defaultDisabledWhenTyping(parsed: ParsedCombo): boolean {
  if (parsed.ctrl || parsed.meta || parsed.alt || parsed.mod) return false;
  if (parsed.key === "escape") return false;
  // Single printable character or "?" should be suppressed while typing.
  return parsed.key.length === 1 || parsed.key === "?";
}

// ---- Pure matcher factory -------------------------------------------------
//
// Extracted from the hook body so the test harness can drive the match
// logic without spinning up a React renderer. The factory does three
// things: pre-parse combos, build a typing-target resolver, and return a
// keydown listener. The hook glues this to `window.addEventListener`.

export interface CreateMatcherOptions {
  /**
   * Override the "what element is currently focused" lookup. Tests pass
   * a fake element here; production passes the default which reads
   * `document.activeElement`.
   */
  getActiveElement?: () => Element | null;
}

export function createShortcutsKeydownListener(
  bindings: ShortcutBinding[],
  options: CreateMatcherOptions = {},
): (event: KeyboardEvent) => void {
  const parsed = bindings.map((b) => ({
    binding: b,
    combo: parseCombo(b.combo),
  }));
  const getActiveElement =
    options.getActiveElement ??
    (() =>
      typeof document === "undefined" ? null : document.activeElement);

  return (event: KeyboardEvent) => {
    const typing = isTypingTarget(getActiveElement());
    for (const { binding, combo } of parsed) {
      const disabledWhenTyping =
        binding.disabledWhenTyping ?? defaultDisabledWhenTyping(combo);
      if (typing && disabledWhenTyping) continue;
      if (!eventMatchesCombo(event, combo)) continue;
      binding.handler(event);
      // Stop here — one event, one matched binding. If multiple bindings
      // wanted the same combo, the one registered first wins, which
      // matches dev expectation (the component mounted first owns it).
      break;
    }
  };
}

// ---- The hook -------------------------------------------------------------

export function useKeyboardShortcuts(bindings: ShortcutBinding[]): void {
  useEffect(() => {
    if (bindings.length === 0) return;

    // Register for the help-overlay enumeration.
    for (const b of bindings) registry.add(b);

    const onKeyDown = createShortcutsKeydownListener(bindings);

    // Capture phase so React Flow / other nested keydown handlers that
    // call stopPropagation can't hide shortcuts from us.
    window.addEventListener("keydown", onKeyDown, true);
    return () => {
      window.removeEventListener("keydown", onKeyDown, true);
      for (const b of bindings) registry.delete(b);
    };
  }, [bindings]);
}

// ---- Single-binding convenience hook -------------------------------------
//
// Components that only want to register one shortcut shouldn't have to
// build a one-element array by hand. `useShortcut` wraps that boilerplate.
// The hook memoizes the binding array so re-renders that don't change the
// inputs don't re-register.

export interface UseShortcutOptions {
  description: string;
  group?: string;
  disabledWhenTyping?: boolean;
}

export function useShortcut(
  combo: string,
  handler: ShortcutHandler,
  options: UseShortcutOptions,
): void {
  const bindings = useMemo<ShortcutBinding[]>(
    () => [
      {
        combo,
        handler,
        description: options.description,
        group: options.group,
        disabledWhenTyping: options.disabledWhenTyping,
      },
    ],
    // handler/options identity is the caller's responsibility — same rule
    // as useEffect. Listing every field explicitly keeps the deps lint-
    // friendly without forcing the caller to wrap everything in useMemo.
    [
      combo,
      handler,
      options.description,
      options.group,
      options.disabledWhenTyping,
    ],
  );
  useKeyboardShortcuts(bindings);
}

// ---- Custom-event names the provider dispatches --------------------------
//
// Centralized here so consumers (ProjectCanvas, InspiraApp, future agents)
// can import the exact string and not drift from the dispatcher. All
// events are fired on `window`.
//
// `canvas-focus-move` carries a `{ direction }` detail payload; the others
// are signal-only. ProjectCanvas / InspiraApp already listen for
// `inspira:canvas-tidy` and `inspira:export-request` using this same
// pattern — we extend it rather than inventing a parallel channel.

export const SHORTCUT_EVENTS = {
  SAVE_PRESSED: "inspira:save-pressed",
  EXPORT_REQUEST: "inspira:export-request",
  SHARE_REQUEST: "inspira:share-request",
  TOPIC_DUPLICATE_SELECTED: "inspira:topic-duplicate-selected",
  CANVAS_FOCUS_MOVE: "inspira:canvas-focus-move",
  COMMAND_PALETTE_OPEN: "inspira:command-palette-open",
  NEW_PROJECT_REQUEST: "inspira:new-project-request",
  SEARCH_OPEN: "inspira:search-open",
} as const;

export type ShortcutEventName =
  (typeof SHORTCUT_EVENTS)[keyof typeof SHORTCUT_EVENTS];

export type CanvasFocusDirection = "up" | "down" | "left" | "right";

export interface CanvasFocusMoveDetail {
  direction: CanvasFocusDirection;
}
