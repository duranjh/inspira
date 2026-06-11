// Inspira — per-topic color-coding popover.
//
// Small palette trigger in the TopicDetail header. Clicking opens a popover
// with five color swatches plus a "Clear" control. Picking a swatch tags
// the topic with that color; picking "Clear" removes the tag. Every slug
// resolves to a theme CSS variable, so the swatches and the node accent
// both flip automatically in dark mode.
//
// Behavior:
//   * Uncontrolled popover state — closes on outside click or Esc, same
//     pattern as `ModelTierChip`.
//   * Optimistic local state: the caller passes the current color and an
//     async `onChange` that hits the API. The popover closes the moment
//     the user picks — the caller is responsible for reconciling on error.
//   * Intentionally inline styles — matches the warm-editorial language
//     without touching App.css. No new CSS file for five swatches.
//
// Accessibility:
//   * The trigger uses aria-haspopup/aria-expanded. Its aria-label comes
//     from a single i18n key ("Topic color"), not the currently-selected
//     color, so screen readers announce the control's purpose rather than
//     its state — parity with the model-tier chip.
//   * Swatches are real <button>s with role="menuitemradio" and the
//     aria-checked state reflecting the current pick. Keyboard users land
//     on the trigger in the natural tab order, open the popover, and tab
//     across the swatches.
//   * Each swatch carries a localized aria-label (the color name) so the
//     reader announces something semantically useful rather than "button".

import { useCallback, useEffect, useRef, useState, type ReactElement } from "react";

import { t } from "../../i18n";
import type { TopicColor } from "./api";

export type TopicColorPickerProps = {
  // Current color on the topic. `null`/`undefined` both render as the
  // "no color" state — the trigger shows an outline swatch and no swatch
  // in the popover is marked as selected.
  value: TopicColor | null | undefined;
  // Persist the selection. Called with a slug to set, or `null` to clear.
  // Caller owns error handling; we close the popover immediately.
  onChange: (color: TopicColor | null) => void | Promise<void>;
};

// Ordered list the popover renders, left-to-right. Kept deliberately short
// (5 colors) per the design brief — anything longer becomes a sub-menu,
// which we don't want for a warm editorial surface.
const COLOR_SLUGS: readonly TopicColor[] = [
  "sage",
  "rust",
  "gold",
  "ink",
  "paper",
] as const;

// Map each slug to the theme CSS variable used for the swatch fill. These
// stay in lockstep with `TopicNode.tsx` — if you change the var here, do
// it there too. `paper` is a tricky one because the base `--paper` is so
// close to the card background that the swatch would disappear; we pull
// `--paper-edge` instead so it still reads as a distinct chip while
// staying squarely in the "paper" family tonally.
const SWATCH_VAR: Record<TopicColor, string> = {
  sage: "var(--sage)",
  rust: "var(--rust)",
  gold: "var(--gold)",
  ink: "var(--ink)",
  paper: "var(--paper-edge)",
};

function colorLabel(slug: TopicColor): string {
  return t(`topic_color.label.${slug}`);
}

export function TopicColorPicker({
  value,
  onChange,
}: TopicColorPickerProps): ReactElement {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);

  // Close on outside click or Esc — identical pattern to ModelTierChip so
  // the two popovers feel like siblings.
  useEffect(() => {
    if (!open) return;
    const onDocClick = (e: MouseEvent) => {
      const root = rootRef.current;
      if (root && !root.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDocClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDocClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const pick = useCallback(
    (next: TopicColor | null) => {
      setOpen(false);
      void onChange(next);
    },
    [onChange],
  );

  const currentFill = value ? SWATCH_VAR[value] : "transparent";

  return (
    <div ref={rootRef} style={{ position: "relative" }}>
      <button
        type="button"
        className="topic-color-picker__trigger"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={t("topic_color.button_aria")}
        onClick={() => setOpen((v) => !v)}
        style={{
          height: 28,
          padding: "0 10px",
          borderRadius: 999,
          border: "1px solid var(--paper-edge, #e4ddcb)",
          background: "transparent",
          color: "var(--ink-2, #423a2d)",
          fontFamily: "var(--ff-sans)",
          fontSize: 12,
          letterSpacing: "0.02em",
          cursor: "pointer",
          display: "inline-flex",
          alignItems: "center",
          gap: 8,
          whiteSpace: "nowrap",
          transition:
            "background 120ms ease, color 120ms ease, border-color 120ms ease",
        }}
      >
        {/* Current-color dot. When no color is set, render a hollow outline
            so the user can still see the control without a filled chip. */}
        <span
          aria-hidden="true"
          style={{
            width: 12,
            height: 12,
            borderRadius: "50%",
            background: currentFill,
            border: value
              ? "1px solid rgba(43,37,32,0.15)"
              : "1px dashed var(--ink-4, #706055)",
            flex: "0 0 auto",
          }}
        />
        <span aria-hidden="true">{t("topic_color.button_label")}</span>
      </button>

      {open ? (
        <div
          role="menu"
          aria-label={t("topic_color.menu_aria")}
          className="topic-color-picker__menu"
          style={{
            position: "absolute",
            top: "calc(100% + 8px)",
            right: 0,
            minWidth: 220,
            background: "var(--paper-1, var(--paper, #fcf8ee))",
            border: "1px solid var(--paper-edge, #e4ddcb)",
            borderRadius: 10,
            boxShadow: "0 8px 24px rgba(30, 24, 12, 0.16)",
            padding: 10,
            // T3.4: bumped from 40 → 200. On mobile the planner's
            // question card created a stacking context (via transform
            // for the morph-in animation) that trapped the original
            // 40 below it. 200 clears every internal drawer surface
            // while staying below the global modals (LegalOverlay,
            // Dialog backdrop ≥ 3000).
            zIndex: 200,
          }}
        >
          <div
            style={{
              padding: "2px 4px 8px",
              fontFamily: "var(--ff-sans)",
              fontSize: 11,
              textTransform: "uppercase",
              letterSpacing: "0.08em",
              color: "var(--ink-3, #7a6f5e)",
            }}
          >
            {t("topic_color.menu_heading")}
          </div>
          <div
            role="group"
            aria-label={t("topic_color.menu_heading")}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 6,
              padding: "2px 4px 8px",
            }}
          >
            {COLOR_SLUGS.map((slug) => {
              const selected = value === slug;
              return (
                <button
                  key={slug}
                  type="button"
                  role="menuitemradio"
                  aria-checked={selected}
                  aria-label={colorLabel(slug)}
                  title={colorLabel(slug)}
                  onClick={() => pick(slug)}
                  style={{
                    width: 26,
                    height: 26,
                    borderRadius: "50%",
                    background: SWATCH_VAR[slug],
                    // The outer border is always present so every swatch
                    // reads as a distinct chip even against the cream
                    // menu background. A thicker ring appears around the
                    // currently-selected one so users know which is live.
                    border: selected
                      ? "2px solid var(--ink, #2b2520)"
                      : "1px solid rgba(43,37,32,0.2)",
                    padding: 0,
                    cursor: "pointer",
                    outline: "none",
                    boxShadow: selected
                      ? "0 0 0 2px var(--paper-1, #fcf8ee)"
                      : "none",
                  }}
                />
              );
            })}
          </div>
          <button
            type="button"
            role="menuitem"
            onClick={() => pick(null)}
            disabled={!value}
            style={{
              display: "block",
              width: "100%",
              textAlign: "left",
              padding: "8px 10px",
              border: "none",
              borderRadius: 6,
              background: "transparent",
              color: value ? "var(--ink-2, #423a2d)" : "var(--ink-4, #706055)",
              fontFamily: "var(--ff-sans)",
              fontSize: 12,
              cursor: value ? "pointer" : "default",
              letterSpacing: "0.02em",
            }}
          >
            {t("topic_color.clear")}
          </button>
        </div>
      ) : null}
    </div>
  );
}
