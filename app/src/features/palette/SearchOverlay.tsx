// SearchOverlay — cross-project search across projects, topics, decisions,
// and Q&A turns.
//
// The overlay debounces a 200ms network call against `api.searchAll`. When
// the backend endpoint is missing (404), we silently fall back to local
// filtering over the currently-loaded project's topics plus the known
// project list. Decisions and turns require the backend — those groups
// stay empty on the fallback path.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { JSX } from "react";
import { t } from "../../i18n";
import {
  api,
  type SearchHit,
  type Topic,
  type V2Project,
} from "../inspira/api";
import "./palette.css";

export type SearchOverlayProps = {
  open: boolean;
  onClose: () => void;
  onOpenProject: (projectId: string) => void;
  onOpenTopic: (projectId: string, topicId: string) => void;
  // Fallback data for the /api/v2/search 404 path.
  activeProjectTopics?: Topic[];
  activeProjectId?: string;
  projects?: V2Project[];
};

// Normalised bundle used by both the server-response path and the local
// fallback. `truncated` is only set by the server.
type ResultBundle = {
  hits: SearchHit[];
  truncated: boolean;
};

// sessionStorage key for remembering the last query + results on reopen.
const SESSION_KEY = "inspira.search.lastState";

type StoredState = {
  query: string;
  results: ResultBundle;
};

// Convenience: group hits by kind for rendering.
function groupHits(hits: SearchHit[]): {
  projects: SearchHit[];
  topics: SearchHit[];
  decisions: SearchHit[];
  turns: SearchHit[];
} {
  const groups = { projects: [] as SearchHit[], topics: [] as SearchHit[], decisions: [] as SearchHit[], turns: [] as SearchHit[] };
  for (const h of hits) {
    if (h.kind === "project") groups.projects.push(h);
    else if (h.kind === "topic") groups.topics.push(h);
    else if (h.kind === "decision") groups.decisions.push(h);
    else if (h.kind === "turn") groups.turns.push(h);
  }
  return groups;
}

function readStored(): StoredState | null {
  try {
    const raw = sessionStorage.getItem(SESSION_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as unknown;
    if (
      typeof parsed !== "object" ||
      parsed === null ||
      !("query" in parsed) ||
      !("results" in parsed)
    ) {
      return null;
    }
    return parsed as StoredState;
  } catch {
    return null;
  }
}

function writeStored(state: StoredState): void {
  try {
    sessionStorage.setItem(SESSION_KEY, JSON.stringify(state));
  } catch {
    // sessionStorage may be unavailable (privacy mode) — silently skip.
  }
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

// Case-insensitive substring match. Cheap and perfectly adequate for the
// "titles only" local fallback; the server is expected to do something
// smarter (prefix/full-text/etc.) when the endpoint exists.
function localContains(haystack: string, needle: string): boolean {
  if (needle.length === 0) return false;
  return haystack.toLowerCase().includes(needle.toLowerCase());
}

// Wrap each occurrence of the needle in the haystack with <mark>. Used to
// render matched characters in the result rows.
function highlightSubstring(haystack: string, needle: string): string {
  const n = needle.trim();
  if (n.length === 0) return escapeHtml(haystack);
  const lowHay = haystack.toLowerCase();
  const lowNeedle = n.toLowerCase();
  let out = "";
  let i = 0;
  while (i < haystack.length) {
    const found = lowHay.indexOf(lowNeedle, i);
    if (found === -1) {
      out += escapeHtml(haystack.slice(i));
      break;
    }
    out += escapeHtml(haystack.slice(i, found));
    out += "<mark>";
    out += escapeHtml(haystack.slice(found, found + n.length));
    out += "</mark>";
    i = found + n.length;
  }
  return out;
}

// Lightweight detection: any thrown Error whose message contains "404"
// counts as "endpoint missing" and triggers the local fallback.
function isNotFound(err: unknown): boolean {
  if (err instanceof Error) {
    return err.message.includes("404");
  }
  return false;
}

// Local fallback that uses only the in-memory topics + project list.
// Decisions and turns require the backend — those groups stay empty here.
function localFallback(
  query: string,
  opts: {
    activeProjectTopics?: Topic[];
    activeProjectId?: string;
    projects?: V2Project[];
  },
): ResultBundle {
  const { activeProjectTopics = [], activeProjectId, projects = [] } = opts;
  const hits: SearchHit[] = [];
  for (const p of projects) {
    if (localContains(p.title, query)) {
      hits.push({
        kind: "project",
        project_id: p.project_id,
        project_title: p.title,
        topic_id: null,
        topic_title: null,
        snippet: p.title,
        matched_field: "title",
      });
    }
  }
  for (const topic of activeProjectTopics) {
    if (localContains(topic.title, query)) {
      const projTitle = projects.find((p) => p.project_id === (activeProjectId ?? topic.project_id))?.title ?? topic.project_id;
      hits.push({
        kind: "topic",
        project_id: topic.project_id,
        project_title: projTitle,
        topic_id: topic.topic_id,
        topic_title: topic.title,
        snippet: topic.title,
        matched_field: "title",
      });
    }
  }
  return { hits, truncated: false };
}

const EMPTY_RESULTS: ResultBundle = {
  hits: [],
  truncated: false,
};

export function SearchOverlay(props: SearchOverlayProps): JSX.Element | null {
  const {
    open,
    onClose,
    onOpenProject,
    onOpenTopic,
    activeProjectTopics,
    activeProjectId,
    projects,
  } = props;

  const [query, setQuery] = useState("");
  const [results, setResults] = useState<ResultBundle>(EMPTY_RESULTS);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const listRef = useRef<HTMLDivElement | null>(null);

  // Restore last-seen query + results on reopen so the user doesn't have
  // to retype what they were just looking at.
  useEffect(() => {
    if (!open) return;
    const stored = readStored();
    setQuery(stored?.query ?? "");
    setResults(stored?.results ?? EMPTY_RESULTS);
    setSelectedIndex(0);
    const raf = requestAnimationFrame(() => {
      inputRef.current?.focus();
      inputRef.current?.select();
    });
    return () => cancelAnimationFrame(raf);
  }, [open]);

  // 200ms debounce. A blank query clears results.
  useEffect(() => {
    if (!open) return;
    const trimmed = query.trim();
    if (trimmed.length === 0) {
      setResults(EMPTY_RESULTS);
      return;
    }
    const fallbackOpts = {
      activeProjectTopics,
      activeProjectId,
      projects,
    };
    const handle = window.setTimeout(() => {
      let cancelled = false;
      api
        .searchAll(trimmed)
        .then((data) => {
          if (cancelled) return;
          setResults({ hits: data.hits, truncated: data.truncated });
        })
        .catch((err) => {
          if (cancelled) return;
          if (isNotFound(err)) {
            setResults(localFallback(trimmed, fallbackOpts));
          } else {
            // Other errors: surface no results rather than noisy UI.
            setResults(EMPTY_RESULTS);
          }
        });
      return () => {
        cancelled = true;
      };
    }, 200);
    return () => window.clearTimeout(handle);
  }, [query, open, activeProjectTopics, projects]);

  // Persist query + results when they change while open.
  useEffect(() => {
    if (!open) return;
    writeStored({ query, results });
  }, [open, query, results]);

  // Build the flat keyboard-nav list from the server's ordered hit array.
  // The server already ranks hits (title matches first, body second) so we
  // preserve that order while grouping for rendering.
  const flat: SearchHit[] = useMemo(() => results.hits, [results.hits]);

  useEffect(() => {
    if (selectedIndex >= flat.length) {
      setSelectedIndex(0);
    }
  }, [flat.length, selectedIndex]);

  useEffect(() => {
    if (!open) return;
    const el = listRef.current?.querySelector<HTMLElement>(
      `[data-palette-index="${selectedIndex}"]`,
    );
    if (el) el.scrollIntoView({ block: "nearest" });
  }, [selectedIndex, open]);

  const runHit = useCallback(
    (hit: SearchHit) => {
      if (hit.kind === "project") {
        onOpenProject(hit.project_id);
      } else if (hit.topic_id) {
        // Topics, decisions, and turns all open their parent topic.
        onOpenTopic(hit.project_id, hit.topic_id);
      } else {
        onOpenProject(hit.project_id);
      }
      onClose();
    },
    [onOpenProject, onOpenTopic, onClose],
  );

  // Group hits for section headings while keeping the absolute index for
  // keyboard navigation aligned with `flat`. MUST be called before the
  // `open` early-return below — React error #310 fires if a later
  // render reaches this useMemo when an earlier one didn't.
  const groups = useMemo(() => groupHits(results.hits), [results.hits]);

  if (!open) return null;

  const onKeyDown = (e: React.KeyboardEvent): void => {
    if (e.key === "Escape") {
      e.preventDefault();
      onClose();
      return;
    }
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setSelectedIndex((i) => (flat.length === 0 ? 0 : (i + 1) % flat.length));
      return;
    }
    if (e.key === "ArrowUp") {
      e.preventDefault();
      setSelectedIndex((i) =>
        flat.length === 0 ? 0 : (i - 1 + flat.length) % flat.length,
      );
      return;
    }
    if (e.key === "Enter") {
      e.preventDefault();
      const hit = flat[selectedIndex];
      if (hit) runHit(hit);
    }
  };

  const trimmed = query.trim();
  const hasAnyResults = flat.length > 0;

  // Render one hit button. `absIdx` is the position in `flat` (used for
  // keyboard selection); `hit` carries everything we need to display.
  let runningIndex = 0;

  function renderHit(hit: SearchHit): JSX.Element {
    const idx = runningIndex++;
    const isSelected = idx === selectedIndex;
    const parentLabel = hit.project_title;
    const snippet = hit.snippet;
    return (
      <button
        key={`${hit.kind}-${hit.project_id}-${hit.topic_id ?? ""}-${idx}`}
        type="button"
        data-palette-index={idx}
        className={"palette-item" + (isSelected ? " palette-item--selected" : "")}
        onMouseEnter={() => setSelectedIndex(idx)}
        onClick={() => runHit(hit)}
      >
        <span className="palette-item__main">
          <span
            className="palette-item__label palette-item__label--wrap"
            dangerouslySetInnerHTML={{
              __html: highlightSubstring(snippet, trimmed),
            }}
          />
          {hit.kind !== "project" && (
            <span className="palette-item__sub">
              {t("search_overlay.in_project", { title: parentLabel })}
            </span>
          )}
        </span>
      </button>
    );
  }

  return (
    <div
      className="palette-scrim"
      role="dialog"
      aria-modal="true"
      aria-label={t("search_overlay.aria")}
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="palette-card palette-card--search" onKeyDown={onKeyDown}>
        <div className="palette-input-row">
          <input
            ref={inputRef}
            className="palette-input"
            type="text"
            placeholder={t("search_overlay.placeholder")}
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setSelectedIndex(0);
            }}
            aria-label={t("search_overlay.query_aria")}
          />
          <span className="palette-kbd">esc</span>
        </div>

        <div className="palette-list" ref={listRef}>
          {trimmed.length === 0 ? (
            <div className="palette-hint">
              {t("search_overlay.hint")}
            </div>
          ) : !hasAnyResults ? (
            <div className="palette-empty" role="status">
              <h2 className="palette-empty__headline">
                {t("empty.search.headline", { query: trimmed })}
              </h2>
              <p className="palette-empty__hint">
                {t("empty.search.body")}
              </p>
            </div>
          ) : (
            <>
              {groups.projects.length > 0 && (
                <div className="palette-group">
                  <div className="palette-group__heading">{t("search_overlay.group_projects")}</div>
                  {groups.projects.map((h) => renderHit(h))}
                </div>
              )}

              {groups.topics.length > 0 && (
                <div className="palette-group">
                  <div className="palette-group__heading">{t("search_overlay.group_topics")}</div>
                  {groups.topics.map((h) => renderHit(h))}
                </div>
              )}

              {groups.decisions.length > 0 && (
                <div className="palette-group">
                  <div className="palette-group__heading">{t("search_overlay.group_decisions")}</div>
                  {groups.decisions.map((h) => renderHit(h))}
                </div>
              )}

              {groups.turns.length > 0 && (
                <div className="palette-group">
                  <div className="palette-group__heading">{t("search_overlay.group_turns")}</div>
                  {groups.turns.map((h) => renderHit(h))}
                </div>
              )}

              {results.truncated && (
                <div className="palette-hint palette-hint--truncated">
                  {t("search_overlay.truncated_hint")}
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
