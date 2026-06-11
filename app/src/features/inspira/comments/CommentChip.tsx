// Tiny status indicator pinned at the top-right of a decision row.
// Three states (open / partial / addressed) drive distinct styling.

import React, { useState } from "react";

import { useCommentsForTarget } from "./CommentsContext";
import { CommentThread } from "./CommentThread";
import type { CommentTarget } from "./types";

export type CommentChipProps = {
  target: CommentTarget;
};

export function CommentChip({ target }: CommentChipProps): React.JSX.Element | null {
  const entry = useCommentsForTarget(target);
  const [open, setOpen] = useState(false);

  // Hide the chip entirely when no comments exist — chip appears as soon
  // as the user submits one (optimistic add). The selection-button is
  // the discovery affordance for first-time commenting.
  if (entry.comments.length === 0) return null;

  return (
    <span
      className="cc-pin"
      data-state={entry.status}
      data-cc-no-select
      onMouseDown={(e) => e.stopPropagation()}
    >
      <button
        type="button"
        aria-label={`Comments (${entry.comments.length})`}
        className="cc-pin__btn"
        onClick={(e) => {
          e.stopPropagation();
          setOpen((v) => !v);
        }}
      >
        <span className="cc-pin__dot" />
      </button>
      {open ? (
        <CommentThread target={target} onClose={() => setOpen(false)} />
      ) : null}
    </span>
  );
}
