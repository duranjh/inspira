// Inspira — locale-aware formatting helpers.
//
// Every helper reads the active locale via `getLocale()` (module-scoped, kept
// in sync with the React LocaleContext). That means:
//   - All Intl.* objects are constructed with the user's chosen locale.
//   - When the user flips the locale in the LocalePicker, any component that
//     already calls `useLocale()` (or any `t()` call in the same tree) will
//     re-render and these helpers will produce the new locale's output.
//
// Usage
// -----
//   import { formatDate, formatRelativeTime, formatNumber } from "../../i18n";
//
// Bad input (falsy / NaN) returns "" rather than throwing so callers don't
// need to guard.

import { getLocale } from "./index";

// ---- Date / time ---------------------------------------------------------

/**
 * Format an ISO 8601 string or Date as a medium-length date string,
 * e.g. "21 Apr 2026" (en) / "21 avr. 2026" (fr).
 */
export function formatDate(
  iso: string | Date | null | undefined,
  opts?: Intl.DateTimeFormatOptions,
): string {
  if (!iso) return "";
  const d = iso instanceof Date ? iso : new Date(iso as string);
  if (isNaN(d.getTime())) return "";
  try {
    return new Intl.DateTimeFormat(getLocale(), {
      dateStyle: "medium",
      ...opts,
    }).format(d);
  } catch {
    return "";
  }
}

/**
 * Format an ISO 8601 string or Date as a medium date + short time,
 * e.g. "21 Apr 2026, 15:30" (en).
 */
export function formatDateTime(
  iso: string | Date | null | undefined,
  opts?: Intl.DateTimeFormatOptions,
): string {
  if (!iso) return "";
  const d = iso instanceof Date ? iso : new Date(iso as string);
  if (isNaN(d.getTime())) return "";
  try {
    return new Intl.DateTimeFormat(getLocale(), {
      dateStyle: "medium",
      timeStyle: "short",
      ...opts,
    }).format(d);
  } catch {
    return "";
  }
}

// Breakpoints in seconds. Same progression used in ProjectCard / ProjectsListPage
// (previously duplicated). Now the single authoritative list.
const SECOND = 1;
const MINUTE = 60 * SECOND;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;
const WEEK = 7 * DAY;
const MONTH = 30.4375 * DAY; // ~2629800 s
const YEAR = 365.25 * DAY;   // ~31557600 s

/**
 * Format an ISO 8601 string or Date as a relative time string using the
 * active locale, e.g. "3 minutes ago" (en) / "il y a 3 minutes" (fr).
 *
 * Returns "" for falsy / invalid input.
 */
export function formatRelativeTime(
  iso: string | Date | null | undefined,
): string {
  if (!iso) return "";
  const d = iso instanceof Date ? iso : new Date(iso as string);
  if (isNaN(d.getTime())) return "";
  try {
    const diffSec = Math.round((d.getTime() - Date.now()) / 1000);
    const abs = Math.abs(diffSec);
    const rtf = new Intl.RelativeTimeFormat(getLocale(), { numeric: "auto" });
    if (abs < MINUTE) return rtf.format(diffSec, "second");
    if (abs < HOUR) return rtf.format(Math.round(diffSec / MINUTE), "minute");
    if (abs < DAY) return rtf.format(Math.round(diffSec / HOUR), "hour");
    if (abs < WEEK) return rtf.format(Math.round(diffSec / DAY), "day");
    if (abs < MONTH) return rtf.format(Math.round(diffSec / WEEK), "week");
    if (abs < YEAR) return rtf.format(Math.round(diffSec / MONTH), "month");
    return rtf.format(Math.round(diffSec / YEAR), "year");
  } catch {
    return "";
  }
}

// ---- Numbers -------------------------------------------------------------

/**
 * Format a number according to the active locale, e.g. "1,234" (en)
 * / "1 234" (fr). Pass `opts` for currency, percent, etc.
 */
export function formatNumber(
  n: number | null | undefined,
  opts?: Intl.NumberFormatOptions,
): string {
  if (n == null || isNaN(n)) return "";
  try {
    return new Intl.NumberFormat(getLocale(), opts).format(n);
  } catch {
    return String(n);
  }
}
