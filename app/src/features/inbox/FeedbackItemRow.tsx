// Inbox row — single feedback item.
//
// In F6 each row represents one item (slice 3 — embeddings —
// adds cluster grouping where one row stands for N items). Row
// shows category chip + title + source + timestamp + click-to-
// open-drawer.

import { ReactElement } from "react";

import { CategoryChip, isFeedbackCategory } from "./CategoryChip";
import type { FeedbackItem } from "./types";

export interface FeedbackItemRowProps {
  item: FeedbackItem;
  selected: boolean;
  onClick: () => void;
  /** Multi-select checkbox — when defined, renders a leading checkbox.
   *  Click on the checkbox toggles ``checked`` via ``onToggleChecked``
   *  and is suppressed from the row's drawer-open click. */
  checked?: boolean;
  onToggleChecked?: () => void;
}

function fmtRelative(iso: string | null | undefined): string {
  if (!iso) return "";
  const ts = Date.parse(iso);
  if (!Number.isFinite(ts)) return "";
  const deltaSec = Math.max(0, Math.floor((Date.now() - ts) / 1000));
  if (deltaSec < 60) return "just now";
  if (deltaSec < 3600) {
    const m = Math.floor(deltaSec / 60);
    return `${m} min ago`;
  }
  if (deltaSec < 86400) {
    const h = Math.floor(deltaSec / 3600);
    return `${h}h ago`;
  }
  if (deltaSec < 86400 * 7) {
    const d = Math.floor(deltaSec / 86400);
    return `${d}d ago`;
  }
  // Older items show absolute date.
  try {
    return new Date(iso).toLocaleDateString(undefined, {
      month: "short",
      day: "numeric",
    });
  } catch {
    return "";
  }
}

export function FeedbackItemRow({
  item,
  selected,
  onClick,
  checked,
  onToggleChecked,
}: FeedbackItemRowProps): ReactElement {
  const cat = isFeedbackCategory(item.type_hint) ? item.type_hint : "noise";
  const showCheckbox = onToggleChecked !== undefined;
  return (
    <div
      className={
        "inbox-row-wrap" +
        (checked ? " inbox-row-wrap--checked" : "")
      }
    >
      {showCheckbox ? (
        <label
          className="inbox-row__check"
          onClick={(e) => e.stopPropagation()}
          aria-label={`Select feedback: ${item.title}`}
        >
          <input
            type="checkbox"
            checked={!!checked}
            onChange={() => onToggleChecked?.()}
          />
        </label>
      ) : null}
      <button
        type="button"
        className={
          "inbox-row" + (selected ? " inbox-row--selected" : "")
        }
        onClick={onClick}
        aria-current={selected ? "true" : undefined}
      >
        <div className="inbox-row__top">
          <CategoryChip category={cat} />
          <span className="inbox-row__title">{item.title}</span>
          <span className="inbox-row__source">{item.source}</span>
        </div>
        {item.body ? (
          <p className="inbox-row__body">{item.body}</p>
        ) : null}
        <div className="inbox-row__meta">
          {item.author ? (
            <span className="inbox-row__meta-item">{item.author}</span>
          ) : null}
          {item.received_at || item.ingested_at ? (
            <span className="inbox-row__meta-item">
              {fmtRelative(item.received_at || item.ingested_at)}
            </span>
          ) : null}
        </div>
      </button>
    </div>
  );
}
