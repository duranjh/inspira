// Dropdown thread anchored to the chip. Renders user comment + AI
// status line + a small composer. Click-outside / Escape close.

import React, { useRef } from "react";

import { useDismissOn } from "../../../hooks/useDismissOn";
import { useFocusTrap } from "../../../hooks/useFocusTrap";
import { useCommentsForTarget } from "./CommentsContext";
import type { CommentTarget } from "./types";

export type CommentThreadProps = {
  target: CommentTarget;
  onClose: () => void;
};

export function CommentThread({
  target,
  onClose,
}: CommentThreadProps): React.JSX.Element {
  const entry = useCommentsForTarget(target);
  const ref = useRef<HTMLDivElement>(null);

  useDismissOn({
    enabled: true,
    onDismiss: onClose,
    esc: true,
    clickOutsideRef: ref,
  });
  const { onKeyDown } = useFocusTrap(ref, { enabled: true });

  return (
    <div
      ref={ref}
      className="cc-thread"
      role="dialog"
      aria-label="Comment thread"
      onKeyDown={onKeyDown}
    >
      {entry.comments.map((c, i) => (
        <div key={c.comment_id} className="cc-thread__msg cc-thread__msg--user">
          <div className="cc-thread__body">{c.text}</div>
          {i === entry.comments.length - 1 ? (
            <div className="cc-thread__status" data-state={entry.status}>
              <span className="cc-thread__dot" />
              <span className="cc-thread__status-label">
                {entry.status === "addressed"
                  ? "Addressed — AI regenerated."
                  : entry.status === "partial"
                    ? "Partially addressed."
                    : entry.pending
                      ? "Working…"
                      : "Open."}
              </span>
            </div>
          ) : null}
        </div>
      ))}
    </div>
  );
}
