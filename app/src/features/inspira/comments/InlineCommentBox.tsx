// Composer popover that opens when the user clicks the floating button.
// Two primary buttons (Apply = single, Apply-all = cascade) + Cancel.
// Click-outside / Escape dismiss.

import React, { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { useDismissOn } from "../../../hooks/useDismissOn";
import { useFocusTrap } from "../../../hooks/useFocusTrap";
import { computeFloatingPosition, type SelectionInfo } from "./useTextSelection";

export type InlineCommentBoxProps = {
  // The selection that triggered this composer — drives positioning.
  anchor: SelectionInfo;
  onApply: (text: string) => void;
  onApplyAll: (text: string) => void;
  onCancel: () => void;
};

export function InlineCommentBox({
  anchor,
  onApply,
  onApplyAll,
  onCancel,
}: InlineCommentBoxProps): React.JSX.Element | null {
  const [draft, setDraft] = useState("");
  const ref = useRef<HTMLDivElement>(null);
  const [mountNode, setMountNode] = useState<HTMLElement | null>(null);

  useEffect(() => {
    if (typeof document === "undefined") return;
    setMountNode(document.body);
  }, []);

  useDismissOn({
    enabled: true,
    onDismiss: onCancel,
    esc: true,
    clickOutsideRef: ref,
  });
  // Textarea already has autoFocus; the trap handles Tab cycling +
  // focus restoration when the box unmounts.
  const { onKeyDown } = useFocusTrap(ref, {
    enabled: true,
    autoFocus: false,
  });

  if (!mountNode) return null;
  const pos = computeFloatingPosition(anchor.rect, undefined, 320);
  const top = pos.placement === "above" ? Math.max(8, anchor.rect.top - 180) : anchor.rect.top + anchor.rect.height + 12;
  const trimmed = draft.trim();
  const disabled = trimmed.length === 0;

  return createPortal(
    <div
      ref={ref}
      className="cc-comment-box"
      style={{ position: "fixed", top, left: pos.left, zIndex: 9001, width: 320 }}
      data-cc-no-select
      onKeyDown={onKeyDown}
    >
      <textarea
        autoFocus
        className="cc-comment-textarea"
        placeholder="What should change?"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
      />
      <div className="cc-comment-actions">
        <button
          type="button"
          className="cc-btn cc-btn--primary"
          disabled={disabled}
          onClick={() => onApply(trimmed)}
        >
          Apply
        </button>
        <button
          type="button"
          className="cc-btn cc-btn--secondary"
          disabled={disabled}
          onClick={() => onApplyAll(trimmed)}
        >
          Apply-all
        </button>
        <button type="button" className="cc-btn cc-btn--ghost" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </div>,
    mountNode,
  );
}
