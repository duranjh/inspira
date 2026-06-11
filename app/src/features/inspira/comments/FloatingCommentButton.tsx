// Selection-anchored "Comment" pill. Portals to document.body so it
// can sit above any clipped canvas / overflow:hidden parent.

import React, { useEffect, useState } from "react";
import { createPortal } from "react-dom";

import {
  computeFloatingPosition,
  useTextSelection,
  type SelectionInfo,
} from "./useTextSelection";

export type FloatingCommentButtonProps = {
  onClick: (info: SelectionInfo) => void;
};

export function FloatingCommentButton({
  onClick,
}: FloatingCommentButtonProps): React.JSX.Element | null {
  const info = useTextSelection();
  const [mountNode, setMountNode] = useState<HTMLElement | null>(null);

  useEffect(() => {
    if (typeof document === "undefined") return;
    setMountNode(document.body);
  }, []);

  if (!info || !mountNode) return null;
  const pos = computeFloatingPosition(info.rect);
  return createPortal(
    <button
      type="button"
      className="cc-float-btn"
      style={{ position: "fixed", top: pos.top, left: pos.left, zIndex: 9000 }}
      onMouseDown={(e) => {
        // Prevent the selection from being cleared before the click handler fires.
        e.preventDefault();
      }}
      onClick={(e) => {
        e.preventDefault();
        onClick(info);
      }}
      data-cc-no-select
    >
      Comment
    </button>,
    mountNode,
  );
}
