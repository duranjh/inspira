// Connectors API client.
//
// Reads through the shared httpClient — X-Workspace-Id is auto-
// injected for every endpoint here (the connectors router is
// workspace-scoped via `current_workspace_member` on the backend).

import { httpClient } from "../../lib/httpClient";
import {
  getActiveWorkspaceId,
  workspaceReady,
} from "../workspaces/WorkspaceContext";
import type { ConnectorsResponse } from "./types";
import type { ParsedFeedback } from "./CsvPasteDialog";

export async function getConnectors(): Promise<ConnectorsResponse> {
  return httpClient.get<ConnectorsResponse>("/api/v2/connectors");
}

export interface GitHubOAuthStart {
  install_url: string;
  state_token: string;
}

export async function startGitHubOAuth(
  options?: { redirect_to?: string },
): Promise<GitHubOAuthStart> {
  return httpClient.post<GitHubOAuthStart>(
    "/api/v2/connectors/github/oauth/start",
    options ?? {},
  );
}

// ── Local-repo upload (W3.1 Onboarding Wizard Step 2 path B) ──────

export interface LocalRepoUploadResult {
  ok: true;
  accepted: number;
  skipped: number;
  total_bytes: number;
  repo_id: string;
}

/** Upload a folder of source files as the workspace's repo snapshot.
 *  Each File's name should carry its relative path (set from the
 *  browser's webkitRelativePath when constructing the FormData). */
export async function uploadLocalRepo(
  formData: FormData,
): Promise<LocalRepoUploadResult> {
  // httpClient doesn't expose multipart directly — call fetch with
  // the same credentials + workspace-id header convention.
  await workspaceReady();
  const wsId = getActiveWorkspaceId();
  const headers: HeadersInit = wsId ? { "X-Workspace-Id": wsId } : {};
  const resp = await fetch("/api/v2/connectors/local-repo/upload", {
    method: "POST",
    body: formData,
    credentials: "include",
    headers,
  });
  if (!resp.ok) {
    const detail = await resp.text();
    throw new Error(
      `POST /api/v2/connectors/local-repo/upload failed: ${resp.status} ${resp.statusText} — ${detail}`,
    );
  }
  return (await resp.json()) as LocalRepoUploadResult;
}

export async function disconnectGitHub(): Promise<{ disconnected: boolean }> {
  return httpClient.delete<{ disconnected: boolean }>(
    "/api/v2/connectors/github",
  );
}

export async function triggerGitHubSync(): Promise<{ status: string }> {
  return httpClient.post<{ status: string }>(
    "/api/v2/connectors/github/sync",
  );
}

// ── Linear (W2 F4) ─────────────────────────────────────────────────

export interface LinearConnectResult {
  ok: true;
  account: { id: string | null; name: string | null };
}

export async function connectLinear(
  apiKey: string,
): Promise<LinearConnectResult> {
  return httpClient.post<LinearConnectResult>(
    "/api/v2/connectors/linear/connect",
    { api_key: apiKey },
  );
}

export async function triggerLinearSync(): Promise<{ status: string }> {
  return httpClient.post<{ status: string }>(
    "/api/v2/connectors/linear/sync",
  );
}

export async function disconnectLinear(): Promise<{ disconnected: boolean }> {
  return httpClient.delete<{ disconnected: boolean }>(
    "/api/v2/connectors/linear",
  );
}

// ── CSV / JSON paste-in (W2 F4) ────────────────────────────────────

export interface CsvImportResult {
  inserted: number;
  skipped: number;
  total: number;
}

export async function importCsvRows(
  rows: ParsedFeedback[],
): Promise<CsvImportResult> {
  return httpClient.post<CsvImportResult>(
    "/api/v2/connectors/csv/import",
    { rows },
  );
}
