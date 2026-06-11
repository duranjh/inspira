// Drill-down drawer — pops in from the right with full item
// detail + manual category override + close.
//
// Per the B2.2 design: 500px-wide right-side drawer with a top
// bar (title + close), focused-item view (source / chips / body),
// and a bottom action bar. F6 ships a simplified version (no
// cluster siblings, no Promote → that's F8 territory).

import { ReactElement, useEffect, useState } from "react";

import { CategoryChip, isFeedbackCategory } from "./CategoryChip";
import { ALL_CATEGORIES, type FeedbackCategory, type FeedbackItem } from "./types";

export interface FeedbackItemDrawerProps {
  item: FeedbackItem | null;
  open: boolean;
  onClose: () => void;
  onCategoryChange: (
    item: FeedbackItem,
    category: FeedbackCategory,
  ) => Promise<void> | void;
}

export function FeedbackItemDrawer({
  item,
  open,
  onClose,
  onCategoryChange,
}: FeedbackItemDrawerProps): ReactElement | null {
  const [editing, setEditing] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  // ESC closes (mirrors CreateWorkspaceDialog).
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  // Reset edit state when item changes.
  useEffect(() => {
    setEditing(false);
    setSubmitting(false);
  }, [item?.item_id]);

  if (!open || !item) return null;

  const currentCat = isFeedbackCategory(item.type_hint)
    ? item.type_hint
    : "noise";

  const handleCategorySelect = async (cat: FeedbackCategory) => {
    if (cat === currentCat) {
      setEditing(false);
      return;
    }
    setSubmitting(true);
    try {
      await onCategoryChange(item, cat);
      setEditing(false);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <>
      <div
        className="inbox-drawer__backdrop"
        onClick={onClose}
        aria-hidden
      />
      <aside
        className="inbox-drawer"
        role="dialog"
        aria-modal="true"
        aria-label="Feedback item details"
      >
        <header className="inbox-drawer__top">
          <button
            type="button"
            className="btn btn--ghost btn--sm"
            onClick={onClose}
          >
            ← Inbox
          </button>
          <div className="inbox-drawer__title">{item.title}</div>
          <button
            type="button"
            className="btn btn--icon btn--ghost"
            onClick={onClose}
            aria-label="Close drawer"
          >
            ×
          </button>
        </header>
        <div className="inbox-drawer__body">
          <div className="inbox-drawer__source">
            {item.source}
            {item.received_at ? ` · ${item.received_at.slice(0, 10)}` : ""}
            {item.author ? ` · ${item.author}` : ""}
            {item.author_email ? ` <${item.author_email}>` : ""}
          </div>
          <div className="inbox-drawer__meta">
            <CategoryChip category={currentCat} />
            <button
              type="button"
              className="btn btn--ghost btn--sm inbox-drawer__edit-btn"
              onClick={() => setEditing((v) => !v)}
              disabled={submitting}
            >
              {editing ? "Cancel" : "Change category"}
            </button>
          </div>
          {editing ? (
            <div className="inbox-drawer__category-picker" role="listbox">
              {ALL_CATEGORIES.map((cat) => (
                <button
                  key={cat}
                  type="button"
                  className={
                    "inbox-pill" +
                    (cat === currentCat ? " inbox-pill--active" : "")
                  }
                  onClick={() => void handleCategorySelect(cat)}
                  disabled={submitting}
                >
                  {cat[0].toUpperCase() + cat.slice(1)}
                </button>
              ))}
            </div>
          ) : null}
          {item.body ? (
            <p className="inbox-drawer__bodytext">{item.body}</p>
          ) : null}
        </div>
        {/* The "Promote to project" button used to live here. Founder
            direction (2026-05-04): the autonomous flow auto-promotes
            every cluster on import + auto-spawns the orchestrator on
            tile click, so the manual Promote button is no longer the
            entry point. Drawer becomes read-only inspection of the
            feedback item. */}
      </aside>
    </>
  );
}
