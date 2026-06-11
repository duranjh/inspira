// Public surface of the comment-cascade module.
//
// CSS is imported here so any feature consuming the module gets the
// styles wired in one shot (no duplicate <link> concerns; Vite
// dedupes side-effect imports).

import "./comments.css";

export { CommentTargetWrapper } from "./CommentTargetWrapper";
export { FloatingCommentButton } from "./FloatingCommentButton";
export { InlineCommentBox } from "./InlineCommentBox";
export { CommentChip } from "./CommentChip";
export { CommentThread } from "./CommentThread";
export { CascadeBanner } from "./CascadeBanner";
export { DiffBadge } from "./DiffBadge";
export { CommentsLayer } from "./CommentsLayer";

export {
  CommentsProvider,
  useComments,
  useCommentsForTarget,
  useCascadePreview,
  useVersionAge,
} from "./CommentsContext";

export { useTextSelection, computeFloatingPosition } from "./useTextSelection";

export { cascadeApi, pollCascadeUntilDone } from "./cascadeApi";

export type {
  Comment,
  CommentStatus,
  CommentTarget,
  CommentTargetKind,
  CascadePreview,
  CascadeRun,
  CascadeRunStatus,
  AffectedScope,
  BannerState,
  NewDecisionVersion,
  ScopeMode,
  VersionAge,
} from "./types";
