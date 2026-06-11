// Feedback inbox API client.
//
// Reads via the shared httpClient — X-Workspace-Id is auto-
// injected, all reads/writes are workspace-scoped server-side.

import { httpClient } from "../../lib/httpClient";
import type {
  ClustersResponse,
  FeedbackCategory,
  FeedbackItem,
  FeedbackListResponse,
} from "./types";

export interface ListFilters {
  source?: string;
  status?: string;
  archived?: boolean;
  limit?: number;
  offset?: number;
}

export async function listFeedbackItems(
  filters: ListFilters = {},
): Promise<FeedbackListResponse> {
  const qs = new URLSearchParams();
  if (filters.source) qs.set("source", filters.source);
  if (filters.status) qs.set("status", filters.status);
  if (typeof filters.archived === "boolean") {
    qs.set("archived", filters.archived ? "true" : "false");
  }
  if (typeof filters.limit === "number") qs.set("limit", String(filters.limit));
  if (typeof filters.offset === "number") qs.set("offset", String(filters.offset));
  const qsStr = qs.toString();
  return httpClient.get<FeedbackListResponse>(
    `/api/v2/connectors/feedback/items${qsStr ? `?${qsStr}` : ""}`,
  );
}

export async function updateItemCategory(
  itemId: string,
  category: FeedbackCategory,
): Promise<{ ok: boolean; item: FeedbackItem | null }> {
  return httpClient.patch<{ ok: boolean; item: FeedbackItem | null }>(
    `/api/v2/connectors/feedback/items/${encodeURIComponent(itemId)}`,
    { type_hint: category },
  );
}

export async function listClusters(): Promise<ClustersResponse> {
  return httpClient.get<ClustersResponse>(
    "/api/v2/connectors/feedback/clusters",
  );
}

export async function bulkDeleteFeedbackItems(
  itemIds: string[],
): Promise<{ ok: boolean; deleted: number }> {
  return httpClient.post<{ ok: boolean; deleted: number }>(
    "/api/v2/connectors/feedback/items/bulk-delete",
    { item_ids: itemIds },
  );
}
