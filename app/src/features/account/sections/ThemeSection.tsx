// Account > Theme section.
//
// Two independent pickers:
//   Style — Bookworm (warm editorial) | Modern (clean, precise)
//   Mode  — Light | Dark | System
//
// Changes are applied immediately by writing data-style / data-theme on
// <html> (via the theme module) and persisted to localStorage.
//
// The existing three-way light/dark/system toggle is preserved. The new
// style picker sits above it as a pair of radio-cards.

import { useCallback } from "react";

import { useTheme } from "../../../theme";
import type { ThemeStyle, ThemeMode } from "../../../theme";
import { t } from "../../../i18n";

export type ThemeSectionProps = {
  onThemeChange?: (effective: "light" | "dark") => void;
};

export function ThemeSection({ onThemeChange }: ThemeSectionProps) {
  const { style, mode, setStyle, setMode } = useTheme();

  const handleStyle = useCallback(
    (next: ThemeStyle) => {
      setStyle(next);
    },
    [setStyle],
  );

  const handleMode = useCallback(
    (next: ThemeMode) => {
      setMode(next);
      // Notify callers that care about effective light/dark value
      if (onThemeChange) {
        const effective =
          next === "system"
            ? typeof window !== "undefined" &&
              typeof window.matchMedia === "function" &&
              window.matchMedia("(prefers-color-scheme: dark)").matches
              ? "dark"
              : "light"
            : next === "dark"
            ? "dark"
            : "light";
        onThemeChange(effective);
      }
    },
    [setMode, onThemeChange],
  );

  return (
    <section className="account-section" aria-labelledby="account-theme-heading">
      <h2 className="account-section__heading" id="account-theme-heading">
        {t("account.theme.heading")}
      </h2>

      {/* Style picker */}
      <div className="account-section__body">
        <p className="account-theme-label">{t("account.theme.style_label")}</p>
        <div
          className="account-theme-cards"
          role="radiogroup"
          aria-label={t("account.theme.style_label")}
        >
          <label
            className={
              "account-theme-card" +
              (style === "bookworm" ? " account-theme-card--active" : "")
            }
          >
            <input
              type="radio"
              name="account-style"
              value="bookworm"
              className="account-theme__input"
              checked={style === "bookworm"}
              onChange={() => handleStyle("bookworm")}
            />
            <span className="account-theme-card__swatch account-theme-card__swatch--bookworm" aria-hidden="true">
              <span className="account-theme-card__swatch-serif">Aa</span>
            </span>
            <span className="account-theme-card__name">
              {t("theme.bookworm_label")}
            </span>
            <span className="account-theme-card__tagline">
              {t("theme.bookworm_tagline")}
            </span>
          </label>

          <label
            className={
              "account-theme-card" +
              (style === "modern" ? " account-theme-card--active" : "")
            }
          >
            <input
              type="radio"
              name="account-style"
              value="modern"
              className="account-theme__input"
              checked={style === "modern"}
              onChange={() => handleStyle("modern")}
            />
            <span className="account-theme-card__swatch account-theme-card__swatch--modern" aria-hidden="true">
              <span className="account-theme-card__swatch-sans">Aa</span>
            </span>
            <span className="account-theme-card__name">
              {t("theme.modern_label")}
            </span>
            <span className="account-theme-card__tagline">
              {t("theme.modern_tagline")}
            </span>
          </label>
        </div>
      </div>

      {/* Mode picker */}
      <div className="account-section__body">
        <p className="account-theme-label">{t("account.theme.mode_label")}</p>
        <div
          className="account-theme"
          role="radiogroup"
          aria-label={t("account.theme.mode_label")}
        >
          <label className="account-theme__option">
            <input
              type="radio"
              name="account-theme"
              value="light"
              className="account-theme__input"
              checked={mode === "light"}
              onChange={() => handleMode("light")}
            />
            <span className="account-theme__label">{t("account.theme.light")}</span>
          </label>
          <label className="account-theme__option">
            <input
              type="radio"
              name="account-theme"
              value="dark"
              className="account-theme__input"
              checked={mode === "dark"}
              onChange={() => handleMode("dark")}
            />
            <span className="account-theme__label">{t("account.theme.dark")}</span>
          </label>
          <label className="account-theme__option">
            <input
              type="radio"
              name="account-theme"
              value="system"
              className="account-theme__input"
              checked={mode === "system"}
              onChange={() => handleMode("system")}
            />
            <span className="account-theme__label">{t("account.theme.system")}</span>
          </label>
        </div>
      </div>
    </section>
  );
}
