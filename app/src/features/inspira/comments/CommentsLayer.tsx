// Single mount point that owns the selection → composer → cascade
// orchestration. Drop one <CommentsLayer/> per surface (ProjectCanvas,
// TopicDetail) inside the matching <CommentsProvider/>; the layer wires
// the FloatingCommentButton, the active InlineCommentBox, the
// CascadeBanner, and the Apply / Apply-all → preview / commit dance.

import React, { useCallback, useRef, useState } from "react";

import { CascadeBanner } from "./CascadeBanner";
import { useComments } from "./CommentsContext";
import { FloatingCommentButton } from "./FloatingCommentButton";
import { InlineCommentBox } from "./InlineCommentBox";
import type { CommentTarget } from "./types";
import type { SelectionInfo } from "./useTextSelection";

export type CommentsLayerProps = {
  // Optional callback when a cascade completes (e.g., parent refetches
  // decisions so DiffBadges materialize on next render).
  onCascadeComplete?: () => void;
};

export function CommentsLayer({
  onCascadeComplete,
}: CommentsLayerProps): React.JSX.Element {
  const { optimisticAddComment, previewCascade, commitCascade } = useComments();
  const [composerAnchor, setComposerAnchor] = useState<SelectionInfo | null>(null);

  // Hold the pending comment text + target so CascadeBanner's Confirm
  // can fire commitCascade with the right payload.
  const pendingRef = useRef<{ target: CommentTarget; text: string } | null>(null);

  const onSelectionClick = useCallback((info: SelectionInfo) => {
    setComposerAnchor(info);
  }, []);

  const onApply = useCallback(
    (text: string) => {
      if (!composerAnchor) return;
      const target = composerAnchor.target;
      optimisticAddComment(target, text);
      setComposerAnchor(null);
      void commitCascade(target, text, "local")
        .then(() => onCascadeComplete?.())
        .catch((err) => {
          console.warn("[CommentsLayer] cascade local failed:", err);
        });
    },
    [composerAnchor, optimisticAddComment, commitCascade, onCascadeComplete],
  );

  const onApplyAll = useCallback(
    (text: string) => {
      if (!composerAnchor) return;
      const target = composerAnchor.target;
      optimisticAddComment(target, text);
      pendingRef.current = { target, text };
      setComposerAnchor(null);
      void previewCascade(target, text, "cascade").catch((err) => {
        console.warn("[CommentsLayer] cascade preview failed:", err);
        pendingRef.current = null;
      });
    },
    [composerAnchor, optimisticAddComment, previewCascade],
  );

  const onConfirm = useCallback(() => {
    const pending = pendingRef.current;
    pendingRef.current = null;
    if (!pending) return;
    void commitCascade(pending.target, pending.text, "cascade")
      .then(() => onCascadeComplete?.())
      .catch((err) => {
        console.warn("[CommentsLayer] cascade commit failed:", err);
      });
  }, [commitCascade, onCascadeComplete]);

  return (
    <>
      <FloatingCommentButton onClick={onSelectionClick} />
      {composerAnchor ? (
        <InlineCommentBox
          anchor={composerAnchor}
          onApply={onApply}
          onApplyAll={onApplyAll}
          onCancel={() => setComposerAnchor(null)}
        />
      ) : null}
      <CascadeBanner onConfirm={onConfirm} />
    </>
  );
}
