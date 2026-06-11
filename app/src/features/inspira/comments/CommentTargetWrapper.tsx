// Generic wrapper that marks an inline element as a comment target.
//
// Intentionally trivial: just a <span> with the three data attributes
// the selection hook walks the DOM for. ``kind`` lets later surfaces
// reuse the same atoms on code blocks (``kind="code"``) without forking the
// selection logic.

import React from "react";

import type { CommentTargetKind } from "./types";

export type CommentTargetWrapperProps = {
  kind: CommentTargetKind;
  id: string;
  children: React.ReactNode;
  // Allow consumers to override the rendered tag — TopicNode wraps an
  // <li> child with a <span>, but TopicDetail's body is already a <div>.
  as?: keyof React.JSX.IntrinsicElements;
  className?: string;
  style?: React.CSSProperties;
};

export function CommentTargetWrapper({
  kind,
  id,
  children,
  as = "span",
  className,
  style,
}: CommentTargetWrapperProps): React.JSX.Element {
  const Tag = as as React.ElementType;
  return (
    <Tag
      data-cc-target=""
      data-cc-target-kind={kind}
      data-cc-target-id={id}
      className={className}
      style={style}
    >
      {children}
    </Tag>
  );
}
