// Feedback inbox types — match the backend API shape.

export type FeedbackItemStatus =
  | "queued"
  | "classified"
  | "discarded"
  | "promoted";

export type FeedbackCategory =
  | "bug"
  | "feature"
  | "complaint"
  | "praise"
  | "question"
  | "noise";

export const ALL_CATEGORIES: readonly FeedbackCategory[] = [
  "bug",
  "feature",
  "complaint",
  "praise",
  "question",
  "noise",
];

export interface FeedbackItem {
  item_id: string;
  workspace_id: string;
  source: string;
  external_id: string | null;
  content_hash: string;
  title: string;
  body: string;
  author: string | null;
  author_email: string | null;
  received_at: string | null;
  ingested_at: string;
  type_hint: string | null;
  status: FeedbackItemStatus;
  cluster_id?: string | null;
}

export interface FeedbackListResponse {
  items: FeedbackItem[];
  total: number;
  queued: number;
}

export interface FeedbackCluster {
  cluster_id: string;
  theme: string | null;
  item_count: number;
  created_at: string;
  updated_at: string;
}

export interface ClustersResponse {
  clusters: FeedbackCluster[];
}
