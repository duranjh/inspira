// W2-θ comment-cascade types — shared across atoms.
//
// Generic ``CommentTarget`` keeps the atoms reusable for ι's code-pane
// integration later: today every target is a decision; tomorrow the
// same chip mounts on a code block via ``kind: "code"``.

export type CommentTargetKind = "decision" | "code";

export type CommentTarget = {
  kind: CommentTargetKind;
  id: string;
};

export type CommentStatus = "open" | "addressed" | "partial";

export type Comment = {
  comment_id: string;
  target: CommentTarget;
  text: string;
  created_at: string;
  // Server-driven; flips when a cascade fires that includes this comment.
  status: CommentStatus;
};

// scope_mode mirrors the BE: "local" = single-decision regen,
// "cascade" = full affected-scope regen via Apply-all.
export type ScopeMode = "local" | "cascade";

export type BannerState = "none" | "narrow" | "wide";

export type AffectedScope = {
  decision_ids: string[];
  topic_ids: string[];
  count: number;
  banner_state: BannerState;
};

export type CascadePreview = {
  affected_scope: AffectedScope;
  estimated_cost_usd: number;
  estimated_seconds: number;
};

export type NewDecisionVersion = {
  decision_id: string;
  version_int: number;
  prior_version_int: number;
  statement: string;
  rationale: string | null;
  change_note: string | null;
  is_new_decision: boolean;
};

export type CascadeRunStatus = "pending" | "running" | "complete" | "failed";

export type CascadeRun = {
  cascade_id: string;
  status: CascadeRunStatus;
  scope_mode: ScopeMode;
  commented_decisions: Array<{ decision_id: string; comment_text: string }>;
  affected_scope:
    | (AffectedScope & {
        new_decision_versions?: NewDecisionVersion[];
        failed_decisions?: Array<{ decision_id: string; error: string }>;
      })
    | null;
  diff_summary: {
    updated_count: number;
    created_count: number;
    failed_count: number;
  } | null;
  error: string | null;
  started_at: string;
  completed_at: string | null;
};

// Diff-badge age states — drive pulse vs. static vs. faded styling.
export type VersionAge = "fresh" | "recent" | "stale";
