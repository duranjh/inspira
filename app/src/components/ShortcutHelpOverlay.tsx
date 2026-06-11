// Inspira — keyboard-shortcut cheat-sheet overlay.
//
// A small paper-card modal that lists every currently-registered shortcut
// grouped by context. Rendered by InspiraApp whenever the user presses
// `?` (or otherwise toggles the overlay open).
//
// Visuals are inline-styled to keep the scope contained — App.css
// deliberately untouched. The aesthetic matches Inspira's warm-editorial
// language: cream paper, soft shadow, serif display, monospace pills for
// the key glyphs.

import { useCallback, useEffect, useMemo, useRef } from "react";

import {
  listRegisteredShortcuts,
  type ShortcutBinding,
} from "../hooks/useKeyboardShortcuts";
import { t } from "../i18n";

export type ShortcutHelpOverlayProps = {
  open: boolean;
  onClose: () => void;
};

// Group order — explicit so the cheat sheet reads top-to-bottom the way a
// user would discover the app. Unknown groups appear after the known ones
// in insertion order.
const GROUP_ORDER = ["Global", "Canvas", "Topic detail"] as const;

export function ShortcutHelpOverlay({ open, onClose }: ShortcutHelpOverlayProps) {
  const closeButtonRef = useRef<HTMLButtonElement | null>(null);
  const cardRef = useRef<HTMLDivElement | null>(null);

  // Grab a snapshot when opening. The registry is stable across the life
  // of an overlay-open, so we don't need live updates inside this render.
  const groupedBindings = useMemo<Record<string, ShortcutBinding[]>>(() => {
    if (!open) return {};
    const all = listRegisteredShortcuts();
    const groups: Record<string, ShortcutBinding[]> = {};
    for (const b of all) {
      const g = b.group ?? "Other";
      (groups[g] ??= []).push(b);
    }
    return groups;
  }, [open]);

  const orderedGroupNames = useMemo(() => {
    const names = Object.keys(groupedBindings);
    const known = GROUP_ORDER.filter((g) => names.includes(g));
    const extra = names.filter((n) => !(GROUP_ORDER as readonly string[]).includes(n));
    return [...known, ...extra];
  }, [groupedBindings]);

  // Focus the close button on open so tab-order starts from a sensible
  // place. Not a full focus trap — v1 requirement.
  useEffect(() => {
    if (!open) return;
    const id = window.requestAnimationFrame(() => {
      closeButtonRef.current?.focus();
    });
    return () => window.cancelAnimationFrame(id);
  }, [open]);

  // Click-outside closes. We attach on document in capture phase so nested
  // pointer handlers (React Flow, etc.) can't eat the click.
  const handleBackdropPointerDown = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      if (e.target === e.currentTarget) onClose();
    },
    [onClose],
  );

  if (!open) return null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={t("shortcuts.dialog_label")}
      onPointerDown={handleBackdropPointerDown}
      className="shortcut-help-overlay"
      style={backdropStyle}
    >
      {/* Scoped responsive overrides — inline styles above aren't media-query
          targettable, so we inject class-based rules for narrow viewports. */}
      <style>{`
        @media (max-width: 520px) {
          .shortcut-help-overlay {
            padding: 8px !important;
            align-items: flex-end !important;
          }
          .shortcut-help-overlay__card {
            border-radius: 18px 18px 0 0 !important;
            width: 100% !important;
            max-width: 100% !important;
            padding: 22px 18px calc(22px + env(safe-area-inset-bottom, 0)) !important;
            max-height: 90vh !important;
          }
          .shortcut-help-overlay__title {
            font-size: 22px !important;
          }
          .shortcut-help-overlay__row {
            gap: 10px !important;
          }
          .shortcut-help-overlay__pill {
            min-width: 56px !important;
            font-size: 11px !important;
          }
          .shortcut-help-overlay__desc {
            font-size: 14px !important;
          }
          .shortcut-help-overlay__footer {
            font-size: 11px !important;
          }
        }
        @media (pointer: coarse) {
          .shortcut-help-overlay__close {
            min-width: 44px !important;
            min-height: 44px !important;
            width: 44px !important;
            height: 44px !important;
          }
        }
      `}</style>
      <div ref={cardRef} className="shortcut-help-overlay__card" style={cardStyle}>
        <header style={headerStyle}>
          <div>
            <div style={eyebrowStyle}>{t("app.brand")}</div>
            <h2 className="shortcut-help-overlay__title" style={titleStyle}>
              {t("shortcuts.title")}
            </h2>
          </div>
          <button
            ref={closeButtonRef}
            type="button"
            onClick={onClose}
            aria-label={t("shortcuts.close_aria")}
            className="shortcut-help-overlay__close"
            style={closeButtonStyle}
            onFocus={(e) => {
              e.currentTarget.style.outline =
                "2px solid var(--focus-ring, rgba(43, 37, 32, 0.35))";
              e.currentTarget.style.outlineOffset = "2px";
            }}
            onBlur={(e) => {
              e.currentTarget.style.outline = "none";
            }}
          >
            {"\u00D7"}
          </button>
        </header>

        <div style={groupsWrapStyle}>
          {orderedGroupNames.length === 0 ? (
            <p style={emptyStyle}>{t("shortcuts.empty")}</p>
          ) : (
            orderedGroupNames.map((group) => (
              <section key={group} style={groupStyle}>
                <h3 style={groupTitleStyle}>{group}</h3>
                <ul style={rowsStyle}>
                  {groupedBindings[group].map((b, i) => (
                    <li
                      key={`${group}-${b.combo}-${i}`}
                      className="shortcut-help-overlay__row"
                      style={rowStyle}
                    >
                      <span className="shortcut-help-overlay__pill" style={pillStyle}>
                        {formatCombo(b.combo)}
                      </span>
                      <span className="shortcut-help-overlay__desc" style={descStyle}>
                        {b.description}
                      </span>
                    </li>
                  ))}
                </ul>
              </section>
            ))
          )}
        </div>

        <footer className="shortcut-help-overlay__footer" style={footerStyle}>
          <span>{t("shortcuts.footer_press")}</span>
          <span style={inlinePillStyle}>?</span>
          <span>{t("shortcuts.footer_or")}</span>
          <span style={inlinePillStyle}>Esc</span>
          <span>{t("shortcuts.footer_to_close")}</span>
        </footer>
      </div>
    </div>
  );
}

// ---- Combo display helper ------------------------------------------------
//
// Normalize display for common combos. "Cmd+K" / "Ctrl+K" is shown with
// the platform symbol when we can detect the OS. Leaves everything else
// alone so authors can write "Shift+?" and have it render verbatim.

const IS_MAC =
  typeof navigator !== "undefined" &&
  /Mac|iPod|iPhone|iPad/.test(navigator.platform);

function formatCombo(combo: string): string {
  return combo
    .split("+")
    .map((part) => {
      const p = part.trim();
      const lower = p.toLowerCase();
      if (lower === "mod") return IS_MAC ? "\u2318" : "Ctrl";
      if (lower === "cmd" || lower === "meta" || lower === "command")
        return IS_MAC ? "\u2318" : "Cmd";
      if (lower === "ctrl" || lower === "control") return "Ctrl";
      if (lower === "alt" || lower === "option" || lower === "opt")
        return IS_MAC ? "\u2325" : "Alt";
      if (lower === "shift") return "Shift";
      if (lower === "esc" || lower === "escape") return "Esc";
      if (p.length === 1) return p.toUpperCase();
      return p.charAt(0).toUpperCase() + p.slice(1);
    })
    .join(" + ");
}

// ---- Inline styles --------------------------------------------------------
//
// Everything scoped to this component via inline styles so we don't
// pollute App.css. Colors match Inspira tokens (--paper, --ink-*, --sage)
// with hardcoded fallbacks so this renders sanely even if the tokens
// aren't declared on an ancestor for some reason.

const backdropStyle: React.CSSProperties = {
  position: "fixed",
  inset: 0,
  background: "rgba(43, 37, 32, 0.28)",
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  zIndex: 2000,
  padding: 24,
};

const cardStyle: React.CSSProperties = {
  background: "var(--paper, #F5F0E6)",
  color: "var(--ink-1, #2B2520)",
  borderRadius: 14,
  boxShadow: "0 24px 56px -16px rgba(43, 37, 32, 0.35), 0 2px 4px rgba(43, 37, 32, 0.08)",
  border: "1px solid var(--border-soft, rgba(43, 37, 32, 0.08))",
  width: "min(560px, 100%)",
  maxHeight: "calc(100vh - 48px)",
  overflowY: "auto",
  padding: 28,
  fontFamily: "var(--ff-serif, Georgia, 'Times New Roman', serif)",
};

const headerStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "flex-start",
  justifyContent: "space-between",
  gap: 16,
  marginBottom: 20,
};

const eyebrowStyle: React.CSSProperties = {
  fontFamily: "var(--ff-sans, system-ui, sans-serif)",
  fontSize: 11,
  letterSpacing: "0.14em",
  textTransform: "uppercase",
  color: "var(--ink-3, #7A6F64)",
  marginBottom: 6,
};

const titleStyle: React.CSSProperties = {
  fontFamily: "var(--ff-serif, Georgia, serif)",
  fontSize: 26,
  lineHeight: 1.1,
  fontWeight: 500,
  margin: 0,
  color: "var(--ink-1, #2B2520)",
};

const closeButtonStyle: React.CSSProperties = {
  appearance: "none",
  background: "transparent",
  border: "1px solid var(--paper-edge, rgba(43, 37, 32, 0.12))",
  borderRadius: 999,
  width: 32,
  height: 32,
  fontSize: 18,
  lineHeight: 1,
  color: "var(--ink-2, #4A413A)",
  cursor: "pointer",
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  padding: 0,
  flexShrink: 0,
};

const groupsWrapStyle: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 20,
};

const groupStyle: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 8,
};

const groupTitleStyle: React.CSSProperties = {
  fontFamily: "var(--ff-sans, system-ui, sans-serif)",
  fontSize: 11,
  letterSpacing: "0.14em",
  textTransform: "uppercase",
  color: "var(--ink-3, #7A6F64)",
  margin: 0,
  paddingBottom: 4,
  borderBottom: "1px solid var(--border-soft, rgba(43, 37, 32, 0.08))",
};

const rowsStyle: React.CSSProperties = {
  listStyle: "none",
  margin: 0,
  padding: 0,
  display: "flex",
  flexDirection: "column",
  gap: 8,
};

const rowStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 14,
};

const pillStyle: React.CSSProperties = {
  fontFamily:
    "var(--ff-mono, 'SFMono-Regular', Menlo, 'DejaVu Sans Mono', monospace)",
  fontSize: 12,
  background: "var(--border-soft, rgba(43, 37, 32, 0.06))",
  border: "1px solid var(--paper-edge, rgba(43, 37, 32, 0.1))",
  borderRadius: 6,
  padding: "3px 8px",
  minWidth: 68,
  textAlign: "center",
  color: "var(--ink-1, #2B2520)",
  flexShrink: 0,
};

const descStyle: React.CSSProperties = {
  fontFamily: "var(--ff-serif, Georgia, serif)",
  fontSize: 15,
  lineHeight: 1.35,
  color: "var(--ink-2, #4A413A)",
};

const footerStyle: React.CSSProperties = {
  marginTop: 24,
  paddingTop: 14,
  borderTop: "1px solid var(--border-soft, rgba(43, 37, 32, 0.08))",
  fontFamily: "var(--ff-sans, system-ui, sans-serif)",
  fontSize: 12,
  color: "var(--ink-3, #7A6F64)",
  display: "flex",
  alignItems: "center",
  gap: 6,
  flexWrap: "wrap",
};

const inlinePillStyle: React.CSSProperties = {
  fontFamily:
    "var(--ff-mono, 'SFMono-Regular', Menlo, 'DejaVu Sans Mono', monospace)",
  fontSize: 11,
  background: "var(--border-soft, rgba(43, 37, 32, 0.06))",
  border: "1px solid var(--paper-edge, rgba(43, 37, 32, 0.1))",
  borderRadius: 4,
  padding: "1px 6px",
};

const emptyStyle: React.CSSProperties = {
  fontFamily: "var(--ff-serif, Georgia, serif)",
  fontSize: 14,
  color: "var(--ink-3, #7A6F64)",
  margin: 0,
};
