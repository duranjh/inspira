// Workspace API client — list / create / detail / invite.
//
// All calls go through the shared httpClient (lib/httpClient.ts)
// which handles X-Workspace-Id injection, auth-endpoint skipping,
// and the workspaceReady() init-race gate. None of the workspace
// endpoints actually need X-Workspace-Id (they're either
// list-mine, create, or path-scoped under {workspace_id}/...),
// but the client's skip-list takes care of that.

import { httpClient } from "../../lib/httpClient";
import type { WorkspaceSummary } from "./WorkspaceContext";

export interface Workspace {
  workspace_id: string;
  slug: string;
  name: string;
  created_at: string;
  billing_owner_user_id: string;
  plan_tier: string;
  stripe_customer_id: string | null;
  settings: Record<string, unknown>;
  archived_at: string | null;
}

export interface CreateWorkspaceBody {
  slug: string;
  name: string;
}

export async function listWorkspaces(): Promise<{
  workspaces: WorkspaceSummary[];
}> {
  return httpClient.get<{ workspaces: WorkspaceSummary[] }>(
    "/api/v2/workspaces",
  );
}

export async function createWorkspace(
  body: CreateWorkspaceBody,
): Promise<{ workspace: Workspace & { role: string } }> {
  return httpClient.post<{ workspace: Workspace & { role: string } }>(
    "/api/v2/workspaces",
    body,
  );
}

export interface UpdateWorkspaceBody {
  name?: string;
  slug?: string;
}

/**
 * Update a workspace's mutable fields. Admin or owner only on the
 * server side; the client doesn't pre-gate, so a viewer/member who
 * triggers this gets a 403 surfaced as ``HttpError`` for the caller.
 *
 * Server validates: at least one field must be present, slug shape
 * matches CreateWorkspace (3-40 lowercase + alphanumeric + hyphens,
 * no ``personal-*``). 409 ``workspace_slug_taken`` on collision.
 */
export async function updateWorkspace(
  workspaceId: string,
  body: UpdateWorkspaceBody,
): Promise<{ workspace: Workspace }> {
  return httpClient.patch<{ workspace: Workspace }>(
    `/api/v2/workspaces/${workspaceId}`,
    body,
  );
}

export interface WorkspaceMember {
  workspace_id: string;
  user_id: string;
  role: string;
  created_at: string;
  invited_by: string | null;
}

export async function getWorkspace(
  workspaceId: string,
): Promise<{
  workspace: Workspace;
  members: WorkspaceMember[];
  your_role: string;
}> {
  return httpClient.get(`/api/v2/workspaces/${workspaceId}`);
}

export async function inviteMember(
  workspaceId: string,
  body: { email: string; role: string },
): Promise<{
  invitation: {
    email: string;
    role: string;
    status: "added" | "queued" | "already_member";
  };
}> {
  return httpClient.post(
    `/api/v2/workspaces/${workspaceId}/members`,
    body,
  );
}

/**
 * Soft-delete a workspace (BE flips ``archived_at = NOW()``).
 *
 * Owner-only. The BE blocks the user's last active workspace with
 * a 409 ``last_active_workspace`` so they don't get locked out;
 * the FE additionally gates the Delete button when only one row
 * exists. The destructive blast radius is gated client-side
 * behind a type-"delete" confirmation dialog.
 */
export async function deleteWorkspace(
  workspaceId: string,
): Promise<{
  workspace: {
    workspace_id: string;
    slug: string;
    name: string;
    archived_at: string;
  };
}> {
  return httpClient.delete(`/api/v2/workspaces/${workspaceId}`);
}
