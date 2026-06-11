// Shared HTTP client for v4-onward API surfaces (workspaces,
// connectors, future feedback / prioritization / artifacts).
//
// W2 C4 watch points applied:
//
//  #1 X-Workspace-Id sourced from WorkspaceContext only.
//     The interceptor calls ``getActiveWorkspaceId()`` from
//     ``features/workspaces/WorkspaceContext`` — never reads URL
//     params or localStorage directly. The context is the source
//     of truth, initialized from the auth-resolved membership
//     list (so a user can never spoof a workspace they don't
//     belong to via URL mutation).
//
//  #2 Auth + bootstrap endpoints skip the X-Workspace-Id header.
//     Login / logout / signup / password-reset / email-confirm
//     run before workspace context exists. The skip list also
//     covers /api/health and /api/v2/workspaces (the list-mine +
//     create paths — neither needs a workspace context).
//
//  #3 Initialization race — block until workspace ready.
//     Non-skipped requests await ``workspaceReady()`` before
//     adding the header. Returns immediately once the first
//     listWorkspaces() response has hydrated the context (success
//     or empty). This means a request fired during the first
//     render won't ship without a header.
//
// The existing ``features/inspira/api.ts`` keeps its own helpers
// for the canvas surface — those are tightly coupled to the
// realtime / project-not-found dispatchers and will be migrated
// onto this client in W4 when the endpoint workspace-scope audit
// lands.

import { getActiveWorkspaceId, workspaceReady } from "../features/workspaces/WorkspaceContext";

export const API_BASE_URL =
  (import.meta.env.VITE_INSPIRA_API_URL as string | undefined) ??
  "http://127.0.0.1:4174";

// Path prefixes / exact matches that NEVER receive X-Workspace-Id.
// The order is "match against the path string before the query":
//   - prefix match: any path starting with the entry
//   - exact match: only the literal path (no trailing /)
//
// Bootstrap endpoints (auth flow, health, workspace list/create)
// run before / outside workspace context. Adding the header here
// would either be ignored by the backend or — worse — cause a
// 400 once we tighten the dependency to reject unknown header
// values on no-scope routes.
const SKIP_WORKSPACE_HEADER_PREFIXES: readonly string[] = [
  "/api/auth/",
  "/api/health",
];
const SKIP_WORKSPACE_HEADER_EXACT: readonly string[] = [
  // GET list-mine + POST create — neither needs a workspace context.
  // Per-workspace endpoints under /api/v2/workspaces/{id}/... DO
  // get the header (they're caught by current_workspace_member's
  // path-param resolution server-side, but the header is harmless
  // and useful for audit).
  "/api/v2/workspaces",
];

function shouldSkipWorkspaceHeader(path: string): boolean {
  // Strip query string for matching; don't let ?foo=bar trick the
  // skip-list.
  const pathname = path.split("?")[0];
  for (const prefix of SKIP_WORKSPACE_HEADER_PREFIXES) {
    if (pathname.startsWith(prefix)) return true;
  }
  return SKIP_WORKSPACE_HEADER_EXACT.includes(pathname);
}

/**
 * Build the headers map for a request, including X-Workspace-Id
 * when applicable. Awaits workspace context readiness for any
 * non-skipped path so we never send a request before the FE knows
 * which workspace to use.
 */
async function buildHeaders(
  path: string,
  init?: RequestInit,
  hasBody?: boolean,
): Promise<Headers> {
  const headers = new Headers(init?.headers ?? {});
  // Set Content-Type when there's a body — either init.body (raw fetch
  // override) OR the body param the caller passed to request() (the
  // common path for httpClient.post/patch/put). Without this, the
  // browser defaults to text/plain and FastAPI's strict-JSON Pydantic
  // bodies reject with 422 "Field required" because no JSON is parsed.
  // (Surfaced via wizard Step 1 #142.)
  if (
    !headers.has("Content-Type") &&
    (init?.body !== undefined || hasBody === true)
  ) {
    headers.set("Content-Type", "application/json");
  }
  if (shouldSkipWorkspaceHeader(path)) {
    return headers;
  }
  // Block until the workspace context has hydrated (auth-resolved →
  // listWorkspaces() returned). After this resolves, getActiveWorkspaceId
  // returns the persisted/auto-selected workspace_id or null (no
  // workspaces yet — anon or pre-create).
  await workspaceReady();
  const workspaceId = getActiveWorkspaceId();
  if (workspaceId) {
    headers.set("X-Workspace-Id", workspaceId);
  }
  return headers;
}

export class HttpError extends Error {
  constructor(
    public readonly status: number,
    public readonly path: string,
    public readonly detail: unknown,
  ) {
    super(
      `${path} failed: ${status} ${
        typeof detail === "string" ? detail : JSON.stringify(detail)
      }`,
    );
    this.name = "HttpError";
  }
}

async function parseError(response: Response, path: string): Promise<HttpError> {
  let detail: unknown;
  try {
    detail = await response.json();
  } catch {
    try {
      detail = await response.text();
    } catch {
      detail = response.statusText;
    }
  }
  return new HttpError(response.status, path, detail);
}

async function request<T>(
  method: "GET" | "POST" | "PATCH" | "PUT" | "DELETE",
  path: string,
  body?: unknown,
  init?: RequestInit,
): Promise<T> {
  const headers = await buildHeaders(path, init, body !== undefined);
  const finalInit: RequestInit = {
    ...init,
    method,
    headers,
    credentials: "include",
  };
  if (body !== undefined) {
    finalInit.body =
      typeof body === "string" ? body : JSON.stringify(body);
  }
  const response = await fetch(`${API_BASE_URL}${path}`, finalInit);
  if (!response.ok) {
    throw await parseError(response, path);
  }
  // Some routes return 204 / 202 with empty bodies; tolerate.
  const contentLength = response.headers.get("Content-Length");
  if (response.status === 204 || contentLength === "0") {
    return undefined as T;
  }
  return (await response.json()) as T;
}

export const httpClient = {
  get: <T>(path: string, init?: RequestInit) =>
    request<T>("GET", path, undefined, init),
  post: <T>(path: string, body?: unknown, init?: RequestInit) =>
    request<T>("POST", path, body, init),
  patch: <T>(path: string, body?: unknown, init?: RequestInit) =>
    request<T>("PATCH", path, body, init),
  put: <T>(path: string, body?: unknown, init?: RequestInit) =>
    request<T>("PUT", path, body, init),
  delete: <T>(path: string, init?: RequestInit) =>
    request<T>("DELETE", path, undefined, init),
};

// Test-only: surface the skip-list predicate so unit tests can
// assert on the auth-endpoint scoping without spinning up a fetch
// mock.
export const __testing__ = { shouldSkipWorkspaceHeader };
