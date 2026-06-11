// Activity Timeline — per-project audit-log feed.
//
// Slide-in panel docked to the right edge of the canvas. Renders
// audit_log events newest-first with a relative timestamp, a category
// glyph, and a short human-readable label. The "Load more" button at
// the bottom paginates the feed +20 at a time.
//
// The panel is purely informational — no editing from here. Clicking
// entries does nothing (yet); the goal is to give the user a
// chronological record of what's happened on the canvas so they can
// trust the history.
//
// Styling note: this component is self-contained and injects its own
// <style> block (following the precedent set by ShortcutHelpOverlay).
// That keeps the component behind one import without editing App.css —
// the tokens (--paper, --sage, etc.) are inherited from :root so the
// warm editorial palette stays consistent.
//
// Data source: GET /api/v2/projects/{id}/activity — see
// services/planning_studio_service/store.py::list_project_activity.

import { useCallback, useEffect, useRef, useState } from "react";

import { api, type ActivityEvent } from "./api";
import { t } from "../../i18n";

// Shared selector prefix — lets the CSS below target only our nodes.
const ROOT_CLASS = "activity-panel";

// CSS injected once per session via the singleton <style> below. Uses
// the global design tokens declared in App.css (--paper, --sage, --ink
// ramp, etc.) so the panel stays theme-consistent without duplicating
// any colors.
const PANEL_STYLES = `
.activity-panel {
  position: absolute;
  top: 0;
  right: 0;
  bottom: 0;
  /* Above the canvas-actions button group (z-index: 45) which would
     otherwise sit on top of the panel's right edge and block its
     content. Stays below the topic-detail drawer (z: 100) and
     Dialog backdrop (z: 3000+). Founder-reported #032. */
  z-index: 50;
  width: min(360px, 92vw);
  display: flex;
  flex-direction: column;
  /* Theme token, not a hard-coded cream — without this the panel lit
   * up white on the dark canvas. --paper has dark-mode overrides in
   * App.css. */
  background: var(--paper);
  border-left: 1px solid var(--paper-edge);
  box-shadow: -20px 0 40px -20px rgba(43, 37, 32, 0.18);
  animation: activity-panel-slide 300ms cubic-bezier(0.2, 0.7, 0.2, 1);
}
@keyframes activity-panel-slide {
  from { transform: translateX(16px); opacity: 0; }
  to   { transform: translateX(0);   opacity: 1; }
}
@media (prefers-reduced-motion: reduce) {
  .activity-panel { animation: none; }
}
.activity-panel__header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding: 20px 20px 12px;
  border-bottom: 1px solid var(--border-soft);
}
.activity-panel__titles { min-width: 0; }
.activity-panel__heading {
  margin: 0 0 4px;
  font-family: var(--ff-display);
  font-size: 22px;
  font-weight: 500;
  color: var(--ink);
  letter-spacing: -0.01em;
}
.activity-panel__subheading {
  margin: 0;
  font-family: var(--ff-sans);
  font-size: 12px;
  color: var(--ink-3);
  line-height: 1.4;
}
.activity-panel__close {
  flex: none;
  width: 32px;
  height: 32px;
  padding: 0;
  border: 1px solid transparent;
  border-radius: 50%;
  background: transparent;
  color: var(--ink-2);
  font-size: 24px;
  line-height: 1;
  cursor: pointer;
  transition: background 120ms ease, border-color 120ms ease;
}
.activity-panel__close:hover,
.activity-panel__close:focus-visible {
  background: color-mix(in srgb, var(--sage) 10%, transparent);
  border-color: var(--paper-edge);
  outline: none;
}
.activity-panel__body {
  flex: 1;
  overflow-y: auto;
  padding: 12px 12px 20px;
}
.activity-panel__status,
.activity-panel__empty {
  padding: 40px 16px;
  text-align: center;
  color: var(--ink-3);
  font-family: var(--ff-text);
  font-size: 14px;
  line-height: 1.55;
}
.activity-panel__error {
  padding: 24px 16px;
  text-align: center;
  color: var(--rust);
  font-family: var(--ff-sans);
  font-size: 13px;
}
.activity-panel__retry {
  margin-top: 12px;
  padding: 6px 16px;
  border: 1px solid var(--paper-edge);
  border-radius: 999px;
  background: var(--paper);
  color: var(--ink-2);
  font-family: var(--ff-sans);
  font-size: 12px;
  cursor: pointer;
}
.activity-panel__retry:hover,
.activity-panel__retry:focus-visible {
  border-color: var(--ink-5);
  outline: none;
}
.activity-panel__list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.activity-panel__item {
  display: flex;
  gap: 12px;
  align-items: flex-start;
  padding: 10px 12px;
  border-radius: 8px;
  transition: background 120ms ease;
}
.activity-panel__item:hover {
  background: color-mix(in srgb, var(--sage) 5%, transparent);
}
.activity-panel__glyph {
  flex: none;
  width: 28px;
  height: 28px;
  border-radius: 50%;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: color-mix(in srgb, var(--sage) 12%, var(--paper));
  color: var(--sage);
  border: 1px solid color-mix(in srgb, var(--sage) 22%, transparent);
}
.activity-panel__glyph[data-category="decision"] {
  background: color-mix(in srgb, var(--gold) 12%, var(--paper));
  color: var(--gold);
  border-color: color-mix(in srgb, var(--gold) 22%, transparent);
}
.activity-panel__glyph[data-category="relationship"] {
  background: color-mix(in srgb, var(--ink-4) 16%, var(--paper));
  color: var(--ink-3);
  border-color: color-mix(in srgb, var(--ink-4) 30%, transparent);
}
.activity-panel__glyph[data-category="export"],
.activity-panel__glyph[data-category="share"] {
  background: color-mix(in srgb, var(--rust) 10%, var(--paper));
  color: var(--rust);
  border-color: color-mix(in srgb, var(--rust) 20%, transparent);
}
.activity-panel__item-body { min-width: 0; flex: 1; }
.activity-panel__item-text {
  margin: 0;
  font-family: var(--ff-text);
  font-size: 14px;
  line-height: 1.45;
  color: var(--ink);
  word-break: break-word;
}
.activity-panel__item-meta {
  margin: 2px 0 0;
  font-family: var(--ff-sans);
  font-size: 11px;
  color: var(--ink-4);
  letter-spacing: 0.02em;
}
.activity-panel__more {
  padding: 12px 8px 4px;
  display: flex;
  justify-content: center;
}
.activity-panel__more-btn {
  padding: 6px 18px;
  border: 1px solid var(--paper-edge);
  border-radius: 999px;
  background: var(--paper);
  color: var(--ink-2);
  font-family: var(--ff-sans);
  font-size: 12px;
  letter-spacing: 0.03em;
  cursor: pointer;
  transition: border-color 120ms ease, transform 120ms ease;
}
.activity-panel__more-btn:hover:not(:disabled),
.activity-panel__more-btn:focus-visible {
  border-color: var(--ink-5);
  transform: translateY(-1px);
  outline: none;
}
.activity-panel__more-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
@media (max-width: 720px) {
  .activity-panel { width: 100%; }
}
`;

// Page size when loading more rows. First-load pull is slightly bigger
// so the panel feels populated immediately without an auto-click.
const INITIAL_PAGE_SIZE = 30;
const LOAD_MORE_PAGE_SIZE = 20;

export type ActivityTimelineProps = {
  projectId: string;
  onClose: () => void;
};

type FeedState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ready"; events: ActivityEvent[]; hasMore: boolean };

export function ActivityTimeline({ projectId, onClose }: ActivityTimelineProps) {
  const [state, setState] = useState<FeedState>({ kind: "loading" });
  const [loadingMore, setLoadingMore] = useState(false);
  const closeBtnRef = useRef<HTMLButtonElement | null>(null);

  // Fetch the first page on mount and whenever the project changes.
  const loadInitial = useCallback(() => {
    let cancelled = false;
    setState({ kind: "loading" });
    api
      .listProjectActivity(projectId, { limit: INITIAL_PAGE_SIZE, offset: 0 })
      .then((res) => {
        if (cancelled) return;
        setState({
          kind: "ready",
          events: res.events,
          hasMore: res.has_more,
        });
      })
      .catch(() => {
        if (cancelled) return;
        setState({ kind: "error", message: t("activity.error") });
      });
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  useEffect(() => loadInitial(), [loadInitial]);

  // Focus the close button on open so ESC / keyboard users can dismiss
  // immediately without hunting for the affordance.
  useEffect(() => {
    const raf = requestAnimationFrame(() => {
      closeBtnRef.current?.focus();
    });
    return () => cancelAnimationFrame(raf);
  }, []);

  // ESC closes the panel — matches the TopicDetail drawer convention.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        onClose();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const handleLoadMore = useCallback(() => {
    if (state.kind !== "ready" || !state.hasMore || loadingMore) return;
    setLoadingMore(true);
    api
      .listProjectActivity(projectId, {
        limit: LOAD_MORE_PAGE_SIZE,
        offset: state.events.length,
      })
      .then((res) => {
        setState((prev) => {
          if (prev.kind !== "ready") return prev;
          // Guard against duplicate IDs in the rare case the user
          // triggered two pages concurrently — union by event_id.
          const seen = new Set(prev.events.map((e) => e.event_id));
          const fresh = res.events.filter((e) => !seen.has(e.event_id));
          return {
            kind: "ready",
            events: [...prev.events, ...fresh],
            hasMore: res.has_more,
          };
        });
      })
      .catch(() => {
        // Non-fatal: keep the list we already have and surface an
        // inline error hint via the main error state only if the
        // current list is empty (no point blowing the panel away when
        // the user already has 30 rows to read).
        setState((prev) =>
          prev.kind === "ready" && prev.events.length > 0
            ? prev
            : { kind: "error", message: t("activity.error") },
        );
      })
      .finally(() => setLoadingMore(false));
  }, [state, loadingMore, projectId]);

  return (
    <aside
      className={ROOT_CLASS}
      role="complementary"
      aria-label={t("activity.panel.aria")}
    >
      {/* Scoped styles — keeps the panel self-contained so we don't
          have to edit App.css. Injected per-mount; the browser
          de-duplicates identical stylesheets by rule-hash. */}
      <style>{PANEL_STYLES}</style>
      <header className="activity-panel__header">
        <div className="activity-panel__titles">
          <h2 className="activity-panel__heading">
            {t("activity.panel.heading")}
          </h2>
          <p className="activity-panel__subheading">
            {t("activity.panel.subheading")}
          </p>
        </div>
        <button
          ref={closeBtnRef}
          type="button"
          className="activity-panel__close"
          onClick={onClose}
          aria-label={t("activity.panel.close_aria")}
        >
          <span aria-hidden="true">×</span>
        </button>
      </header>

      <div className="activity-panel__body">
        {state.kind === "loading" ? (
          <p className="activity-panel__status" role="status">
            {t("activity.loading")}
          </p>
        ) : state.kind === "error" ? (
          <div className="activity-panel__error" role="alert">
            <p>{state.message}</p>
            <button
              type="button"
              className="activity-panel__retry"
              onClick={loadInitial}
            >
              {t("activity.retry")}
            </button>
          </div>
        ) : state.events.length === 0 ? (
          <p className="activity-panel__empty">{t("activity.empty")}</p>
        ) : (
          <ul className="activity-panel__list">
            {state.events.map((event) => (
              <li key={event.event_id} className="activity-panel__item">
                <span
                  className="activity-panel__glyph"
                  aria-hidden="true"
                  data-category={event.category}
                >
                  <CategoryIcon category={event.category} />
                </span>
                <div className="activity-panel__item-body">
                  <p className="activity-panel__item-text">
                    {humanizeEvent(event)}
                  </p>
                  <p className="activity-panel__item-meta">
                    <time dateTime={event.created_at}>
                      {relativeTime(event.created_at)}
                    </time>
                  </p>
                </div>
              </li>
            ))}
          </ul>
        )}

        {state.kind === "ready" && state.hasMore ? (
          <div className="activity-panel__more">
            <button
              type="button"
              className="activity-panel__more-btn"
              onClick={handleLoadMore}
              disabled={loadingMore}
            >
              {loadingMore
                ? t("activity.loading_more")
                : t("activity.load_more")}
            </button>
          </div>
        ) : null}
      </div>
    </aside>
  );
}

// ---- Helpers --------------------------------------------------------------

/**
 * Map an audit_log ``{category, action, subject_title}`` tuple to a
 * translation key. Falls back to a generic per-category phrasing when
 * we don't have a specific mapping for the action; falls back to
 * ``activity.text.unknown`` only when the category itself is unknown.
 */
function humanizeEvent(event: ActivityEvent): string {
  const subject = event.subject_title.trim();
  const hasSubject = subject.length > 0;

  // Special-case: exports. Most export actions don't carry a subject
  // string worth showing; the raw format name ("markdown", "pdf") is
  // already obvious from the download.
  if (event.category === "export") {
    return hasSubject
      ? t("activity.text.export.with_subject", { subject })
      : t("activity.text.export.generic");
  }

  // Share: the subject is a URL path fragment, not a user-friendly
  // label — collapse to a fixed phrase for mint/revoke, generic
  // otherwise.
  if (event.category === "share") {
    if (event.action === "mint") return t("activity.text.share.mint");
    if (event.action === "revoke") return t("activity.text.share.revoke");
    return t("activity.text.share.generic");
  }

  // Relationships: delete is always phrased generically (the label
  // alone doesn't identify the connection), create uses the label.
  if (event.category === "relationship") {
    if (event.action === "delete") {
      return t("activity.text.relationship.delete");
    }
    if (event.action === "create") {
      return hasSubject
        ? t("activity.text.relationship.create", { subject })
        : t("activity.text.relationship.create_untitled");
    }
    return hasSubject
      ? t("activity.text.relationship.generic", { subject })
      : t("activity.text.relationship.generic_untitled");
  }

  // Project create has no meaningful subject (the title isn't always
  // set at creation time), so the phrase is fixed.
  if (event.category === "project") {
    if (event.action === "create") {
      return t("activity.text.project.create");
    }
    if (event.action === "rename") {
      return hasSubject
        ? t("activity.text.project.rename", { subject })
        : t("activity.text.project.rename_untitled");
    }
    if (event.action === "archive") {
      return t("activity.text.project.archive");
    }
    if (event.action === "unarchive") {
      return t("activity.text.project.unarchive");
    }
    return hasSubject
      ? t("activity.text.project.generic", { subject })
      : t("activity.text.project.generic_untitled");
  }

  // Topic / Decision: per-action mapping with generic fallback.
  const knownActions: readonly string[] =
    event.category === "topic"
      ? ["create", "update", "delete", "close"]
      : ["create", "delete"];
  const action = knownActions.includes(event.action) ? event.action : "generic";
  const suffix = hasSubject ? "" : "_untitled";
  const params = hasSubject ? { subject } : undefined;
  return t(`activity.text.${event.category}.${action}${suffix}`, params);
}

/**
 * Render a relative time stamp for an ISO8601 string. Stops at days —
 * anything older drops to a plain date. Locale-aware via the browser's
 * own Intl formatter for the "days ago" tier.
 */
function relativeTime(iso: string): string {
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
  // For older events, show an absolute date.
  try {
    return new Date(then).toLocaleDateString();
  } catch {
    return iso;
  }
}

// ---- Icons ----------------------------------------------------------------

/**
 * Small glyph that matches the event's category. Intentionally tiny
 * and monochrome — color comes from the enclosing .activity-panel__glyph
 * via ``data-category`` so the component file stays style-free.
 */
function CategoryIcon({ category }: { category: ActivityEvent["category"] }) {
  const iconProps = {
    viewBox: "0 0 16 16",
    width: 14,
    height: 14,
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.4,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
  };
  switch (category) {
    case "topic":
      return (
        <svg {...iconProps}>
          <rect x="3" y="3" width="10" height="10" rx="2" />
          <path d="M6 7h4" />
          <path d="M6 10h3" />
        </svg>
      );
    case "decision":
      return (
        <svg {...iconProps}>
          <path d="M3 8l3 3 7-7" />
        </svg>
      );
    case "relationship":
      return (
        <svg {...iconProps}>
          <circle cx="4" cy="8" r="1.6" />
          <circle cx="12" cy="8" r="1.6" />
          <path d="M5.5 8h5" />
        </svg>
      );
    case "project":
      return (
        <svg {...iconProps}>
          <path d="M2.5 5.5a2 2 0 0 1 2-2h3l1.2 1.6h3.8a2 2 0 0 1 2 2V11a2 2 0 0 1-2 2h-8a2 2 0 0 1-2-2z" />
        </svg>
      );
    case "share":
      return (
        <svg {...iconProps}>
          <circle cx="5" cy="8" r="1.8" />
          <circle cx="12" cy="4" r="1.8" />
          <circle cx="12" cy="12" r="1.8" />
          <path d="M6.5 7l4-2.4" />
          <path d="M6.5 9l4 2.4" />
        </svg>
      );
    case "export":
      return (
        <svg {...iconProps}>
          <path d="M8 2v8" />
          <path d="M5 7l3 3 3-3" />
          <path d="M3 13h10" />
        </svg>
      );
    default:
      return (
        <svg {...iconProps}>
          <circle cx="8" cy="8" r="5" />
        </svg>
      );
  }
}
