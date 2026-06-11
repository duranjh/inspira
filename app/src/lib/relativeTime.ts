/**
 * Shared "X minutes ago" / "2 days ago" / "just now" formatter.
 *
 * Originally lived inline in ``ActivityTimeline.tsx``; extracted 2026-04-28
 * with #089 (Next Steps tab) so the new "Last generated …" header on the
 * Next Steps cards can render the same shape as the timeline rows.
 *
 * Tier thresholds (UTC seconds since the input timestamp):
 *  - <45s     → "Just now"
 *  - <60min   → "{count} minutes ago"
 *  - <24h     → "{count} hours ago"
 *  - <14d     → "{count} days ago"
 *  - else     → ``new Date().toLocaleDateString()`` (fallback to the locale's
 *               native short-date format; English-leaning at 14d+ is fine
 *               since Inspira's editorial voice rarely needs more than two
 *               weeks of relative-tense framing).
 *
 * Unparseable input is returned verbatim so callers don't have to defend
 * against null/empty strings on top of this.
 */
import { t } from "../i18n";


export function formatRelativeTime(iso: string | null | undefined): string {
  if (!iso) return "";
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return iso;
  const seconds = Math.max(0, Math.floor((Date.now() - then) / 1000));
  if (seconds < 45) return t("activity.time.just_now");
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return t("activity.time.minutes_ago", { count: minutes });
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return t("activity.time.hours_ago", { count: hours });
  const days = Math.floor(hours / 24);
  if (days < 14) return t("activity.time.days_ago", { count: days });
  try {
    return new Date(then).toLocaleDateString();
  } catch {
    return iso;
  }
}
