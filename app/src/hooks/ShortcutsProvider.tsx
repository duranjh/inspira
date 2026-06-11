// Inspira — provider that registers the "extra" app-level shortcuts.
//
// Intentionally NOT mounted into the tree by this file. The parent
// commit session wires it in at the InspiraApp root once the contention
// on that file settles. Until then the provider exists as a drop-in:
// mount once, shortcuts are live app-wide.
//
// Responsibility split:
//   - InspiraApp already owns: `?`, `Esc`, `Mod+K`, `n`, `t`, `/`, and
//     the Esc-closes-topic-detail advertisement row. Those bindings also
//     directly drive local state (opening help overlay, kicking off a
//     new project, etc.) so they have to live in the same component as
//     the state.
//   - Everything else — save-intercept, export dialog, share dialog,
//     duplicate-selected-topic, arrow-key canvas navigation — only
//     needs to shout a custom event onto `window` and let the existing
//     feature code pick it up (the same pattern as `inspira:canvas-tidy`
//     and `inspira:export-request`). That's what this provider does.
//
// The provider has zero rendered output. It's a state-less shim around
// `useKeyboardShortcuts`. Mount anywhere inside the authenticated tree
// — once — and it participates in the `listRegisteredShortcuts()`
// snapshot so the help overlay auto-lists every binding.
//
// To install (for the parent session):
//
//     import { ShortcutsProvider } from "../../hooks/ShortcutsProvider";
//     // ... inside the authenticated render:
//     <ShortcutsProvider />
//
// No props. Safe to mount at the root; harmless if mounted twice (each
// instance registers its own bindings, the first handler wins because
// useKeyboardShortcuts breaks on the first match).

import { useMemo } from "react";

import { toast } from "../components/ToastProvider";
import { t } from "../i18n";
import {
  useKeyboardShortcuts,
  SHORTCUT_EVENTS,
  type CanvasFocusDirection,
  type CanvasFocusMoveDetail,
  type ShortcutBinding,
} from "./useKeyboardShortcuts";

/**
 * Fires a window-level CustomEvent so feature components (ProjectCanvas,
 * InspiraApp) can react to shortcut presses without this provider having
 * to know about their internal state. Matches the pattern already used
 * by `inspira:canvas-tidy`, `inspira:export-request`, etc.
 */
function dispatch<T>(name: string, detail?: T): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(
    detail === undefined
      ? new CustomEvent(name)
      : new CustomEvent(name, { detail }),
  );
}

export function ShortcutsProvider(): null {
  const bindings = useMemo<ShortcutBinding[]>(() => {
    return [
      // ---- Global ---------------------------------------------------
      //
      // Save-intercept. The canvas auto-persists on every edit, so a
      // literal "save" is a no-op — but users hit Cmd/Ctrl+S reflexively
      // on any app that looks like a document, and leaving the default
      // "Save page" browser dialog visible is a jarring way to learn
      // that it's live. We swallow the key and show a reassuring toast.
      {
        combo: "Mod+S",
        description: t("shortcuts.desc.save_intercept"),
        group: "Global",
        handler: (event) => {
          event.preventDefault();
          dispatch(SHORTCUT_EVENTS.SAVE_PRESSED);
          toast.info(t("shortcuts.toast.save_intercept"), {
            title: t("shortcuts.toast.save_intercept_title"),
          });
        },
      },
      // Command-palette binding is already owned by InspiraApp (Mod+K).
      // Advertising it again here would just double-list it in the help
      // overlay, so this provider intentionally does NOT register it.

      // ---- Canvas ---------------------------------------------------
      //
      // Export / share dialog triggers. InspiraApp already listens for
      // `inspira:export-request` and opens the export dialog; we extend
      // that channel with a twin `inspira:share-request`. If the parent
      // session has wired the share listener, Mod+Shift+E will open it;
      // if not, the event is a no-op (same graceful-degradation rule
      // as every other custom-event-driven integration point).
      {
        combo: "Mod+E",
        description: t("shortcuts.desc.export"),
        group: "Canvas",
        handler: (event) => {
          event.preventDefault();
          dispatch(SHORTCUT_EVENTS.EXPORT_REQUEST);
        },
      },
      {
        combo: "Mod+Shift+E",
        description: t("shortcuts.desc.share"),
        group: "Canvas",
        handler: (event) => {
          event.preventDefault();
          dispatch(SHORTCUT_EVENTS.SHARE_REQUEST);
        },
      },

      // ---- Topic detail ---------------------------------------------
      //
      // Duplicate the currently-selected topic. The hook doesn't know
      // anything about selection — ProjectCanvas listens for this event
      // and does the lookup against its own selection state. If nothing
      // is selected the handler is an effective no-op (ProjectCanvas
      // will early-return).
      {
        combo: "Mod+D",
        description: t("shortcuts.desc.duplicate_topic"),
        group: "Canvas",
        handler: (event) => {
          event.preventDefault();
          dispatch(SHORTCUT_EVENTS.TOPIC_DUPLICATE_SELECTED);
        },
      },

      // Fit-view: zoom the canvas so every topic is visible. Loose-
      // coupled via an `inspira:canvas-fit-view` window event — the same
      // pattern as `inspira:canvas-tidy` — so this provider doesn't need
      // to reach into the React Flow viewport directly.
      {
        combo: "f",
        description: t("shortcuts.desc.fit_view"),
        group: "Canvas",
        handler: (event) => {
          event.preventDefault();
          dispatch("inspira:canvas-fit-view");
        },
      },

      // Arrow-key canvas navigation. Fires a directional event that
      // ProjectCanvas can interpret as "move selection to the connected
      // topic in this direction". Disabled when typing so you can still
      // arrow through text fields.
      ...makeArrowBindings(),
    ];
  }, []);

  useKeyboardShortcuts(bindings);
  return null;
}

/**
 * Build the four arrow-key bindings in one pass — same handler logic,
 * direction varies. Each binding fires `inspira:canvas-focus-move` with
 * a typed direction payload; ProjectCanvas is responsible for deciding
 * whether there's a selection to move (no-op if not) and which connected
 * topic is "closest" in that direction.
 */
function makeArrowBindings(): ShortcutBinding[] {
  const directions: { combo: string; direction: CanvasFocusDirection }[] = [
    { combo: "ArrowUp", direction: "up" },
    { combo: "ArrowDown", direction: "down" },
    { combo: "ArrowLeft", direction: "left" },
    { combo: "ArrowRight", direction: "right" },
  ];
  return directions.map(({ combo, direction }) => ({
    combo,
    description: t(`shortcuts.desc.arrow_${direction}`),
    group: "Canvas",
    // We need to stay off arrow keys while the user is typing — text-
    // field cursor movement must work. The hook's default behavior for
    // non-modifier keys would already suppress them, but named keys
    // (ArrowUp/…) flow through by default. Force-enable the typing
    // guard here.
    disabledWhenTyping: true,
    handler: (event) => {
      event.preventDefault();
      const detail: CanvasFocusMoveDetail = { direction };
      dispatch(SHORTCUT_EVENTS.CANVAS_FOCUS_MOVE, detail);
    },
  }));
}
