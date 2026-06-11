// Inspira — language picker.
//
// A small dropdown that swaps the active locale. Every option renders in
// its own native form (Español, Français, Deutsch, …) so a user who
// doesn't read the current UI language can still recognize their own.
//
// The picker is intentionally minimal — it's a styling-friendly <select>
// so browsers handle keyboard nav, screen-reader labels, and touch
// behavior for us. On mobile it becomes a native picker wheel. The
// warm-editorial look is painted via inline styles matched to App.css
// so we don't need to register another class rule.
//
// Two render modes (via `variant` prop):
//   - "menu" (default)  — compact select with a globe glyph prefix,
//                         suitable for the UserMenu or a footer row.
//   - "inline"          — no prefix glyph, no border. Used inside an
//                         already-framed container (e.g. the auth gate
//                         or legal footer) where the select should
//                         disappear into the row.

import { useCallback } from "react";

import { availableLocales, useLocale, t } from "../i18n";
import { toast } from "./ToastProvider";

export type LocalePickerProps = {
  variant?: "menu" | "inline";
  /** Optional callback for closing a parent menu after the user picks. */
  onPicked?: () => void;
};

export function LocalePicker({ variant = "menu", onPicked }: LocalePickerProps) {
  const [locale, setLocale] = useLocale();
  const codes = availableLocales();

  const onChange = useCallback(
    (e: React.ChangeEvent<HTMLSelectElement>) => {
      const next = e.target.value;
      // P1.11 (#071) — Surface the forward-only behavior as a toast
      // when the user actually picks a new language. UI chrome strings
      // re-render via `t()`, but LLM-generated content (topic titles,
      // questions, decisions, why-this-matters) was authored in the
      // language of whichever locale was active when the LLM call ran
      // — and stays in that language. Without this note the user
      // could reasonably expect mid-project translation, see English
      // strings on a Spanish UI, and assume the locale switch failed.
      // Skip the toast when the value didn't actually change (safety;
      // the <select> in theory only fires onChange on real changes).
      if (next !== locale) {
        toast.info(t("account.locale_toggle_note"), { durationMs: 6000 });
      }
      setLocale(next);
      onPicked?.();
    },
    [locale, setLocale, onPicked],
  );

  return (
    <div
      className={`locale-picker locale-picker--${variant}`}
      aria-label={t("locale.picker_aria")}
    >
      {variant === "menu" ? (
        <span className="locale-picker__glyph" aria-hidden="true">
          🌐
        </span>
      ) : null}
      <label htmlFor="inspira-locale-picker" className="visually-hidden">
        {t("locale.picker_label")}
      </label>
      <select
        id="inspira-locale-picker"
        className="locale-picker__select"
        value={locale}
        onChange={onChange}
      >
        {codes.map((code) => (
          <option key={code} value={code}>
            {t(`locale.name.${code}`)}
          </option>
        ))}
      </select>
      <span className="locale-picker__caret" aria-hidden="true">
        ▾
      </span>
    </div>
  );
}
