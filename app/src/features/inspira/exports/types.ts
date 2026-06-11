// Shared types for the W2 κ export modals.

export type ExportProvider = "linear" | "github";

export type PriorityLabel = "P0" | "P1" | "P2";

export type ExportProjectOptions = {
  include_canvas_link: boolean;
  include_source_feedback: boolean;
  apply_priority_label: boolean;
  priority_label: PriorityLabel;
};

export type ConnectorDestination = {
  configured: boolean;
  display: string | null;
  metadata: Record<string, unknown>;
  hint: string | null;
};

export type ExportSuccess = {
  ok: true;
  provider: ExportProvider;
  issue_url: string;
  // Linear: identifier (e.g. "ACM-249") + sub_issue_count.
  identifier?: string;
  sub_issue_count?: number;
  // GitHub: issue_number + issue_id.
  issue_number?: number;
  issue_id?: number | string;
};

export const DEFAULT_OPTIONS: ExportProjectOptions = {
  include_canvas_link: true,
  include_source_feedback: true,
  apply_priority_label: true,
  priority_label: "P1",
};
