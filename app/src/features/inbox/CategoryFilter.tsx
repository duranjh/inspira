// Filter pills row — Source + Category + Search.
//
// Per the B2.2 design: pills that toggle filter state, and a
// search input on the right. The pills are visually inline.
// Source + Category support multi-select (not exclusive); search
// is debounced 200ms.

import { ReactElement, useEffect, useMemo, useState } from "react";

import type { FeedbackCategory, FeedbackItem } from "./types";
import { ALL_CATEGORIES } from "./types";

const CATEGORY_LABEL: Record<FeedbackCategory, string> = {
  bug: "Bug",
  feature: "Feature",
  complaint: "Complaint",
  praise: "Praise",
  question: "Question",
  noise: "Noise",
};

export interface InboxFilters {
  categories: ReadonlySet<FeedbackCategory>;
  sources: ReadonlySet<string>;
  search: string;
}

export interface CategoryFilterProps {
  items: FeedbackItem[];
  filters: InboxFilters;
  onChange: (next: InboxFilters) => void;
}

export function CategoryFilter({
  items,
  filters,
  onChange,
}: CategoryFilterProps): ReactElement {
  const sources = useMemo(() => {
    const s = new Set<string>();
    for (const it of items) s.add(it.source);
    return Array.from(s).sort();
  }, [items]);

  const [searchDraft, setSearchDraft] = useState(filters.search);
  // Debounce search input → onChange.
  useEffect(() => {
    if (searchDraft === filters.search) return;
    const handle = window.setTimeout(() => {
      onChange({ ...filters, search: searchDraft });
    }, 200);
    return () => window.clearTimeout(handle);
  }, [searchDraft, filters, onChange]);

  const toggleCategory = (cat: FeedbackCategory) => {
    const next = new Set(filters.categories);
    if (next.has(cat)) next.delete(cat);
    else next.add(cat);
    onChange({ ...filters, categories: next });
  };

  const toggleSource = (source: string) => {
    const next = new Set(filters.sources);
    if (next.has(source)) next.delete(source);
    else next.add(source);
    onChange({ ...filters, sources: next });
  };

  const reset = () => {
    setSearchDraft("");
    onChange({
      categories: new Set(),
      sources: new Set(),
      search: "",
    });
  };

  const hasFilters =
    filters.categories.size > 0 ||
    filters.sources.size > 0 ||
    filters.search !== "";

  return (
    <div className="inbox-filters">
      <span className="inbox-filters__label">Categories</span>
      {ALL_CATEGORIES.map((cat) => {
        const active = filters.categories.has(cat);
        return (
          <button
            key={cat}
            type="button"
            className={
              "inbox-pill" + (active ? " inbox-pill--active" : "")
            }
            onClick={() => toggleCategory(cat)}
          >
            {CATEGORY_LABEL[cat]}
          </button>
        );
      })}
      {sources.length > 0 ? (
        <>
          <span className="inbox-filters__divider" aria-hidden />
          <span className="inbox-filters__label">Sources</span>
          {sources.map((source) => {
            const active = filters.sources.has(source);
            return (
              <button
                key={source}
                type="button"
                className={
                  "inbox-pill" + (active ? " inbox-pill--active" : "")
                }
                onClick={() => toggleSource(source)}
              >
                {source}
              </button>
            );
          })}
        </>
      ) : null}
      {hasFilters ? (
        <button
          type="button"
          className="inbox-filters__reset"
          onClick={reset}
        >
          Reset filters
        </button>
      ) : null}
      <div className="inbox-search">
        <input
          type="text"
          placeholder="Search feedback…"
          value={searchDraft}
          onChange={(e) => setSearchDraft(e.target.value)}
          spellCheck={false}
        />
      </div>
    </div>
  );
}

/** Apply filter state to the items list. Pure function so tests
 *  can assert on its behaviour without rendering the page. */
export function applyFilters(
  items: FeedbackItem[],
  filters: InboxFilters,
): FeedbackItem[] {
  const search = filters.search.trim().toLowerCase();
  return items.filter((it) => {
    if (filters.categories.size > 0) {
      const cat = (it.type_hint || "").toLowerCase();
      if (!filters.categories.has(cat as FeedbackCategory)) return false;
    }
    if (filters.sources.size > 0 && !filters.sources.has(it.source)) {
      return false;
    }
    if (search) {
      const haystack = `${it.title} ${it.body || ""} ${it.author || ""}`
        .toLowerCase();
      if (!haystack.includes(search)) return false;
    }
    return true;
  });
}
