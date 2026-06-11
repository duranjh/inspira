// Feedback inbox (B2.2) — the F6 surface.
//
// Lists every feedback_item the workspace has ingested. Filter
// pills (categories + sources + search) narrow the list; click
// a row to drill into the drawer; manual category override from
// the drawer. The cluster-grouping shape (B2.2 "12 items in this
// cluster") arrives in slice 3 once embedding-based dedupe lands;
// until then each row is one item.
//
// Empty-state copy: "Inspira hasn't seen any feedback yet" with a
// CTA to /connectors. Avoids "no data" — the empty state is the
// onboarding nudge.

import { ReactElement, useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { toast } from "../../components/ToastProvider";
import { AuthedShell } from "../shared/AuthedShell";
import { CategoryFilter, applyFilters, type InboxFilters } from "./CategoryFilter";
import { FeedbackItemDrawer } from "./FeedbackItemDrawer";
import { FeedbackItemRow } from "./FeedbackItemRow";
import {
  bulkDeleteFeedbackItems,
  listFeedbackItems,
  updateItemCategory,
} from "./api";
import type { FeedbackCategory, FeedbackItem } from "./types";

const PAGE_SIZE = 200;

export function InboxPage(): ReactElement {
  return (
    <AuthedShell>
      <InboxBody />
    </AuthedShell>
  );
}

function InboxBody(): ReactElement {
  const [items, setItems] = useState<FeedbackItem[]>([]);
  const [total, setTotal] = useState<number>(0);
  const [loading, setLoading] = useState(true);
  const [errorBanner, setErrorBanner] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [filters, setFilters] = useState<InboxFilters>({
    categories: new Set(),
    sources: new Set(),
    search: "",
  });
  // Multi-select state for bulk delete. Reset on refresh + on
  // successful delete so stale ids don't linger.
  const [checked, setChecked] = useState<Set<string>>(new Set());
  const [deleting, setDeleting] = useState(false);
  // New / Archive tabs. "New" =
  // raw, untouched (cluster_id IS NULL). "Archive" = AI-sifted
  // (cluster_id IS NOT NULL). Default to New.
  const [tab, setTab] = useState<"new" | "archive">("new");

  const refresh = useCallback(async () => {
    setLoading(true);
    setErrorBanner(null);
    try {
      const res = await listFeedbackItems({
        archived: tab === "archive",
        limit: PAGE_SIZE,
      });
      setItems(res.items);
      setTotal(res.total);
    } catch (exc) {
      setErrorBanner(
        exc instanceof Error
          ? exc.message
          : "Couldn't load feedback — refresh the page.",
      );
    } finally {
      setLoading(false);
    }
  }, [tab]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const visible = useMemo(
    () => applyFilters(items, filters),
    [items, filters],
  );

  const selectedItem = useMemo(
    () => items.find((it) => it.item_id === selectedId) ?? null,
    [items, selectedId],
  );

  const toggleChecked = useCallback((itemId: string) => {
    setChecked((prev) => {
      const next = new Set(prev);
      if (next.has(itemId)) {
        next.delete(itemId);
      } else {
        next.add(itemId);
      }
      return next;
    });
  }, []);

  const handleBulkDelete = useCallback(async () => {
    if (checked.size === 0 || deleting) return;
    const ids = Array.from(checked);
    setDeleting(true);
    try {
      const result = await bulkDeleteFeedbackItems(ids);
      // Optimistic prune — splice the deleted ids out of items so
      // the row vanishes without a full refetch.
      setItems((prev) => prev.filter((it) => !checked.has(it.item_id)));
      setTotal((prev) => Math.max(0, prev - result.deleted));
      setChecked(new Set());
      toast.success(
        `Deleted ${result.deleted} item${result.deleted === 1 ? "" : "s"}.`,
      );
    } catch (exc) {
      toast.error(
        exc instanceof Error
          ? `Delete failed: ${exc.message}`
          : "Delete failed.",
      );
    } finally {
      setDeleting(false);
    }
  }, [checked, deleting]);

  const toggleSelectAllVisible = useCallback(
    (visibleIds: string[], allChecked: boolean) => {
      setChecked((prev) => {
        const next = new Set(prev);
        if (allChecked) {
          // Already covered → clear just the visible subset.
          for (const id of visibleIds) next.delete(id);
        } else {
          for (const id of visibleIds) next.add(id);
        }
        return next;
      });
    },
    [],
  );

  const handleCategoryChange = useCallback(
    async (item: FeedbackItem, category: FeedbackCategory) => {
      try {
        const result = await updateItemCategory(item.item_id, category);
        if (result.item) {
          setItems((prev) =>
            prev.map((it) =>
              it.item_id === item.item_id ? result.item! : it,
            ),
          );
          toast.success(`Category set to ${category}.`);
        }
      } catch (exc) {
        toast.error(
          exc instanceof Error
            ? exc.message
            : "Couldn't update the category.",
        );
        throw exc;
      }
    },
    [],
  );

  const empty = !loading && items.length === 0;
  const filtered = !loading && items.length > 0 && visible.length === 0;

  return (
    <div className="inbox-page">
      <header className="inbox-page__header">
        <p className="eyebrow">Feedback inbox</p>
        <h1 className="display inbox-page__title">
          What partners are <em>telling you</em>.
        </h1>
        <p className="meta inbox-page__lede">
          {loading
            ? "Loading…"
            : total === 0
              ? tab === "new"
                ? "No new feedback — Inspira has sifted through it all."
                : "Nothing archived yet."
              : total === 1
                ? "1 item."
                : `${total} items.`}
        </p>
      </header>

      <div className="inbox-page__tabs" role="tablist" aria-label="Inbox view">
        <button
          type="button"
          role="tab"
          aria-selected={tab === "new"}
          className={
            "inbox-page__tab" +
            (tab === "new" ? " inbox-page__tab--active" : "")
          }
          onClick={() => {
            if (tab === "new") return;
            setTab("new");
            setChecked(new Set());
          }}
        >
          New
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === "archive"}
          className={
            "inbox-page__tab" +
            (tab === "archive" ? " inbox-page__tab--active" : "")
          }
          onClick={() => {
            if (tab === "archive") return;
            setTab("archive");
            setChecked(new Set());
          }}
        >
          Archive
        </button>
      </div>

      {errorBanner ? (
        <div className="inbox-page__error" role="alert">
          {errorBanner}
        </div>
      ) : null}

      {empty ? (
        <div className="inbox-page__empty card">
          {tab === "new" ? (
            <>
              <h2 className="section-title">All caught up.</h2>
              <p className="meta">
                New feedback will land here as it arrives. Items already
                sifted into clusters live in <em>Archive</em>.
              </p>
              <button
                type="button"
                className="btn btn--primary"
                onClick={() => {
                  setTab("archive");
                  setChecked(new Set());
                }}
              >
                Show Archive →
              </button>
            </>
          ) : (
            <>
              <h2 className="section-title">No archived items yet.</h2>
              <p className="meta">
                Items move here automatically once Inspira clusters
                them. Connect a source if Inspira hasn't seen anything
                yet.
              </p>
              <Link to="/connectors" className="btn btn--primary">
                Open connectors →
              </Link>
            </>
          )}
        </div>
      ) : null}

      {!empty && !loading ? (
        <CategoryFilter
          items={items}
          filters={filters}
          onChange={setFilters}
        />
      ) : null}

      {!empty && !loading && visible.length > 0 ? (() => {
        const visibleIds = visible.map((it) => it.item_id);
        const allVisibleChecked =
          visibleIds.length > 0 &&
          visibleIds.every((id) => checked.has(id));
        return (
          <div className="inbox-page__bulkbar">
            <label className="inbox-page__bulkbar-check">
              <input
                type="checkbox"
                checked={allVisibleChecked}
                onChange={() =>
                  toggleSelectAllVisible(visibleIds, allVisibleChecked)
                }
              />
              <span>
                {checked.size === 0
                  ? `Select all (${visibleIds.length})`
                  : `${checked.size} selected`}
              </span>
            </label>
            {checked.size > 0 ? (
              <button
                type="button"
                className="inbox-page__bulkbar-delete"
                onClick={handleBulkDelete}
                disabled={deleting}
              >
                {deleting
                  ? "Deleting…"
                  : `Delete ${checked.size} item${checked.size === 1 ? "" : "s"}`}
              </button>
            ) : null}
          </div>
        );
      })() : null}

      <div className="inbox-page__list">
        {filtered ? (
          <div className="inbox-page__nothing-matches">
            No items match the current filters.
          </div>
        ) : null}
        {visible.map((it) => (
          <FeedbackItemRow
            key={it.item_id}
            item={it}
            selected={it.item_id === selectedId}
            onClick={() => setSelectedId(it.item_id)}
            checked={checked.has(it.item_id)}
            onToggleChecked={() => toggleChecked(it.item_id)}
          />
        ))}
      </div>

      <FeedbackItemDrawer
        item={selectedItem}
        open={selectedId !== null}
        onClose={() => setSelectedId(null)}
        onCategoryChange={handleCategoryChange}
      />
    </div>
  );
}
