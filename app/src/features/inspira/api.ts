// Thin wrapper around the Inspira backend HTTP surface.
//
// The backend service runs on http://127.0.0.1:4174 by default (see
// services/planning_studio_service/config.py). For Vite dev the frontend
// origin is different, so CORS is open at the server. No auth layer yet —
// the service is local-only for now.

import { getLocale } from "../../i18n";
import {
  getActiveWorkspaceId,
  workspaceReady,
} from "../workspaces/WorkspaceContext";

export const DEFAULT_BASE_URL =
  (import.meta.env.VITE_INSPIRA_API_URL as string | undefined) ??
  "http://127.0.0.1:4174";

export type Checkpoint = {
  id: string;
  question: string;
  status: "open" | "partial" | "answered";
  answered_in_turn_id?: string | null;
};

// Color tags a user can attach to a topic to visually group related topics
// on the canvas. All five slugs resolve to an existing theme CSS variable
// (``--sage``, ``--rust``, ``--gold``, ``--ink``, ``--paper``) so dark mode
// flips them automatically. The backend's allowlist must stay in lockstep
// with this — see ``PlanningStudioStore.TOPIC_COLOR_ALLOWLIST``.
export type TopicColor = "sage" | "rust" | "gold" | "ink" | "paper";

export type Topic = {
  topic_id: string;
  project_id: string;
  title: string;
  icon: string;
  position_x: number;
  position_y: number;
  status: "empty" | "in_progress" | "fleshed_out";
  order_index: number;
  origin: "planner_initial" | "planner_proposed" | "user_manual";
  metadata?: Record<string, unknown>;
  checkpoints?: Checkpoint[];
  // User-only note the planner never sees. `null` / `undefined` both mean
  // "no note set"; empty string on the wire is also treated as cleared.
  // Only the topic owner can read or write this field (IDOR-checked on
  // the server).
  private_notes?: string | null;
  // Optional color tag. Surfaced as a top-level field by the server but
  // actually persisted inside ``metadata.color`` — the two are kept in
  // sync automatically. ``null`` / ``undefined`` both mean "no color".
  color?: TopicColor | null;
  created_at: string;
  updated_at: string;
};

export type Relationship = {
  relationship_id: string;
  project_id: string;
  source_topic_id: string;
  target_topic_id: string;
  label: string | null;
  origin: "planner_inferred" | "user_drawn";
  strength: string | null;
  created_at: string;
};

export type KickoffRawResponse = {
  domain: string;
  domain_confidence: "high" | "medium" | "low";
  opening_card: { body: string };
  topics: Array<{ title: string; icon: string; why_this_topic: string }>;
  relationships: Array<{
    from_topic_title: string;
    to_topic_title: string;
    label: string | null;
  }>;
  suggested_first_topic: string;
  clarifying_question_if_too_vague: string | null;
  _sanitize?: Record<string, unknown>;
};

export type KickoffEnvelope = {
  kickoff: KickoffRawResponse;
  topics: Topic[];
  relationships: Relationship[];
};

export type ConflictResolution = {
  conflicting_decision_id: string;
  conflicting_topic_title: string;
  current_statement_summary: string;
  previous_statement_summary: string;
};

export type TopicTurn = {
  action: "ask" | "pressure_test" | "followup" | "suggest_close" | "resolve_conflict";
  question: string | null;
  why_this_matters: string | null;
  suggested_responses: Array<{ label: string; intent: string }>;
  proposed_decisions: Array<{
    statement: string;
    rationale: string | null;
    extracted_from_turn_id: string;
    target_topic_title: string | null;
  }>;
  consistency_flags: Array<{
    other_topic_title: string;
    other_decision_id: string;
    description: string;
  }>;
  new_topic_proposal: unknown;
  close_recommendation_reason: string | null;
  conflict_resolution: ConflictResolution | null;
  planned_checkpoints: Array<{ id: string; question: string }> | null;
  checkpoint_updates: Array<{ id: string; status: "open" | "partial" | "answered" }> | null;
  _sanitize?: Record<string, unknown>;
};

// B1.2 — per-decision provenance for canvas-review badges. Origin
// (planner vs user) already lives on `proposed_by`; this is the *temporal*
// + *sourcing* layer the planner write-path will populate. The half-fill
// badge renders when proposed_by === "planner" && humanEditedAt != null.
// All fields optional + backward-compatible — frontend mocks until the
// backend planner write emits them.
export type DecisionProvenance = {
  aiSeededAt?: string;
  humanEditedAt?: string;
  sources?: Array<{
    feedbackItemId: string;
    severity: number;
    excerpt: string;
  }>;
};

// Flat row returned by GET /projects/{id}/topics/{topic_id}/provenance.
// Used by the Topic Detail reasoning expander to populate the "Cited
// feedback items" section on cold-opens of completed canvases.
// Live runs populate the same map from `decision.drafted` SSE payloads,
// which carry less metadata (no feedback_item title/body); the FE falls
// back gracefully when those fields are missing.
export type TopicProvenanceRow = {
  decision_id: string;
  feedback_item_id: string;
  weight: number;
  feedback_item: {
    item_id: string;
    title: string;
    body: string;
    source: string;
    received_at: string | null;
    ingested_at: string;
  };
};

export type Decision = {
  decision_id: string;
  topic_id: string;
  project_id: string;
  statement: string;
  rationale: string | null;
  status: "proposed" | "confirmed" | "retracted";
  source_turn_id: string | null;
  proposed_by: "planner" | "user";
  confirmed_by_user_id: string | null;
  created_at: string;
  updated_at: string;
  retracted_at: string | null;
  provenance?: DecisionProvenance;
};

export type QnaTurn = {
  turn_id: string;
  topic_id: string;
  project_id: string;
  role: "planner" | "user";
  order_index: number;
  body: string;
  why_this_matters: string | null;
  action: string | null;
  suggested_responses: Array<{ label: string; intent: string }>;
  status: "open" | "answered" | "deferred" | "na";
  created_at: string;
};

export type ReroutedDecision = {
  decision_id: string;
  original_topic_id: string;
  actual_topic_id: string;
  actual_topic_title: string;
};

export type NewTopicCreated = {
  topic: Topic;
  relationships: Relationship[];
};

export type TopicDeletionSuggestion = {
  target_topic_id: string;
  target_topic_title: string;
  reason: string;
  superseded_by_decision: string | null;
};

export type TopicTurnEnvelope = {
  turn_result: TopicTurn;
  planner_turn: QnaTurn | null;
  rerouted_decisions: ReroutedDecision[];
  checkpoints: Checkpoint[];
  created_topic?: NewTopicCreated | null;
  topic_deletion_suggestion?: TopicDeletionSuggestion | null;
};

// ---- Typed API error classes -----------------------------------------------
//
// Thrown instead of a raw Error so callers can narrow and handle specific
// failure modes without string-parsing `err.message`.

/** Thrown when the backend reports 404 + error code "project_not_found"
 *  (or the equivalent topic/resource variant that implies the project is gone).
 *  InspiraApp's global `inspira:project-not-found` handler catches this,
 *  toasts a friendly message, and routes the user back to the projects list.
 */
export class ProjectNotFoundError extends Error {
  constructor(
    public readonly projectId: string,
    public readonly path: string,
  ) {
    super(`project_not_found: ${projectId}`);
    this.name = "ProjectNotFoundError";
  }
}

// ---- 401 interceptor -----------------------------------------------------
//
// When the backend returns 401 (session expired / cookie invalidated) from
// ANY authenticated request, we fire a `inspira:unauthorized` event on the
// window. InspiraApp listens for it and opens SessionExpiredModal. The
// `/api/auth/login` and `/api/auth/signup` endpoints are allow-listed:
// their 401 is meaningful to the AuthPanel form, not a session expiry.
const AUTH_PATHS_ALLOWED_401 = new Set([
  "/api/auth/login",
  "/api/auth/signup",
  "/api/auth/forgot-password",
  "/api/auth/reset-password",
]);

// Internal — exported so the comments module's standalone fetch
// wrapper (`comments/cascadeApi.ts`) routes 401s through the same
// SessionExpiredModal flow as the rest of the app.
export function maybeDispatchUnauthorized(path: string, status: number): void {
  if (status !== 401) return;
  if (AUTH_PATHS_ALLOWED_401.has(path)) return;
  if (typeof window === "undefined") return;
  try {
    window.dispatchEvent(new CustomEvent("inspira:unauthorized"));
  } catch {
    /* best effort — older browsers without CustomEvent ctor are n/a here */
  }
}

// Extract a project_id from a path like /api/v2/projects/project-abc123/...
// Returns the empty string when the path doesn't carry a project segment.
export function extractProjectIdFromPath(path: string): string {
  const m = path.match(/\/projects\/([^/]+)/);
  return m?.[1] ?? "";
}

// Returns true when the response body text indicates a project (or its
// parent resource) could not be found — covers both "project_not_found"
// and "topic_not_found" (topics belong to projects; if the topic is gone
// the project state is stale in the same way).
export function isProjectNotFoundBody(body: string): boolean {
  return (
    body.includes("project_not_found") ||
    body.includes("topic_not_found")
  );
}

// T5.2: dedup window for `inspira:project-not-found` dispatches.
// Parallel API calls (listTopics + listRelationships + listTurns
// firing concurrently) all see the same 404 and used to dispatch
// individual events, surfacing 2-3 stacked toasts. Track the last
// dispatched projectId + timestamp; skip if we already fired
// within DEDUP_MS for that same project.
const PROJECT_NOT_FOUND_DEDUP_MS = 1500;
let _lastProjectNotFoundProjectId: string | null = null;
let _lastProjectNotFoundAt = 0;

export function dispatchProjectNotFound(projectId: string): void {
  if (typeof window === "undefined") return;
  const now =
    typeof performance !== "undefined" ? performance.now() : Date.now();
  if (
    _lastProjectNotFoundProjectId === projectId &&
    now - _lastProjectNotFoundAt < PROJECT_NOT_FOUND_DEDUP_MS
  ) {
    // Same project, fired within the dedup window — drop this one.
    return;
  }
  _lastProjectNotFoundProjectId = projectId;
  _lastProjectNotFoundAt = now;
  try {
    window.dispatchEvent(
      new CustomEvent("inspira:project-not-found", {
        detail: { projectId },
      }),
    );
  } catch {
    /* best effort */
  }
}

// ---- LLM mode tracking (BYOK composer badge) ----------------------------
// Backend stamps every LLM-backed route response with `X-Inspira-Llm-Mode:
// house|byok`. We stash the latest value on the module so a small pill in
// the composer can render "House key" or "Your key" after each turn.
// Subscribers are notified on change only.

export type LlmMode = "house" | "byok";

let _lastLlmMode: LlmMode | null = null;
const _llmModeSubscribers = new Set<(mode: LlmMode | null) => void>();

function _setLlmMode(next: LlmMode | null): void {
  if (next === _lastLlmMode) return;
  _lastLlmMode = next;
  _llmModeSubscribers.forEach((fn) => {
    try {
      fn(next);
    } catch {
      /* subscriber errors must not break the fetch pipeline */
    }
  });
}

export function getLastLlmMode(): LlmMode | null {
  return _lastLlmMode;
}

export function subscribeLlmMode(
  fn: (mode: LlmMode | null) => void,
): () => void {
  _llmModeSubscribers.add(fn);
  return () => {
    _llmModeSubscribers.delete(fn);
  };
}

function _captureLlmModeFromHeader(res: Response): void {
  const header = res.headers.get("X-Inspira-Llm-Mode");
  if (header === "house" || header === "byok") {
    _setLlmMode(header);
  }
}

async function postJson<T>(
  path: string,
  body: Record<string, unknown>,
): Promise<T> {
  // Block until WorkspaceContext has hydrated, then stamp X-Workspace-Id
  // on the request. Without this, endpoints behind
  // ``current_workspace_member`` (start-canvas, github-pr export,
  // pr-verification, …) silently fall back to the user's
  // ``default_workspace_id`` and 404 when the active workspace differs.
  await workspaceReady();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  const wsId = getActiveWorkspaceId();
  if (wsId) headers["X-Workspace-Id"] = wsId;
  const res = await fetch(`${DEFAULT_BASE_URL}${path}`, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
    // Include the inspira_session cookie on cross-origin API calls so
    // the backend can identify the authenticated user.
    credentials: "include",
  });
  if (!res.ok) {
    maybeDispatchUnauthorized(path, res.status);
    const detail = await res.text();
    if (res.status === 404 && isProjectNotFoundBody(detail)) {
      const err = new ProjectNotFoundError(extractProjectIdFromPath(path), path);
      dispatchProjectNotFound(err.projectId);
      throw err;
    }
    // Error-message format note: the literal em-dash separator before
    // ``${detail}`` is consumed by ``exports/ExportModal.parseExportError``,
    // which regex-extracts the JSON tail to read ``code``/``provider`` and
    // render friendly inline errors. If you change this template, update
    // the parser in lockstep.
    throw new Error(
      `POST ${path} failed: ${res.status} ${res.statusText} — ${detail}`,
    );
  }
  _captureLlmModeFromHeader(res);
  return res.json() as Promise<T>;
}

// Mirror of `postJson` for HTTP PATCH. First introduced for L5a's
// `PATCH /api/v2/relationships/{id}` (relationship label edits).
// Identical error-handling shape so the call sites don't need to
// branch on verb.
async function patchJson<T>(
  path: string,
  body: Record<string, unknown>,
): Promise<T> {
  await workspaceReady();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  const wsId = getActiveWorkspaceId();
  if (wsId) headers["X-Workspace-Id"] = wsId;
  const res = await fetch(`${DEFAULT_BASE_URL}${path}`, {
    method: "PATCH",
    headers,
    body: JSON.stringify(body),
    credentials: "include",
  });
  if (!res.ok) {
    maybeDispatchUnauthorized(path, res.status);
    const detail = await res.text();
    if (res.status === 404 && isProjectNotFoundBody(detail)) {
      const err = new ProjectNotFoundError(extractProjectIdFromPath(path), path);
      dispatchProjectNotFound(err.projectId);
      throw err;
    }
    throw new Error(
      `PATCH ${path} failed: ${res.status} ${res.statusText} — ${detail}`,
    );
  }
  _captureLlmModeFromHeader(res);
  return res.json() as Promise<T>;
}

// ---- Server-Sent Events (SSE) reader ------------------------------------
// Phase 1 streaming endpoints (/kickoff/stream and /turn/stream) return a
// `text/event-stream` body whose first frame is a `heartbeat` event so the
// UI can flip to "AI is thinking…" within ~50ms. The full envelope still
// arrives in a `complete` event after the LLM round-trip lands.
//
// `ssePost` mirrors `postJson`'s contract on the success path: it resolves
// with the `complete` payload as a Promise<T>, and rejects on either a
// pre-stream 4xx (parsed the same way as postJson) or an `error` event.
// Heartbeats fire callbacks but do not resolve/reject.

export type HeartbeatEvent = { status: string; message: string };
export type SseErrorEvent = { code: string; message: string };

export type SseCallbacks<TComplete> = {
  onHeartbeat?: (data: HeartbeatEvent) => void;
  onComplete: (data: TComplete) => void;
  onError?: (data: SseErrorEvent) => void;
};

class SseProtocolError extends Error {
  constructor(message: string, public readonly payload?: SseErrorEvent) {
    super(message);
    this.name = "SseProtocolError";
  }
}

async function ssePost<TComplete>(
  path: string,
  body: Record<string, unknown>,
  callbacks: SseCallbacks<TComplete>,
  signal?: AbortSignal,
): Promise<TComplete> {
  const res = await fetch(`${DEFAULT_BASE_URL}${path}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
    },
    body: JSON.stringify(body),
    credentials: "include",
    signal,
  });

  // Pre-stream 4xx: same handling as postJson so error shapes (auth,
  // 404 project_not_found) round-trip identically.
  if (!res.ok) {
    maybeDispatchUnauthorized(path, res.status);
    const detail = await res.text();
    if (res.status === 404 && isProjectNotFoundBody(detail)) {
      const err = new ProjectNotFoundError(extractProjectIdFromPath(path), path);
      dispatchProjectNotFound(err.projectId);
      throw err;
    }
    throw new Error(
      `POST ${path} failed: ${res.status} ${res.statusText} — ${detail}`,
    );
  }

  // Capture LLM-mode header same as postJson — set by the streaming route
  // BEFORE the first byte of the stream so this reads correctly.
  _captureLlmModeFromHeader(res);

  if (!res.body) {
    throw new Error(
      `POST ${path}: response body is null — SSE not supported`,
    );
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  let resolved: TComplete | null = null;
  let resolvedFlag = false;

  const dispatchFrame = (rawFrame: string): SseErrorEvent | null => {
    // A frame is a sequence of "field: value" lines. We only care about
    // `event:` and `data:` here. `id:` and `retry:` are spec-defined but
    // unused in Phase 1.
    let eventName = "message";
    const dataLines: string[] = [];
    for (const line of rawFrame.split("\n")) {
      if (!line || line.startsWith(":")) continue; // SSE comment / blank
      const colonIdx = line.indexOf(":");
      const field = colonIdx === -1 ? line : line.slice(0, colonIdx);
      let value = colonIdx === -1 ? "" : line.slice(colonIdx + 1);
      if (value.startsWith(" ")) value = value.slice(1);
      if (field === "event") eventName = value;
      else if (field === "data") dataLines.push(value);
    }
    if (dataLines.length === 0) return null;
    const dataStr = dataLines.join("\n");
    let payload: unknown;
    try {
      payload = JSON.parse(dataStr);
    } catch {
      // Bad payload — surface as a protocol error so the caller knows.
      throw new SseProtocolError(
        `SSE frame had non-JSON data for event=${eventName}`,
      );
    }
    if (eventName === "heartbeat") {
      callbacks.onHeartbeat?.(payload as HeartbeatEvent);
    } else if (eventName === "complete") {
      resolved = payload as TComplete;
      resolvedFlag = true;
      callbacks.onComplete(payload as TComplete);
    } else if (eventName === "error") {
      const errPayload = payload as SseErrorEvent;
      callbacks.onError?.(errPayload);
      return errPayload;
    }
    return null;
  };

  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      // Multi-chunk frame handling: TCP can split a frame across reads.
      // We accumulate decoded bytes in `buffer`, then split on the SSE
      // frame terminator "\n\n". The trailing partial frame stays in
      // `buffer` until the next read completes it.
      buffer += decoder.decode(value, { stream: true });
      let sepIdx: number;
      // eslint-disable-next-line no-cond-assign
      while ((sepIdx = buffer.indexOf("\n\n")) !== -1) {
        const frame = buffer.slice(0, sepIdx);
        buffer = buffer.slice(sepIdx + 2);
        const errEvent = dispatchFrame(frame);
        if (errEvent) {
          throw new SseProtocolError(errEvent.message, errEvent);
        }
        if (resolvedFlag) {
          // Stop reading early once we have the complete payload —
          // the server should close the stream right after but we
          // don't need to wait for it.
          return resolved as TComplete;
        }
      }
    }
    // Stream ended. Flush any trailing frame that didn't have the
    // closing "\n\n" (servers should send it; defensive parse anyway).
    if (buffer.trim()) {
      const errEvent = dispatchFrame(buffer);
      if (errEvent) {
        throw new SseProtocolError(errEvent.message, errEvent);
      }
    }
    if (resolvedFlag) return resolved as TComplete;
    throw new SseProtocolError(
      `SSE stream for ${path} ended without a complete event`,
    );
  } finally {
    try {
      reader.releaseLock();
    } catch {
      /* lock already released */
    }
  }
}

async function getJson<T>(path: string): Promise<T> {
  await workspaceReady();
  const headers: Record<string, string> = {};
  const wsId = getActiveWorkspaceId();
  if (wsId) headers["X-Workspace-Id"] = wsId;
  const res = await fetch(`${DEFAULT_BASE_URL}${path}`, {
    credentials: "include",
    headers,
  });
  if (!res.ok) {
    maybeDispatchUnauthorized(path, res.status);
    const detail = await res.text();
    if (res.status === 404 && isProjectNotFoundBody(detail)) {
      const err = new ProjectNotFoundError(extractProjectIdFromPath(path), path);
      dispatchProjectNotFound(err.projectId);
      throw err;
    }
    throw new Error(
      `GET ${path} failed: ${res.status} ${res.statusText} — ${detail}`,
    );
  }
  _captureLlmModeFromHeader(res);
  return res.json() as Promise<T>;
}

export type TopicUpdate = Partial<{
  title: string;
  icon: string;
  position_x: number;
  position_y: number;
  status: Topic["status"];
}>;

// Source excerpt the user attached to a turn (file content, pasted snippet,
// etc.). Sent through to the planner so it can ground its question on the
// actual material. Excerpts are not persisted on the user turn — the
// frontend inlines the relevant bits into user_answer when needed.
export type AttachedSource = {
  display_name: string;
  kind: string; // "file:text", "file:pdf", "image", "url", etc.
  excerpt: string;
};

// An entry in the example project catalog. Used by the onboarding picker.
export type ExampleProjectCatalogItem = {
  slug: string;
  display_name: string;
  one_liner: string;
  topic_count: number;
};

// A v2 project record owned by the current user. Separate from the legacy
// v1 `Project` shape (which is hardcoded / seed data).
//
// ``shelf_id`` is ``null`` (or undefined on legacy test literals) when
// the project sits on the implicit "Unfiled" shelf. The ShelvesView
// treats both representations the same way.
//
// ``archived_at`` is ``null`` / undefined on active projects and carries
// an ISO-8601 timestamp on archived ones. The ProjectsListPage hides
// archived rows from the default grid and surfaces them in the separate
// "Archived projects" view. Archive is a weaker state than soft-delete;
// deleted projects never surface on either view.
export type V2Project = {
  project_id: string;
  user_id: string;
  title: string;
  metadata?: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  shelf_id?: string | null;
  archived_at?: string | null;
  // Only set on the Recently Deleted view payload.
  deleted_at?: string | null;
  days_remaining?: number;
  // v4 Kanban + state machine (W1, B3.3 / B1.1). All four fields land
  // via migration 20260504_0008; they're optional on the type so the
  // legacy ProjectsListPage code paths that don't touch state stay
  // structurally compatible.
  workspace_id?: string | null;
  project_state?: ProjectState;
  priority_order?: number | null;
  roi_score?: number | null;
};

export type ProjectState =
  | "pending_review"
  | "in_review"
  | "approved"
  | "rejected"
  | "summary_ready";

/** Kanban column ids — see `columnFor` in useKanbanData.ts.
 *
 * Founder rename 2026-05-04: column semantics simplified to:
 *   queue       — fresh shells awaiting AI pick-up
 *   in_progress — AI working OR Draft (canvas drafted, not yet
 *                 sent for review)
 *   in_review   — project_state=in_review
 *   approved    — project_state=approved (no PR pushed yet)
 *   shipped     — approved AND metadata.pr.pr_number is set */
export type KanbanColumn =
  | "queue"
  | "in_progress"
  | "in_review"
  | "approved"
  | "shipped";

// A shelf is a user-owned named container for grouping related projects.
// ``project_count`` is derived server-side (JOIN against v2_projects with
// ``deleted_at IS NULL``) so the frontend header chip doesn't need a
// second round-trip.
export type Shelf = {
  shelf_id: string;
  user_id: string;
  name: string;
  sort_order: number;
  created_at: string;
  updated_at: string;
  project_count: number;
};

// The authenticated (or fallback-system) user returned by /api/auth/me.
//
// ``deleted_at`` is populated when the account has been soft-deleted. The
// backend normally clears the session cookie alongside the delete, but we
// still surface the field so App.tsx can show the AccountDeactivatedPage
// as a safety net (e.g. a race where the cookie is still valid for a
// moment after a delete landed on another tab).
export type AuthedUser = {
  user_id: string;
  email: string;
  display_name: string;
  is_system: boolean;
  deleted_at?: string | null;
  // Set once the user creates / joins a workspace. The post-login Kanban
  // Workspace Home keys off this field; absent it the UI falls back to
  // the legacy ProjectsListPage. Always null for anon / system users.
  default_workspace_id?: string | null;
};

// ---- Security + sign-in session types (Stream 3) ------------------------
// Every method below is stubbed against a backend route that doesn't
// exist yet — calls 404 and the UI surfaces a "Coming soon" toast, matching
// the pattern already used by updateProfile / changePassword. The
// TypeScript types below define the expected shapes.

export type TwoFactorSetupResponse = {
  secret: string;
  qr_svg: string;
  recovery_codes: string[];
};

export type TwoFactorRecoveryResponse = {
  recovery_codes: string[];
};

export type AuthSessionRow = {
  id: string;
  device: string;
  location: string;
  ip: string;
  last_active: string; // ISO 8601
  current: boolean;
};

export type AuthSessionsResponse = {
  sessions: AuthSessionRow[];
};

// Email preferences — one record of three groups. ``security`` keys are
// read-only on the wire (UI shows them as always-on); the backend will
// reject any PATCH targeting group="security".
export type EmailPreferencesGroupKey =
  | "product"
  | "summaries"
  | "security";

export type EmailPreferences = {
  product: {
    weekly_digest: boolean;
    feature_launches: boolean;
    changelog: boolean;
  };
  summaries: {
    workspace_summary: boolean;
    project_activity: boolean;
  };
  security: {
    password_reset: boolean;
    new_device: boolean;
  };
};

// ---- Feedback widget types ----------------------------------------------

export type FeedbackType = "bug" | "idea" | "other";

export type FeedbackSubmission = {
  type: FeedbackType;
  message: string;
  // Data-URL-encoded screenshot when attached, else null. Kept tiny
  // and optional so the wire payload stays under ~1MB.
  screenshot?: string | null;
  follow_up_email?: string | null;
};

// ---- Personal Access Tokens (PATs) --------------------------------------
// Row shape returned by GET /api/v2/auth/tokens.  The backend strips the
// hash and raw token so this is safe to store in component state.
export type AccessTokenSummary = {
  token_id: string;
  name: string;
  scopes: string[];
  created_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
};

// ---- LLM model-tier picker ----------------------------------------------

// Tier slug shared with the backend (see agents/tiers.py::ModelTier).
// Stays a string union — no "enum" constant so the type survives JSON
// round-trips cleanly and stays narrow when callers spread it.
export type ModelTier = "base" | "pro" | "frontier";

// One row in the /api/v2/model-tiers catalog response.
export type ModelTierInfo = {
  slug: ModelTier;
  label: string;
  description: string;
  credit_multiplier: number;
  available: boolean;
};

// Full response of GET /api/v2/model-tiers.
export type ModelTierCatalog = {
  tiers: ModelTierInfo[];
  plan_slug: string;
  // The tier that would run if the user sends no per-turn override.
  current_default: ModelTier;
  // Raw persisted preference — may be null even if current_default is set.
  persisted_default: ModelTier | null;
  // The plan default ignoring any user preference. UX hint for "Pro
  // users default to gpt-5" etc.
  plan_default: ModelTier;
};

// ---- Per-user monthly usage (#080) --------------------------------------

// One row in GET /api/v2/auth/me/usage's tiers array.
export type TierUsageRow = {
  tier: ModelTier;
  used: number;
  cap: number;
  // 0..1 (rounded to 4 decimal places server-side). Frontend renders as
  // percent.
  percent: number;
};

export type BusinessPlanUsage = {
  used: number;
  cap: number;
  percent: number;
};

// Full response of GET /api/v2/auth/me/usage.
export type UsageView = {
  plan_slug: string;
  // Filtered by the user's plan: Free has BASE only; Pro adds PRO;
  // Frontier (slug "team") adds FRONTIER.
  tiers: TierUsageRow[];
  business_plan: BusinessPlanUsage;
};

// ---- BYOK (Bring Your Own Key) ------------------------------------------

// Two supported providers today. Keep in lockstep with
// services/planning_studio_service/byok.py::Provider.
export type ByokProvider = "openai" | "anthropic";

// Per-provider status as returned by GET /api/v2/auth/byok/status.
// `configured` is the UI affordance ("Verified on …" vs "Not configured").
// `last_verified_at` carries an ISO-8601 UTC timestamp of the most recent
// successful save. The raw API key is NEVER returned by any endpoint.
export type ByokStatusEntry = {
  configured: boolean;
  last_verified_at: string | null;
};

export type ByokStatus = {
  openai: ByokStatusEntry;
  anthropic: ByokStatusEntry;
};

// ---- Activity timeline types --------------------------------------------

/** One row in the Activity Timeline feed. Shape mirrors
 *  ``store.list_project_activity``'s return item. ``subject_title`` may be
 *  empty — the frontend falls back to a generic label in that case. */
export type ActivityEvent = {
  event_id: string;
  category:
    | "topic"
    | "relationship"
    | "decision"
    | "project"
    | "share"
    | "export";
  action: string;
  subject_id: string | null;
  subject_title: string;
  created_at: string;
  actor_display_name: string;
};

export type ActivityFeed = {
  events: ActivityEvent[];
  has_more: boolean;
};

// ---- Cross-project search types -----------------------------------------

export type SearchKind = "project" | "topic" | "decision" | "turn";

export type SearchHit = {
  kind: SearchKind;
  project_id: string;
  project_title: string;
  topic_id: string | null;
  topic_title: string | null;
  /** Short excerpt with the matched span; wrap in a <mark> after escaping. */
  snippet: string;
  matched_field: string;
};

export type SearchResponse = {
  hits: SearchHit[];
  truncated: boolean;
};

// PR 2: voice realtime types removed with the rest of the voice
// feature scrap. Plan-tier entitlements live in the EntitlementsResponse
// below + are returned from GET /api/v2/entitlements.

export type EntitlementsResponse = {
  // "team" is the slug; the display label is "Frontier" per #081's
  // rebrand. "enterprise" is the top tier — wired through agents/tiers.py
  // and entitlements.PLAN_TIERS (added 2026-05-04 to close #159 gap).
  plan: "free" | "pro" | "team" | "enterprise";
  features: string[];
};

// ---- Documents (#094 / Item 3 redesign) -----------------------------
// One-shot async long-form doc generation, replacing the per-phase
// BusinessPlan flow. Doc-type derived from project.metadata.domain via
// DOMAIN_TO_DOC_TYPE (FE mirror in ./docTypeMap.ts). 7 doc types in v1.
// POST /document/generate returns 202 + document_id; FE polls GET
// /document/{document_id} every ~2s until status flips off "in_progress"
// (mirror Next Steps poller). PATCH /document/{document_id}/section/
// {section_id} for user inline edits — no LLM, no cap. Cap shares the
// existing business_plan_usage table (Pro 1/mo any doc type, Frontier
// 100/mo). Strict cap-gate: ANY POST 429s when at limit. Increment ONLY
// on first generation of a new (project_id, doc_type) pair (Option C).

/** The 7 v1 doc types. Mirror of services/.../store.py:VALID_DOC_TYPES.
 *  Career and personal domains are unmapped and surface as a friendly
 *  "no doc type for this project type yet" panel (DocumentDomainNotMappedError). */
export type DocType =
  | "business_plan"
  | "prd"
  | "story_outline"
  | "event_plan"
  | "marketing_plan"
  | "research_proposal"
  | "course_outline";

/** A single section within a long-form document. Section_ids are
 *  enforced server-side against the canonical-list-per-doc-type
 *  (services/.../agents/schemas.py:DOCUMENT_CANONICAL_SECTIONS). */
export type DocumentSection = {
  section_id: string;
  title: string;
  prose_markdown: string;
  key_points: string[];
  cited_topics: string[];
};

/** Parsed content payload (NOT a JSON string). The BE parses
 *  content_json once before returning to the FE — see
 *  api.py:_document_to_view. Always present once status === "completed";
 *  null while in_progress or on failed. */
export type DocumentContent = {
  doc_type: DocType;
  sections: DocumentSection[];
};

/** GET /document/{id} + GET /document (latest) response shape.
 *  Mirrors api.py:_document_to_view. */
export type DocumentView = {
  document_id: string;
  project_id: string;
  doc_type: DocType;
  status: "in_progress" | "completed" | "failed";
  content: DocumentContent | null;
  error_message: string | null;
  model_id: string;
  plan_tier: string;
  output_tokens_estimate: number;
  generated_at: string;
  completed_at: string | null;
};

/** POST /document/generate response — 202 with the document_id to
 *  poll. `already_in_flight: true` indicates the BE found an existing
 *  in-progress doc for (project_id, doc_type) and returned it instead
 *  of starting a new one (idempotency). */
export type DocumentGenerateResponse = {
  document_id: string;
  status: "in_progress";
  already_in_flight?: boolean;
};

/** PATCH body shape for inline section edits. At least one field must
 *  be present (BE 422s on empty body). */
export type DocumentSectionPatchBody = {
  title?: string;
  prose_markdown?: string;
};

// ---- Artifact Viewer types ----------------------------------------------

export type ArtifactFile = {
  path: string;
  content: string;
};

export type ArtifactChatMessage = {
  role: "assistant" | "user";
  body: string;
  ts: string;
};

export type ArtifactPayload = {
  latest_scaffold_id: string | null;
  model_used: string | null;
  framework: string;
  language: string;
  files: ArtifactFile[];
  messages: ArtifactChatMessage[];
};

/** Wave F.4 — inline IDE-style comment on a line of generated scaffold
 *  code. Anchor key is ``(file_path, line_number, line_content_hash)``;
 *  ``line_content_hash`` is SHA-256 over the line's raw UTF-8 bytes
 *  truncated to 16 hex chars. The FE recomputes the current line's
 *  hash on render to detect drift ("stale" outline). */
export type ArtifactCommentCategory =
  | "question"
  | "concern"
  | "suggest_fix";

export type ArtifactComment = {
  comment_id: string;
  project_id: string;
  file_path: string;
  line_number: number;
  line_content_hash: string;
  category: ArtifactCommentCategory;
  body: string;
  author_user_id: string;
  parent_comment_id: string | null;
  resolved_at: string | null;
  created_at: string;
  updated_at: string;
};

/** Typed errors for the Document* endpoints. Mirror BusinessPlan*Error
 *  conventions (extends Error, named, public-readonly constructor args)
 *  so InspiraApp can branch via `instanceof` rather than message strings. */

/** 402 — Free user hitting any Document endpoint. */
export class DocumentPlanRequiredError extends Error {
  constructor(public readonly minPlan: string = "pro") {
    super(`document requires plan: ${minPlan}`);
    this.name = "DocumentPlanRequiredError";
  }
}

/** 429 — strict-block cap-gate (Pro 1/mo, Frontier 100/mo). The BE
 *  returns current_count + cap + plan_slug + doc_type so the FE can
 *  render "1/1 plans this month" copy without separate plumbing. */
export class DocumentCapReachedError extends Error {
  constructor(
    public readonly currentCount: number,
    public readonly cap: number,
    public readonly planSlug: string = "",
    public readonly docType: string = "",
  ) {
    super(`document cap reached: ${currentCount}/${cap}`);
    this.name = "DocumentCapReachedError";
  }
}

/** 409 — lost-lock fallback (cooperative advisory lock failed). Rare;
 *  the common in-flight case is a 202 with `already_in_flight: true`. */
export class DocumentInFlightError extends Error {
  constructor(public readonly docType: string = "") {
    super(`another document generation is in flight on this project`);
    this.name = "DocumentInFlightError";
  }
}

/** 422 — project domain unmapped in DOMAIN_TO_DOC_TYPE (career,
 *  personal). Surfaces a friendly "no doc type for this project type
 *  yet" message; user changes domain via separate flow. */
export class DocumentDomainNotMappedError extends Error {
  constructor(public readonly domain: string = "") {
    super(`document doc-type unmapped for domain: ${domain}`);
    this.name = "DocumentDomainNotMappedError";
  }
}

/** 422 — POST /document/generate received a doc_type override that
 *  isn't in VALID_DOC_TYPES (e.g. typo, future doc-type the BE
 *  doesn't support yet). Distinct from DocumentDomainNotMappedError
 *  (which fires when no override is sent and the project's domain
 *  is unmapped). */
export class DocumentInvalidDocTypeError extends Error {
  constructor(public readonly attemptedDocType: string = "") {
    super(`document doc_type override invalid: ${attemptedDocType}`);
    this.name = "DocumentInvalidDocTypeError";
  }
}

/** 404 — document_id not found, project mismatch, user mismatch, or
 *  (PATCH only) section_id not in canonical list for this doc_type. */
export class DocumentNotFoundError extends Error {
  constructor(
    public readonly docType: string | null = null,
    public readonly sectionId: string | null = null,
  ) {
    super(
      sectionId
        ? `document section not found: ${sectionId}`
        : `document not found${docType ? ` (doc_type=${docType})` : ""}`,
    );
    this.name = "DocumentNotFoundError";
  }
}

export const api = {
  // ---- Auth -------------------------------------------------------------
  me: (): Promise<AuthedUser> => getJson("/api/auth/me"),
  signup: (input: {
    email: string;
    password: string;
    display_name?: string;
    terms_accepted?: boolean;
  }): Promise<AuthedUser> => postJson("/api/auth/signup", input),
  login: (input: { email: string; password: string }): Promise<AuthedUser> =>
    postJson("/api/auth/login", input),
  logout: (): Promise<{ logged_out: boolean }> =>
    postJson("/api/auth/logout", {}),

  // Password-reset flow. Both endpoints are allow-listed from the 401
  // interceptor because they are unauthenticated by definition.
  // forgotPassword ALWAYS returns { ok: true } — the backend never reveals
  // whether the email exists (enum-defense). Any non-2xx is re-thrown as a
  // plain Error so callers show the generic error copy.
  forgotPassword: (email: string): Promise<{ ok: true }> =>
    postJson<{ ok: true }>("/api/auth/forgot-password", { email }),

  resetPassword: (token: string, new_password: string): Promise<{ ok: true }> =>
    postJson<{ ok: true }>("/api/auth/reset-password", { token, new_password }),

  // ---- Anonymous → account transfer ------------------------------------
  // Moves every v2 project (+ topics / turns / decisions / etc.) from the
  // caller's previous anonymous session onto their newly-signed-up real
  // account. Called by InspiraApp after a successful signup when the
  // caller had an anon id on the prior session — we pass that id in the
  // body; the backend validates it against the signed session's
  // ``previous_anon_user_id`` stamp before running the UPDATE.
  transferAnonymousProjects: (
    anonymousUserId: string,
  ): Promise<{ transferred: number }> =>
    postJson("/api/v2/auth/transfer-anonymous-projects", {
      anonymous_user_id: anonymousUserId,
    }),

  // ---- Account self-service (backend routes pending) -------------------
  // These endpoints are not wired on the backend yet. Frontend calls will
  // surface a 404 via the normal Error.message shape; callers detect the
  // 404 and show a "Coming soon" toast so the UI is already in place when
  // the backend lands the routes.
  updateProfile: (input: {
    display_name?: string;
  }): Promise<{ user: AuthedUser }> => postJson("/api/auth/profile", input),
  changePassword: (input: {
    current_password: string;
    new_password: string;
  }): Promise<{ ok: boolean }> => postJson("/api/auth/change-password", input),
  deleteAccount: (input: {
    password_confirmation: string;
  }): Promise<{ deleted: boolean }> =>
    postJson("/api/auth/delete-account", input),

  // ---- Personal Access Tokens (PATs) -----------------------------------
  // External automations (Zapier, the Inspira MCP server, a user's own
  // script) authenticate via ``Authorization: Bearer inspira_pat_<raw>``.
  // Tokens are minted from Account Settings → "API tokens".  The raw
  // token is returned ONCE on mint; the frontend must show it in a
  // copy-once dialog and then forget it.
  mintAccessToken: (
    name: string,
  ): Promise<{
    token_id: string;
    name: string;
    token: string;
    created_at: string | null;
  }> => postJson("/api/v2/auth/tokens", { name }),

  listAccessTokens: (): Promise<{ tokens: AccessTokenSummary[] }> =>
    getJson("/api/v2/auth/tokens"),

  // DELETE /api/v2/auth/tokens/{token_id}.  The postJson helper is POST
  // only, so we fall back to a direct fetch.  IDOR-checked on the
  // server; a 404 means "not yours or doesn't exist".
  revokeAccessToken: async (tokenId: string): Promise<void> => {
    const path = `/api/v2/auth/tokens/${encodeURIComponent(tokenId)}`;
    const res = await fetch(`${DEFAULT_BASE_URL}${path}`, {
      method: "DELETE",
      credentials: "include",
    });
    if (!res.ok) {
      const detail = await res.text();
      throw new Error(
        `DELETE ${path} failed: ${res.status} ${res.statusText} — ${detail}`,
      );
    }
  },

  // ---- Projects (v2, user-scoped) --------------------------------------
  listV2Projects: (): Promise<{ projects: V2Project[] }> =>
    getJson("/api/v2/projects"),
  createV2Project: (title: string): Promise<{ project: V2Project }> =>
    postJson("/api/v2/projects", { title }),

  // v4 flow — extract themes from pasted customer feedback. The
  // PasteFeedbackDialog calls this on Submit, then fires one kickoff
  // per returned theme to auto-generate one project per theme on the
  // workspace home.
  extractThemes: (
    items: string[],
  ): Promise<{
    themes: { title: string; summary: string; source_indices: number[] }[];
    total_items: number;
  }> =>
    postJson("/api/v2/feedback/extract-themes", {
      items,
      locale: getLocale(),
    }),
  renameV2Project: (
    projectId: string,
    title: string,
  ): Promise<{ project: V2Project }> =>
    postJson(`/api/v2/projects/${projectId}/update`, { title }),
  deleteV2Project: (
    projectId: string,
  ): Promise<{ deleted: boolean; project_id: string }> =>
    postJson(`/api/v2/projects/${projectId}/delete`, {}),
  bulkDeleteV2Projects: (
    projectIds: string[],
  ): Promise<{ ok: boolean; deleted: number }> =>
    postJson(`/api/v2/projects/bulk-delete`, { project_ids: projectIds }),
  // Deep-clones an owned project: copies topics (with positions),
  // relationships, decisions, open questions, risks, and Q&A turns; a
  // " (copy)" suffix is appended to the title. shelf_id is NOT copied
  // (the duplicate starts on "Unfiled") and no share token is minted.
  // Server returns the full envelope as ``{project: V2Project}`` with
  // 201; this helper unwraps the envelope so callers see just the
  // project, matching the spec contract
  // ``api.duplicateProject(id) -> Promise<V2Project>``.
  duplicateProject: async (projectId: string): Promise<V2Project> => {
    const envelope = await postJson<{ project: V2Project }>(
      `/api/v2/projects/${projectId}/duplicate`,
      {},
    );
    return envelope.project;
  },

  // ---- Project archiving (v2, user-scoped) -----------------------------
  // Archive is a softer middle ground than delete — the project row stays
  // intact (topics, decisions, Q&A, share tokens all preserved) but falls
  // out of the default projects list. The user can restore via unarchive
  // or still delete if they're sure. Every route is authenticated and
  // user-scoped; cross-user attempts resolve to 404 for IDOR hygiene.
  archiveProject: (
    projectId: string,
  ): Promise<{ project: V2Project }> =>
    postJson(`/api/v2/projects/${projectId}/archive`, {}),

  unarchiveProject: (
    projectId: string,
  ): Promise<{ project: V2Project }> =>
    postJson(`/api/v2/projects/${projectId}/unarchive`, {}),

  listArchivedProjects: (): Promise<{ projects: V2Project[] }> =>
    getJson("/api/v2/projects/archived"),

  // B2.3 — promote a feedback cluster into a new project. Stub:
  // backend route is not yet wired. On 404, the POST resolves to a normal
  // Error and the dialog surfaces it inline; the user retains all their
  // typed state. Returns the newly-created project so the caller can
  // navigate to its canvas in pending_review state.
  promoteToProject: (input: {
    cluster_id: string | null;
    project_title: string;
    topic_seeds: Array<{ name: string; desc: string }>;
    feedback_item_id?: string | null;
  }): Promise<{ project: V2Project }> =>
    postJson(`/api/v2/projects/promote-from-cluster`, input),

  // ---- Recently Deleted (soft-delete with grace) ----------------------
  // Soft-deleted projects stay recoverable for INSPIRA_DELETED_PROJECT_GRACE_DAYS
  // (default 30). The list returns each row enriched with `deleted_at` and a
  // computed `days_remaining`. Restore brings the row back into the active
  // list (200 with the project), or 410 Gone if past grace, or 404 if not
  // owned / not found. Purge is a hard-delete and only allowed on already-
  // soft-deleted rows (returns 204 No Content).
  listRecentlyDeletedProjects: (): Promise<{ projects: V2Project[] }> =>
    getJson("/api/v2/projects/recently-deleted"),

  restoreProject: (
    projectId: string,
  ): Promise<{ project: V2Project }> =>
    postJson(`/api/v2/projects/${projectId}/restore`, {}),

  purgeProject: (projectId: string): Promise<void> =>
    postJson(`/api/v2/projects/${projectId}/purge`, {}),

  // ---- Workspace Kanban + state machine (W1/W2, B3.3 / B1.1) -----------
  // ``listWorkspaceProjects`` is the single GET behind the Kanban; the
  // server returns all 5 columns sorted by (priority_order ASC NULLS
  // LAST, roi_score DESC, created_at DESC). Client groups by
  // project_state. Optional ``state`` filter narrows for column-
  // specific polls (e.g. an "AI thinking" refresh button).
  listWorkspaceProjects: (
    workspaceId: string,
    state?: ProjectState,
  ): Promise<{ projects: V2Project[] }> => {
    const url = state
      ? `/api/v2/workspaces/${workspaceId}/projects?state=${encodeURIComponent(state)}`
      : `/api/v2/workspaces/${workspaceId}/projects`;
    return getJson(url);
  },

  // ``/transition`` is the verb-style state move: 200 on legal, 409
  // on illegal with {error: "illegal_transition", current, attempted}.
  // Cross-column drag goes through manualStateOverride instead so
  // the audit trail records the override + note.
  transitionProjectState: (
    projectId: string,
    action: "start_review" | "approve" | "reject",
  ): Promise<{ project: V2Project }> =>
    postJson(`/api/v2/projects/${projectId}/transition`, { action }),

  // Manual override is the escape hatch — required note explains
  // why the human bypassed the AI's last call. Empty note returns
  // 400 server-side; the dialog enforces it client-side too.
  manualStateOverrideProject: (
    projectId: string,
    targetState: ProjectState,
    note: string,
  ): Promise<{ project: V2Project }> =>
    postJson(
      `/api/v2/projects/${projectId}/manual-state-override`,
      { target_state: targetState, note },
    ),

  // Same-column drag persists a sparse 1024-step int. NULL means
  // "use ROI sort"; any non-null wins.
  manualPriorityOrderProject: (
    projectId: string,
    priorityOrder: number,
  ): Promise<{ project: V2Project }> =>
    postJson(
      `/api/v2/projects/${projectId}/manual-priority-order`,
      { priority_order: priorityOrder },
    ),

  // ---- Shelves (v2, user-scoped) ---------------------------------------
  // A shelf is a named grouping of related projects. Every route is
  // authenticated and scoped by user_id; cross-user attempts resolve to
  // 404 for IDOR hygiene. Passing `shelfIdOrNull=null` to
  // `moveProjectToShelf` un-shelves the project (falls onto "Unfiled").
  listShelves: (): Promise<{ shelves: Shelf[] }> =>
    getJson("/api/v2/shelves"),
  createShelf: (name: string): Promise<{ shelf: Shelf }> =>
    postJson("/api/v2/shelves", { name }),
  renameShelf: (
    shelfId: string,
    name: string,
  ): Promise<{ shelf: Shelf }> =>
    postJson(`/api/v2/shelves/${shelfId}/update`, { name }),
  deleteShelf: (
    shelfId: string,
  ): Promise<{ deleted: boolean; shelf_id: string }> =>
    postJson(`/api/v2/shelves/${shelfId}/delete`, {}),
  moveProjectToShelf: (
    projectId: string,
    shelfIdOrNull: string | null,
  ): Promise<{ project: V2Project }> =>
    postJson(`/api/v2/projects/${projectId}/shelve`, {
      shelf_id: shelfIdOrNull,
    }),

  // URL-fetch proxy — the browser can't fetch most sites directly (CORS),
  // so we proxy through the backend. Returns an AttachedSource shape
  // ready to hand to the planner. See services/planning_studio_service/
  // fetchers/url.py for the server-side safety guards.
  fetchUrl: (url: string): Promise<AttachedSource> =>
    postJson("/api/v2/fetch-url", { url }),

  kickoff: (
    projectId: string,
    userIdea: string,
    attachedSources?: AttachedSource[],
    modelTier?: ModelTier | null,
  ): Promise<KickoffEnvelope> => {
    // Audit 2026-04-25 found //kickoff/stream 404s in prod logs — empty
    // projectId silently produces /api/v2/projects//kickoff which the
    // backend serves as 404. Loud client-side error beats silent 404.
    if (!projectId) {
      throw new Error("api.kickoff: projectId is required");
    }
    return postJson(`/api/v2/projects/${projectId}/kickoff`, {
      user_idea: userIdea,
      attached_sources: attachedSources ?? [],
      locale: getLocale(),
      // Optional per-turn tier override; omit the key when null so the
      // backend uses the persisted default or plan default.
      ...(modelTier ? { model_tier: modelTier } : {}),
    });
  },

  topicTurn: (
    topicId: string,
    userAnswer?: string,
    attachedSources?: AttachedSource[],
    modelTier?: ModelTier | null,
  ): Promise<TopicTurnEnvelope> => {
    if (!topicId) {
      throw new Error("api.topicTurn: topicId is required");
    }
    return postJson(`/api/v2/topics/${topicId}/turn`, {
      user_answer: userAnswer ?? "",
      attached_sources: attachedSources ?? [],
      locale: getLocale(),
      ...(modelTier ? { model_tier: modelTier } : {}),
    });
  },

  // ---- Phase 1 SSE streaming variants ---------------------------------
  // Same envelope shapes as `kickoff` / `topicTurn`. The win is that
  // `onHeartbeat` fires within ~50ms of the request leaving so the UI can
  // flip to "AI is thinking…" instead of staring at a blank wait. The
  // returned promise still resolves with the full envelope once the
  // server emits the `complete` event. The non-streaming methods above
  // remain available as a fallback when the backend feature flag is off.
  kickoffStream: (
    projectId: string,
    userIdea: string,
    attachedSources?: AttachedSource[],
    modelTier?: ModelTier | null,
    callbacks?: { onHeartbeat?: (data: HeartbeatEvent) => void },
    signal?: AbortSignal,
  ): Promise<KickoffEnvelope> => {
    if (!projectId) {
      return Promise.reject(
        new Error("api.kickoffStream: projectId is required"),
      );
    }
    return new Promise<KickoffEnvelope>((resolve, reject) => {
      ssePost<KickoffEnvelope>(
        `/api/v2/projects/${projectId}/kickoff/stream`,
        {
          user_idea: userIdea,
          attached_sources: attachedSources ?? [],
          locale: getLocale(),
          ...(modelTier ? { model_tier: modelTier } : {}),
        },
        {
          onHeartbeat: callbacks?.onHeartbeat,
          onComplete: (data) => resolve(data),
          onError: (err) => reject(new Error(err.message)),
        },
        signal,
      ).catch(reject);
    });
  },

  topicTurnStream: (
    topicId: string,
    userAnswer?: string,
    attachedSources?: AttachedSource[],
    modelTier?: ModelTier | null,
    callbacks?: { onHeartbeat?: (data: HeartbeatEvent) => void },
    signal?: AbortSignal,
  ): Promise<TopicTurnEnvelope> =>
    new Promise<TopicTurnEnvelope>((resolve, reject) => {
      ssePost<TopicTurnEnvelope>(
        `/api/v2/topics/${topicId}/turn/stream`,
        {
          user_answer: userAnswer ?? "",
          attached_sources: attachedSources ?? [],
          locale: getLocale(),
          ...(modelTier ? { model_tier: modelTier } : {}),
        },
        {
          onHeartbeat: callbacks?.onHeartbeat,
          onComplete: (data) => resolve(data),
          onError: (err) => reject(new Error(err.message)),
        },
        signal,
      ).catch(reject);
    }),

  // ---- LLM model-tier picker ------------------------------------------
  listModelTiers: (): Promise<ModelTierCatalog> =>
    getJson("/api/v2/model-tiers"),

  // GET /api/v2/auth/me/usage — per-user monthly usage view (#080).
  // Returns per-tier {used, cap, percent} filtered by the user's plan
  // (Free sees BASE only; Pro adds PRO; Frontier adds FRONTIER) plus
  // a separate business_plan {used, cap, percent} bucket.
  getUsage: (): Promise<UsageView> =>
    getJson("/api/v2/auth/me/usage"),

  // PATCH /api/v2/auth/me/preferred-model-tier. Pass `null` to clear.
  // Uses a direct fetch because the helper library ships only POST + GET.
  setPreferredModelTier: async (
    tier: ModelTier | null,
  ): Promise<{ tier: ModelTier | null; cleared?: boolean }> => {
    const path = "/api/v2/auth/me/preferred-model-tier";
    const res = await fetch(`${DEFAULT_BASE_URL}${path}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tier }),
      credentials: "include",
    });
    if (!res.ok) {
      const detail = await res.text();
      throw new Error(
        `PATCH ${path} failed: ${res.status} ${res.statusText} — ${detail}`,
      );
    }
    return res.json();
  },

  // ---- BYOK (Bring Your Own Key) --------------------------------------
  // Save + verify a provider key. The backend PINGS the provider with
  // the key first — a rejection surfaces here as a thrown Error with a
  // message containing "key_verification_failed" so the UI can show a
  // targeted "that key didn't authenticate" toast.
  saveByokKey: async (
    provider: ByokProvider,
    key: string,
  ): Promise<{ provider: ByokProvider; verified_at: string | null }> =>
    postJson("/api/v2/auth/byok", { provider, api_key: key }),

  // DELETE is rare enough that we keep it as a bespoke fetch call rather
  // than expand the helper library.
  removeByokKey: async (provider: ByokProvider): Promise<void> => {
    const path = `/api/v2/auth/byok/${encodeURIComponent(provider)}`;
    const res = await fetch(`${DEFAULT_BASE_URL}${path}`, {
      method: "DELETE",
      credentials: "include",
    });
    if (!res.ok) {
      const detail = await res.text();
      throw new Error(
        `DELETE ${path} failed: ${res.status} ${res.statusText} — ${detail}`,
      );
    }
  },

  getByokStatus: (): Promise<ByokStatus> =>
    getJson("/api/v2/auth/byok/status"),

  listTopics: (projectId: string): Promise<{ topics: Topic[] }> =>
    getJson(`/api/v2/projects/${projectId}/topics`),

  createTopic: (
    projectId: string,
    input: { title: string; icon?: string; position_x?: number; position_y?: number },
  ): Promise<{ topic: Topic }> =>
    postJson(`/api/v2/projects/${projectId}/topics`, input),

  updateTopic: (
    topicId: string,
    updates: TopicUpdate,
  ): Promise<{ topic: Topic }> =>
    postJson(`/api/v2/topics/${topicId}/update`, updates),

  deleteTopic: (
    topicId: string,
  ): Promise<{ deleted: boolean; topic_id: string }> =>
    postJson(`/api/v2/topics/${topicId}/delete`, {}),

  // Shallow duplicate — creates a sibling topic with a " (copy)" suffix
  // in the same project, offset +40px/+40px from the source position. No
  // relationships, decisions, or Q&A turns are carried over. Returns
  // 201 from the server; the helper unwraps the envelope so callers see
  // just the new Topic row, matching the pattern of `duplicateProject`.
  duplicateTopic: async (topicId: string): Promise<Topic> => {
    const envelope = await postJson<{ topic: Topic }>(
      `/api/v2/topics/${topicId}/duplicate`,
      {},
    );
    return envelope.topic;
  },

  closeTopic: (
    topicId: string,
  ): Promise<{ topic: Topic }> =>
    postJson(`/api/v2/topics/${topicId}/close`, {}),

  // (B3 / #077) — `updateTopicPrivateNotes` removed along with the
  // Private Notes panel in the topic-detail drawer (product
  // decision). Backend route + `private_notes` column are preserved so
  // existing user data stays on disk; nothing in the FE writes to it.

  // Tag a topic with one of the five palette colors, or clear the tag with
  // ``null``. Invalid slugs return 400 so the caller never persists a
  // value the theme has no variable for.
  updateTopicColor: (
    topicId: string,
    color: TopicColor | null,
  ): Promise<{ topic: Topic }> =>
    postJson(`/api/v2/topics/${topicId}/color`, { color }),

  listDecisions: (
    topicId: string,
  ): Promise<{ decisions: Decision[] }> =>
    getJson(`/api/v2/topics/${topicId}/decisions`),

  listProjectDecisions: (
    projectId: string,
  ): Promise<{ decisions: Decision[] }> =>
    getJson(`/api/v2/projects/${projectId}/decisions`),

  listTopicProvenance: (
    projectId: string,
    topicId: string,
  ): Promise<{ provenance: TopicProvenanceRow[] }> =>
    getJson(
      `/api/v2/projects/${projectId}/topics/${topicId}/provenance`,
    ),

  createDecision: (
    topicId: string,
    input: {
      statement: string;
      rationale?: string | null;
      source_turn_id?: string | null;
      proposed_by?: "planner" | "user";
      status?: "proposed" | "confirmed" | "retracted";
    },
  ): Promise<{ decision: Decision }> =>
    postJson(`/api/v2/topics/${topicId}/decisions`, input),

  deleteDecision: (
    decisionId: string,
  ): Promise<{ deleted: boolean; decision_id: string }> =>
    postJson(`/api/v2/decisions/${decisionId}/delete`, {}),

  listRelationships: (
    projectId: string,
  ): Promise<{ relationships: Relationship[] }> =>
    getJson(`/api/v2/projects/${projectId}/relationships`),

  createRelationship: (
    projectId: string,
    input: {
      source_topic_id: string;
      target_topic_id: string;
      label?: string | null;
    },
  ): Promise<{ relationship: Relationship }> =>
    postJson(`/api/v2/projects/${projectId}/relationships`, input),

  deleteRelationship: (
    relationshipId: string,
  ): Promise<{ deleted: boolean; relationship_id: string }> =>
    postJson(`/api/v2/relationships/${relationshipId}/delete`, {}),

  // L5a — PATCH the label on an existing relationship. `null` clears
  // the label so the edge renders unlabeled. Empty strings are also
  // accepted (backend normalizes to NULL).
  updateRelationshipLabel: (
    relationshipId: string,
    label: string | null,
  ): Promise<{ relationship: Relationship }> =>
    patchJson(`/api/v2/relationships/${relationshipId}`, { label }),

  listTurns: (topicId: string): Promise<{ turns: QnaTurn[] }> =>
    getJson(`/api/v2/topics/${topicId}/turns`),

  // ---- Activity timeline ---------------------------------------------
  // Paged audit-log feed for the Activity panel on the canvas. Internal
  // categories (system / auth / admin) are filtered server-side; only
  // user-visible categories come back. ``limit`` and ``offset`` map
  // directly to the URL query string — the server clamps unreasonable
  // values so ad-hoc clients can't blow up the database.
  listProjectActivity: (
    projectId: string,
    options?: { limit?: number; offset?: number },
  ): Promise<ActivityFeed> => {
    const params = new URLSearchParams();
    if (options?.limit !== undefined) {
      params.set("limit", String(options.limit));
    }
    if (options?.offset !== undefined) {
      params.set("offset", String(options.offset));
    }
    const qs = params.toString();
    return getJson(
      `/api/v2/projects/${projectId}/activity${qs ? `?${qs}` : ""}`,
    );
  },

  // Exports are assembled client-side (html2pdf, markdown/json builders)
  // so the backend sees no mutation. Call this after a successful
  // download to record the event in the Activity feed. Fire-and-forget —
  // the caller ignores the return value; a failure here never blocks
  // the export itself.
  logExport: (
    projectId: string,
    fmt: "pdf" | "markdown" | "json" | "csv" | "html",
  ): Promise<void> =>
    postJson(
      `/api/v2/projects/${projectId}/activity/export-logged`,
      { fmt },
    ).then(() => undefined).catch(() => undefined),

  // ---- Auto-link ------------------------------------------------------
  // Standalone auto-link call — used when the user renames a topic and
  // wants to re-propose connections, or when a new topic was created
  // through a non-router path (e.g. kickoff seed already ran but a
  // later rename moved it semantically).
  autoLinkTopic: (
    topicId: string,
  ): Promise<{ relationships: Relationship[] }> =>
    postJson(`/api/v2/topics/${topicId}/auto-link`, {}),

  // ---- Artifact modes (summary / outline / dedupe) --------------------
  // Three v2 endpoints that run the model in "artifact writer" modes
  // over the current project state. Each returns a single object keyed
  // by mode name. Shapes mirror the backend JSON schemas in
  // services/planning_studio_service/agents/schemas_extra.py.
  projectSummary: (
    projectId: string,
  ): Promise<{
    summary: {
      summary_markdown: string;
      suggested_title: string;
      domain_framing: string;
    };
  }> => postJson(`/api/v2/projects/${projectId}/summary`, { locale: getLocale() }),

  projectOutline: (
    projectId: string,
    artifactType: string,
  ): Promise<{
    outline: {
      artifact_kind: string;
      suggested_title: string;
      sections: Array<{
        roman_numeral: string;
        title: string;
        note: string;
        subsections: Array<{
          letter: string;
          title: string;
          note: string;
          sub_subsections: Array<{
            number: string;
            title: string;
            note: string;
          }>;
        }>;
      }>;
    };
  }> =>
    postJson(`/api/v2/projects/${projectId}/outline`, {
      artifact_type: artifactType,
      locale: getLocale(),
    }),

  projectDedupe: (
    projectId: string,
  ): Promise<{
    dedupe: {
      merge_proposals: Array<{
        topic_a_id: string;
        topic_b_id: string;
        overlap_reason: string;
        suggested_merged_title: string;
        suggested_action: "merge" | "keep_both_but_note";
      }>;
    };
  }> => postJson(`/api/v2/projects/${projectId}/dedupe`, { locale: getLocale() }),

  // ---- Documents (#094 / Item 3 redesign) ----------------------------
  // Async one-shot generation; FE polls getDocument(id) every ~2s until
  // status flips off "in_progress". Custom fetch (not postJson) on POST
  // so we can translate typed errors (402/422/429/409) for the panel's
  // error-branching.

  generateDocument: async (
    projectId: string,
    docTypeOverride?: DocType,
  ): Promise<DocumentGenerateResponse> => {
    // #094 follow-up: optional doc_type override (from the empty-state
    // picker) lets the user correct a misidentified domain before
    // generating. When absent, BE derives doc_type from
    // project.metadata.domain as before.
    const body: { locale: string; doc_type?: DocType } = {
      locale: getLocale(),
    };
    if (docTypeOverride) body.doc_type = docTypeOverride;
    const res = await fetch(
      `${DEFAULT_BASE_URL}/api/v2/projects/${projectId}/document/generate`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        credentials: "include",
      },
    );
    if (!res.ok) {
      maybeDispatchUnauthorized(
        `/api/v2/projects/${projectId}/document/generate`,
        res.status,
      );
      let detailJson: any = null;
      try {
        detailJson = await res.json();
      } catch {
        /* swallow JSON-parse errors; fall through to generic */
      }
      const detail = detailJson?.detail ?? {};
      if (res.status === 402) {
        throw new DocumentPlanRequiredError(detail?.min_plan ?? "pro");
      }
      if (res.status === 422) {
        // 422 covers two cases: (a) domain unmapped (no override),
        // (b) override doc_type not in VALID_DOC_TYPES allowlist.
        // Distinguish via detail.error so the FE can show the right
        // toast.
        const errorCode = String(detail?.error ?? "");
        if (errorCode === "invalid_doc_type") {
          throw new DocumentInvalidDocTypeError(
            String(detail?.doc_type ?? ""),
          );
        }
        throw new DocumentDomainNotMappedError(String(detail?.domain ?? ""));
      }
      if (res.status === 429) {
        throw new DocumentCapReachedError(
          Number(detail?.current_count ?? 0),
          Number(detail?.cap ?? 0),
          String(detail?.plan_slug ?? ""),
          String(detail?.doc_type ?? ""),
        );
      }
      if (res.status === 409) {
        throw new DocumentInFlightError(String(detail?.doc_type ?? ""));
      }
      throw new Error(
        `POST document/generate failed: ${res.status} ${res.statusText}`,
      );
    }
    _captureLlmModeFromHeader(res);
    return (await res.json()) as DocumentGenerateResponse;
  },

  /** Poll target — fetches a single document by id. Used by the
   *  poller in InspiraApp; ignores 404 / cross-project / cross-user
   *  errors and surfaces them as DocumentNotFoundError so callers can
   *  cleanup the poller. */
  getDocument: async (
    projectId: string,
    documentId: string,
  ): Promise<DocumentView> => {
    const path = `/api/v2/projects/${projectId}/document/${documentId}`;
    const res = await fetch(`${DEFAULT_BASE_URL}${path}`, {
      credentials: "include",
    });
    if (!res.ok) {
      maybeDispatchUnauthorized(path, res.status);
      if (res.status === 404) {
        throw new DocumentNotFoundError();
      }
      throw new Error(
        `GET document/${documentId} failed: ${res.status} ${res.statusText}`,
      );
    }
    _captureLlmModeFromHeader(res);
    return (await res.json()) as DocumentView;
  },

  /** Latest completed doc for the project. Returns null when there is
   *  no completed document yet (404 from the BE). 422 (domain unmapped)
   *  surfaces as DocumentDomainNotMappedError. Used on tab open + canvas
   *  mount to seed the prefetch slot. */
  getLatestDocument: async (
    projectId: string,
    docType?: DocType,
  ): Promise<DocumentView | null> => {
    const qs = docType ? `?doc_type=${encodeURIComponent(docType)}` : "";
    const path = `/api/v2/projects/${projectId}/document${qs}`;
    const res = await fetch(`${DEFAULT_BASE_URL}${path}`, {
      credentials: "include",
    });
    if (!res.ok) {
      maybeDispatchUnauthorized(path, res.status);
      // 404 here means "no completed doc yet" — return null so the
      // panel renders the empty-state Generate CTA rather than a toast.
      if (res.status === 404) {
        return null;
      }
      let detailJson: any = null;
      try {
        detailJson = await res.json();
      } catch {
        /* swallow */
      }
      const detail = detailJson?.detail ?? {};
      if (res.status === 422) {
        throw new DocumentDomainNotMappedError(String(detail?.domain ?? ""));
      }
      throw new Error(
        `GET document failed: ${res.status} ${res.statusText}`,
      );
    }
    _captureLlmModeFromHeader(res);
    return (await res.json()) as DocumentView;
  },

  /** PATCH a single section's title and/or prose_markdown. No LLM, no
   *  cap. At least one field must be present. Returns the full updated
   *  DocumentView so the FE can re-render with the canonical merged
   *  content. */
  patchDocumentSection: async (
    projectId: string,
    documentId: string,
    sectionId: string,
    body: DocumentSectionPatchBody,
  ): Promise<DocumentView> => {
    const path = `/api/v2/projects/${projectId}/document/${documentId}/section/${encodeURIComponent(sectionId)}`;
    const res = await fetch(`${DEFAULT_BASE_URL}${path}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      credentials: "include",
    });
    if (!res.ok) {
      maybeDispatchUnauthorized(path, res.status);
      let detailJson: any = null;
      try {
        detailJson = await res.json();
      } catch {
        /* swallow */
      }
      const detail = detailJson?.detail ?? {};
      if (res.status === 402) {
        throw new DocumentPlanRequiredError(detail?.min_plan ?? "pro");
      }
      if (res.status === 404) {
        // 404 covers both document_not_found and section_not_found —
        // BE differentiates via the `error` field.
        const errorCode = String(detail?.error ?? "");
        if (errorCode === "section_not_found") {
          throw new DocumentNotFoundError(null, sectionId);
        }
        throw new DocumentNotFoundError();
      }
      throw new Error(
        `PATCH document/${documentId}/section/${sectionId} failed: ${res.status} ${res.statusText}`,
      );
    }
    _captureLlmModeFromHeader(res);
    return (await res.json()) as DocumentView;
  },

  // ---- Entitlements + Scaffold ---------------------------------------
  // PR 2 replaced /api/v2/credits (with its credit-balance shape) with
  // /api/v2/entitlements (plan-tier + feature flags). The buyCredits
  // endpoint was deleted along with the Noop pack flow; reintroduce only
  // when an actual billing provider is wired.
  getEntitlements: (): Promise<EntitlementsResponse> =>
    getJson("/api/v2/entitlements"),

  generateScaffold: (
    projectId: string,
  ): Promise<{
    scaffold: {
      scaffold_id: string;
      project_id: string;
      framework: string;
      language: string;
      created_at: string;
      readme_preview: string;
      post_install_steps: string[];
      truncation_note: string;
      file_count: number;
      files: Array<{ path: string; size: number }>;
    };
    balance: number;
  }> => postJson(`/api/v2/projects/${projectId}/scaffold`, { locale: getLocale() }),

  listScaffolds: (
    projectId: string,
  ): Promise<{
    scaffolds: Array<{
      scaffold_id: string;
      project_id: string;
      user_id: string;
      framework: string;
      language: string;
      created_at: string;
    }>;
  }> => getJson(`/api/v2/projects/${projectId}/scaffolds`),

  // Imperative download — fires off a fetch (with credentials so the
  // session cookie rides along) and triggers a save dialog via a
  // temporary <a> element. Used by ScaffoldResult's "Download zip" CTA.
  downloadScaffold: async (scaffoldId: string): Promise<void> => {
    const res = await fetch(
      `${DEFAULT_BASE_URL}/api/v2/scaffolds/${scaffoldId}/download`,
      { credentials: "include" },
    );
    if (!res.ok) {
      maybeDispatchUnauthorized(
        `/api/v2/scaffolds/${scaffoldId}/download`,
        res.status,
      );
      const detail = await res.text();
      throw new Error(
        `GET scaffold download failed: ${res.status} ${res.statusText} — ${detail}`,
      );
    }
    // Pull filename from Content-Disposition if present; otherwise
    // fall back to a plain stem so the browser doesn't save it as
    // "download".
    const disposition = res.headers.get("content-disposition") || "";
    const match = disposition.match(/filename="([^"]+)"/);
    const filename = match?.[1] ?? `scaffold-${scaffoldId}.zip`;

    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    // Give the browser a tick to start the save, then release the
    // blob URL so Chrome doesn't hold it open.
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  },

  // ---- Example projects -----------------------------------------------
  // Static catalog endpoint that returns the six hand-authored seeds.
  // ``listExamples`` is anonymous-safe. ``createProjectFromExample``
  // requires auth and rejects the system (anonymous) user.
  listExamples: (): Promise<{ examples: ExampleProjectCatalogItem[] }> =>
    getJson("/api/v2/examples"),
  createProjectFromExample: (
    slug: string,
  ): Promise<{ project: V2Project; topics: Topic[] }> =>
    postJson("/api/v2/projects/from-example", { slug }),

  // ---- Templates -------------------------------------------------------
  // Static catalog endpoint (no auth required, anonymous-safe).
  // ``listTemplates`` fetches the summary list for the gallery picker.
  // ``createProjectFromTemplate`` creates a project + topics + relationships
  // from the chosen slug in one round trip and returns the same envelope
  // shape as the kickoff endpoint so the canvas can open immediately.
  listTemplates: (): Promise<{
    templates: Array<{
      slug: string;
      title: string;
      tagline: string;
      description: string;
      topic_count: number;
      relationship_count: number;
      domain_framing: string;
    }>;
  }> => getJson("/api/v2/templates"),

  createProjectFromTemplate: (
    slug: string,
  ): Promise<{
    project: V2Project;
    topics: Topic[];
    relationships: Relationship[];
    template: {
      slug: string;
      title: string;
      tagline: string;
      domain_framing: string;
    };
  }> => postJson("/api/v2/projects/from-template", { slug }),

  // ---- Markdown import ---------------------------------------------------
  // Parses a raw markdown doc (Notion/Obsidian brain dump, outline, etc.)
  // and creates a project with topics from H2 headings. H3 lines under each
  // H2 become seed decisions; prose blocks become context_note decisions so
  // no content is lost. Returns the project + topics + a preview_html
  // rendering of the parsed structure for an optional confirmation step.
  importFromMarkdown: (
    markdown: string,
    title?: string,
  ): Promise<{
    project: V2Project;
    topics: Topic[];
    preview_html: string;
  }> =>
    postJson("/api/v2/projects/from-markdown", {
      markdown,
      ...(title !== undefined ? { title } : {}),
    }),

  // ---- JSON import ------------------------------------------------------
  // The reverse of `exportToJson`. Takes a parsed JSON object (NOT a raw
  // string — the caller parses client-side so any parse error surfaces
  // before we hit the network) and creates a new project with the
  // topics, relationships, and decisions baked in. Q&A turns in the blob
  // are NOT imported (see services/planning_studio_service/json_import.py
  // for the rationale). The server returns the full envelope so the
  // caller can route straight into the canvas without a follow-up
  // round-trip.
  importFromJson: (
    blob: object,
    titleOverride?: string,
  ): Promise<{
    project: V2Project;
    topics: Topic[];
    relationships: Relationship[];
    decisions: Decision[];
  }> =>
    postJson("/api/v2/projects/from-json", {
      json_blob: blob,
      ...(titleOverride !== undefined ? { title: titleOverride } : {}),
    }),

  // ---- Topic merge (dedupe) --------------------------------------------
  // Merges drop_topic_id into keep_topic_id within the same project.
  // Returns counts of re-parented turns, decisions, relationships, and
  // dropped self-edges. The caller is responsible for firing the
  // inspira:topics-changed and inspira:decisions-changed events.
  mergeTopics: (
    projectId: string,
    keepTopicId: string,
    dropTopicId: string,
  ): Promise<{
    merged_turns: number;
    merged_decisions: number;
    rerouted_relationships: number;
    dropped_self_edges: number;
  }> =>
    postJson(`/api/v2/projects/${projectId}/topics/merge`, {
      keep_topic_id: keepTopicId,
      drop_topic_id: dropTopicId,
    }),

  // ---- Homepage AI suggestions -----------------------------------------
  // Returns 3 inferred project ideas based on the user's existing projects.
  // Returns an empty array on any error — non-critical feature; the UI
  // gracefully hides the row when the array is empty.
  getHomepageSuggestions: async (): Promise<string[]> => {
    try {
      const res = await getJson<{ suggestions: string[] }>(
        `/api/v2/homepage/suggestions?locale=${encodeURIComponent(getLocale())}`,
      );
      return Array.isArray(res.suggestions) ? res.suggestions : [];
    } catch {
      return [];
    }
  },

  // ---- Cross-project search --------------------------------------------
  // GET /api/v2/search?q=&limit=
  // Auth required; user-scoped — never returns another user's data.
  // Returns up to `limit` (default 50) SearchHit objects ranked by
  // relevance (title matches rank higher than body matches).
  // Wire-up: see services/planning_studio_service/search.py and the
  // route registration guide at the bottom of api.py.
  searchAll: (
    query: string,
    limit?: number,
  ): Promise<SearchResponse> =>
    getJson(
      `/api/v2/search?q=${encodeURIComponent(query)}${limit !== undefined ? `&limit=${limit}` : ""}`,
    ),

  // ---- Read-only share links -------------------------------------------
  // generateShareLink mints a new link (revokes any prior live one).
  // revokeShareLink invalidates the current link.
  // fetchSharedProject is the public anonymous read used by SharedCanvasPage.

  generateShareLink: (
    projectId: string,
  ): Promise<{
    share_link: {
      token: string;
      project_id: string;
      url_path: string;
      created_at: string;
      revoked_at: string | null;
    } | null;
    url: string;
  }> => postJson(`/api/v2/projects/${projectId}/share`, {}),

  getShareLink: (
    projectId: string,
  ): Promise<{
    share_link: {
      token: string;
      project_id: string;
      url_path: string;
      created_at: string;
      revoked_at: string | null;
    } | null;
  }> => getJson(`/api/v2/projects/${projectId}/share`),

  revokeShareLink: (
    projectId: string,
  ): Promise<{ revoked: boolean }> =>
    postJson(`/api/v2/projects/${projectId}/share/revoke`, {}),

  // ---- Two-factor authentication (Stream 3 stubs) ---------------------
  // TODO(backend): POST /api/auth/2fa/setup
  // TODO(backend): POST /api/auth/2fa/verify
  // TODO(backend): POST /api/auth/2fa/disable
  // TODO(backend): POST /api/auth/2fa/recovery-regen
  // All four 404 today; callers surface "Coming soon" on 404 and
  // fall back to fixture data so design review can still happen.
  setup2FA: (): Promise<TwoFactorSetupResponse> =>
    postJson("/api/auth/2fa/setup", {}),

  verify2FA: (input: { code: string }): Promise<{ verified: boolean }> =>
    postJson("/api/auth/2fa/verify", input),

  disable2FA: (input: { password: string }): Promise<{ disabled: boolean }> =>
    postJson("/api/auth/2fa/disable", input),

  regenerateRecoveryCodes: (input: {
    password: string;
  }): Promise<TwoFactorRecoveryResponse> =>
    postJson("/api/auth/2fa/recovery-regen", input),

  // ---- Active sign-in sessions (Stream 3 stubs) -----------------------
  // TODO(backend): GET    /api/auth/sessions
  // TODO(backend): DELETE /api/auth/sessions/{id}
  // TODO(backend): DELETE /api/auth/sessions
  listSessions: (): Promise<AuthSessionsResponse> =>
    getJson("/api/auth/sessions"),

  revokeSession: async (sessionId: string): Promise<void> => {
    const path = `/api/auth/sessions/${encodeURIComponent(sessionId)}`;
    const res = await fetch(`${DEFAULT_BASE_URL}${path}`, {
      method: "DELETE",
      credentials: "include",
    });
    if (!res.ok) {
      const detail = await res.text();
      throw new Error(
        `DELETE ${path} failed: ${res.status} ${res.statusText} — ${detail}`,
      );
    }
  },

  revokeAllOtherSessions: async (): Promise<{ revoked: number }> => {
    const path = "/api/auth/sessions";
    const res = await fetch(`${DEFAULT_BASE_URL}${path}`, {
      method: "DELETE",
      credentials: "include",
    });
    if (!res.ok) {
      const detail = await res.text();
      throw new Error(
        `DELETE ${path} failed: ${res.status} ${res.statusText} — ${detail}`,
      );
    }
    return res.json() as Promise<{ revoked: number }>;
  },

  // ---- Email preferences (Stream 3 stubs) ------------------------------
  // TODO(backend): GET   /api/auth/email-preferences
  // TODO(backend): PATCH /api/auth/email-preferences
  // The PATCH body shape is `{group, key, value}` so a single toggle
  // round-trips without re-sending the whole record.
  getEmailPreferences: (): Promise<EmailPreferences> =>
    getJson("/api/auth/email-preferences"),

  updateEmailPreference: async (input: {
    group: EmailPreferencesGroupKey;
    key: string;
    value: boolean;
  }): Promise<EmailPreferences> => {
    const path = "/api/auth/email-preferences";
    const res = await fetch(`${DEFAULT_BASE_URL}${path}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
      credentials: "include",
    });
    if (!res.ok) {
      const detail = await res.text();
      throw new Error(
        `PATCH ${path} failed: ${res.status} ${res.statusText} — ${detail}`,
      );
    }
    return res.json() as Promise<EmailPreferences>;
  },

  fetchSharedProject: (
    token: string,
  ): Promise<{
    project: {
      project_id: string;
      title: string;
      created_at: string;
      updated_at: string;
      owner_display_name: string;
    };
    topics: Topic[];
    relationships: Relationship[];
    decisions: Decision[];
    turns_by_topic: Record<string, QnaTurn[]>;
  }> =>
    // Public — no credentials cookie. Same base URL so local dev works.
    fetch(`${DEFAULT_BASE_URL}/api/v2/shared/${encodeURIComponent(token)}`)
      .then((res) => {
        if (!res.ok) {
          return res.text().then((detail) => {
            throw new Error(
              `GET /api/v2/shared/${token} failed: ${res.status} ${res.statusText} — ${detail}`,
            );
          });
        }
        return res.json() as Promise<{
          project: {
            project_id: string;
            title: string;
            created_at: string;
            updated_at: string;
            owner_display_name: string;
          };
          topics: Topic[];
          relationships: Relationship[];
          decisions: Decision[];
          turns_by_topic: Record<string, QnaTurn[]>;
        }>;
      }),

  // ---- Artifact Viewer ---------------------------------------------------
  // Three endpoints in this slice: GET the persisted artifact, generate
  // (SSE) on first open, edit (SSE) for chat-driven refinements.
  getArtifact: (projectId: string): Promise<{ artifact: ArtifactPayload }> =>
    getJson(`/api/v2/projects/${projectId}/artifact`),

  /** Trigger scaffold generation via SSE.
   *
   *  Pass ``options.force = true`` to bypass the BE's cached-manifest
   *  early-return (used by the Regenerate kebab to discard and re-draft).
   *  Default ``false`` makes the auto-fire-on-404 path safe against
   *  the impatient-race window described in issues-log #187 — the
   *  BE replays the persisted manifest as a ``complete`` SSE frame
   *  instead of firing a second LLM call. */
  generateArtifactStream: (
    projectId: string,
    callbacks: SseCallbacks<{ artifact: ArtifactPayload }>,
    signal?: AbortSignal,
    options: { force?: boolean } = {},
  ): Promise<{ artifact: ArtifactPayload }> =>
    ssePost<{ artifact: ArtifactPayload }>(
      `/api/v2/projects/${projectId}/artifact/generate/stream`,
      { force: options.force ?? false },
      callbacks,
      signal,
    ),

  editArtifactStream: (
    projectId: string,
    message: string,
    callbacks: SseCallbacks<{ artifact: ArtifactPayload }>,
    signal?: AbortSignal,
  ): Promise<{ artifact: ArtifactPayload }> =>
    ssePost<{ artifact: ArtifactPayload }>(
      `/api/v2/projects/${projectId}/artifact/edit/stream`,
      { message, locale: getLocale() },
      callbacks,
      signal,
    ),

  /** Autosave one file's content into the project's latest scaffold.
   *  Called debounced from the Code-tab textarea so edits persist
   *  across reloads. Backend rejects with 409 project_locked when
   *  project_state ∉ {pending_review, rejected, summary_ready}. */
  patchArtifactFile: (
    projectId: string,
    path: string,
    content: string,
  ): Promise<{ ok: boolean; saved_at: string }> =>
    patchJson(`/api/v2/projects/${projectId}/artifact/files`, {
      path,
      content,
    }),

  /** Create a new file in the scaffold. 409 if path already exists. */
  createArtifactFile: (
    projectId: string,
    path: string,
    content: string = "",
  ): Promise<{ ok: boolean; path: string; saved_at: string }> =>
    postJson(`/api/v2/projects/${projectId}/artifact/files`, {
      path,
      content,
    }),

  /** Rename / move a file. 409 if new_path already exists. */
  renameArtifactFile: (
    projectId: string,
    oldPath: string,
    newPath: string,
  ): Promise<{ ok: boolean; path: string; saved_at: string }> =>
    patchJson(`/api/v2/projects/${projectId}/artifact/files/rename`, {
      old_path: oldPath,
      new_path: newPath,
    }),

  /** Delete a file. 404 if path missing. */
  deleteArtifactFile: async (
    projectId: string,
    path: string,
  ): Promise<{ ok: boolean; path: string; saved_at: string }> => {
    const url = `/api/v2/projects/${projectId}/artifact/files?path=${encodeURIComponent(path)}`;
    const res = await fetch(`${DEFAULT_BASE_URL}${url}`, {
      method: "DELETE",
      credentials: "include",
    });
    if (!res.ok) {
      const detail = await res.text();
      throw new Error(
        `DELETE ${url} failed: ${res.status} ${res.statusText} — ${detail}`,
      );
    }
    return res.json();
  },

  // ---- Wave F.4: inline IDE-style comments on generated code -----------
  /** List artifact comments for a project. ``include_resolved`` flips
   *  whether resolved threads come back too (default: open only). */
  listArtifactComments: (
    projectId: string,
    options: { includeResolved?: boolean } = {},
  ): Promise<{ comments: ArtifactComment[] }> => {
    const qs = options.includeResolved ? "?include_resolved=true" : "";
    return getJson(
      `/api/v2/projects/${projectId}/artifact/comments${qs}`,
    );
  },

  /** Create a comment (or a reply when ``parent_comment_id`` is set). */
  createArtifactComment: (
    projectId: string,
    body: {
      file_path: string;
      line_number: number;
      line_content: string;
      category: ArtifactCommentCategory;
      body: string;
      parent_comment_id?: string;
    },
  ): Promise<{ comment: ArtifactComment }> =>
    postJson(
      `/api/v2/projects/${projectId}/artifact/comments`,
      body as unknown as Record<string, unknown>,
    ),

  /** Edit a comment's body (author-or-admin only) and/or toggle resolved. */
  updateArtifactComment: (
    projectId: string,
    commentId: string,
    body: { body?: string; resolved?: boolean },
  ): Promise<{ comment: ArtifactComment }> =>
    patchJson(
      `/api/v2/projects/${projectId}/artifact/comments/${commentId}`,
      body as unknown as Record<string, unknown>,
    ),

  // ---- Send-to-Linear / Send-to-GitHub export modals --------------------
  getV2Project: (projectId: string): Promise<{ project: V2Project }> =>
    getJson(`/api/v2/projects/${encodeURIComponent(projectId)}`),

  getConnectorDestination: (
    provider: "linear" | "github",
  ): Promise<{
    configured: boolean;
    display: string | null;
    metadata: Record<string, unknown>;
    hint: string | null;
  }> =>
    getJson(
      `/api/v2/connectors/${encodeURIComponent(provider)}/destination`,
    ),

  exportProjectToLinear: (
    projectId: string,
    options: {
      include_canvas_link: boolean;
      include_source_feedback: boolean;
      apply_priority_label: boolean;
      priority_label: "P0" | "P1" | "P2";
    },
  ): Promise<{
    ok: true;
    provider: "linear";
    issue_url: string;
    issue_id: string;
    identifier: string;
    sub_issue_count: number;
  }> =>
    postJson(
      `/api/v2/projects/${encodeURIComponent(projectId)}/export/linear`,
      options as unknown as Record<string, unknown>,
    ),

  exportProjectToGitHub: (
    projectId: string,
    options: {
      include_canvas_link: boolean;
      include_source_feedback: boolean;
      apply_priority_label: boolean;
      priority_label: "P0" | "P1" | "P2";
    },
  ): Promise<{
    ok: true;
    provider: "github";
    issue_url: string;
    issue_number: number;
    issue_id: number | string;
  }> =>
    postJson(
      `/api/v2/projects/${encodeURIComponent(projectId)}/export/github`,
      options as unknown as Record<string, unknown>,
    ),

  /**
   * Spawn the orchestrator for an auto-promoted Draft project.
   *
   * Idempotent — a second call returns the existing run_id without
   * spawning a duplicate. Server-side flips the project's state to
   * `in_review` so useKanbanData reclassifies it into the AI-thinking
   * column. Returns 202 + {run_id, project_id, status}.
   */
  startProjectCanvas: (
    projectId: string,
    opts?: {
      /** Partner's correction note from a Kanban "rerun" drag. The
       *  BE stashes this in v2_projects.metadata.correction_note and
       *  the orchestrator's sub-agent prompt threads it in as
       *  "Partner correction:" context so the rerun actually
       *  responds to the feedback. Empty / omitted → behaves
       *  exactly as before. */
      correctionNote?: string;
    },
  ): Promise<{
    run_id: string;
    project_id: string;
    status: "thinking" | "already_running";
  }> =>
    postJson(
      `/api/v2/projects/${encodeURIComponent(projectId)}/start-canvas`,
      opts?.correctionNote
        ? { correction_note: opts.correctionNote }
        : {},
    ),

  /**
   * Poll GitHub Actions / check_runs status for the project's pushed PR.
   *
   * Product decision: once the PR is in GitHub, Inspira
   * verifies the change actually landed. v0 reports what the partner's
   * CI says (pass / fail / pending / no_ci_configured); v1 (deferred)
   * spins up a sandboxed runner.
   *
   * Status values:
   *  - "no_pr_metadata"   → Send-to-GitHub hasn't been clicked
   *  - "pr_not_open"      → PR was deleted or doesn't exist
   *  - "pending"          → CI is still running
   *  - "passed"           → all checks passed
   *  - "failed"           → at least one check failed
   *  - "no_ci_configured" → repo has no CI; verification skipped
   */
  getPrVerification: (
    projectId: string,
  ): Promise<{
    status:
      | "pending"
      | "passed"
      | "failed"
      | "no_ci_configured"
      | "pr_not_open"
      | "no_pr_metadata";
    pr_number: number | null;
    pr_url: string | null;
    head_sha: string | null;
    merged: boolean;
    checks: Array<{
      name: string | null;
      status: string | null;
      conclusion: string | null;
      details_url: string | null;
      started_at: string | null;
      completed_at: string | null;
    }>;
    summary: string;
    fetched_at: string;
  }> =>
    getJson(
      `/api/v2/projects/${encodeURIComponent(projectId)}/pr-verification`,
    ),

  /**
   * Push the project's generated scaffold to GitHub as a Pull Request.
   *
   * Distinct from `exportProjectToGithub` (which files an Issue) — this
   * one creates a branch, commits each scaffold file, and opens a PR.
   * Returns 409 `scaffold_not_ready` when the project has no scaffold
   * yet — caller should prompt the user to generate code first.
   */
  exportScaffoldAsGithubPr: (
    projectId: string,
  ): Promise<{
    ok: true;
    provider: "github";
    pr_url: string;
    pr_number: number;
    branch_name: string;
    commits: string[];
    files_pushed: number;
  }> =>
    postJson(
      `/api/v2/projects/${encodeURIComponent(projectId)}/export/github-pr`,
      {},
    ),

  // ---- GitHub repo browser (Wave F.2) ---------------------------------
  // Backs the "Repo" tab in the artifact viewer. Read-only — the user
  // browses their default branch (``main``); edits / commits land on a
  // separate write surface in a later wave. ``X-Workspace-Id`` is
  // auto-injected by ``getJson``, so callers don't pass a workspace id.
  getRepoTree: (
    params?: { ref?: string; recursive?: boolean },
  ): Promise<RepoTreeResponse> => {
    const search = new URLSearchParams();
    if (params?.ref !== undefined) search.set("ref", params.ref);
    else search.set("ref", "main");
    search.set(
      "recursive",
      String(params?.recursive ?? true),
    );
    return getJson(
      `/api/v2/connectors/github/repo/tree?${search.toString()}`,
    );
  },

  getRepoFile: (
    path: string,
    params?: { ref?: string },
  ): Promise<RepoFileResponse> => {
    const search = new URLSearchParams();
    search.set("path", path);
    search.set("ref", params?.ref ?? "main");
    return getJson(
      `/api/v2/connectors/github/repo/file?${search.toString()}`,
    );
  },

  // ---- PR overlay (Wave F.3) -----------------------------------------
  // Project-scoped: the base repo tree merged with the project's latest
  // scaffold. Each entry tagged ``source: base | scaffold | modified``
  // so the FE can render a "modified" badge on touched rows. Auth is
  // owner-only via ``_require_owned_project`` on the BE — matches the
  // existing artifact CRUD pattern.
  getPrOverlayTree: (
    projectId: string,
  ): Promise<PrOverlayTreeResponse> =>
    getJson(`/api/v2/projects/${projectId}/pr-overlay-tree`),

  /** Returns scaffold content for ``scaffold`` / ``modified`` entries.
   *  A ``source: "base"`` response is a sentinel — the FE should
   *  re-fetch the same path via ``getRepoFile`` so the existing F.2
   *  cache + binary-detection path runs. We don't 302 server-side
   *  because that would tangle the two caches.
   */
  getPrOverlayFile: (
    projectId: string,
    path: string,
  ): Promise<PrOverlayFileResponse> => {
    const search = new URLSearchParams();
    search.set("path", path);
    return getJson(
      `/api/v2/projects/${projectId}/pr-overlay-file?${search.toString()}`,
    );
  },

  /** Returns the staleness payload for this project's PR overlay:
   *  drift between the recorded ``base_main_sha`` and current main,
   *  intersected with the project's scaffold paths. Pre-F.5 projects
   *  (no recorded baseline) return ``legacy: true, is_stale: false``
   *  and the row self-heals on the next ``/pr-overlay-tree`` call.
   */
  getPrOverlayStaleness: (
    projectId: string,
  ): Promise<PrOverlayStalenessResponse> =>
    getJson(`/api/v2/projects/${projectId}/pr-overlay-staleness`),

  /** Wave F.6 — kick off "Refresh PR with Inspira". Server re-runs the
   *  scaffold adapter with the fresh main + current draft as redraft
   *  reference, persists a new scaffold, and resets the staleness
   *  baseline. Returns immediately with the refresh_id; the diff is
   *  fetched separately. 409 ``refresh_in_progress`` for a second
   *  concurrent POST.
   */
  startPrRefresh: (
    projectId: string,
  ): Promise<StartRefreshResponse> =>
    postJson(
      `/api/v2/projects/${projectId}/refresh-overlay`, {},
    ),

  /** Wave F.6 — fetch the 3-way diff payload for a completed refresh.
   *  Each ``files`` entry is ``{path, base, partner_edit, ai_redraft,
   *  conflict}``; ``partner_edit: null`` means the file was never
   *  partner-edited and the FE should render a 2-way diff. */
  getRefreshDiff: (
    projectId: string, refreshId: string,
  ): Promise<RefreshDiffResponse> => {
    const search = new URLSearchParams();
    search.set("refresh_id", refreshId);
    return getJson(
      `/api/v2/projects/${projectId}/refresh-diff?${search.toString()}`,
    );
  },

  /** Wave F.6 — apply per-file decisions on a refresh's diff. Returns
   *  the post-resolve scaffold_id + a summary of how many files were
   *  accepted / kept / merged. */
  postRefreshResolutions: (
    projectId: string,
    body: { refresh_id: string; decisions: Record<string, RefreshDecision> },
  ): Promise<RefreshResolveResponse> =>
    postJson(
      `/api/v2/projects/${projectId}/refresh-resolve`, body,
    ),
};

// ---- Repo-browser response types (Wave F.2) ---------------------------

export type RepoTreeEntry = {
  path: string;
  type: "blob" | "tree";
  size?: number;
};

export type RepoTreeResponse = {
  repo_full_name: string;
  ref: string;
  sha: string;
  tree: RepoTreeEntry[];
  truncated: boolean;
};

export type RepoFileResponse = {
  path: string;
  /** Text content for UTF-8 files; ``null`` for binary files (FE should
   *  render a "cannot preview" placeholder rather than the content). */
  content: string | null;
  binary: boolean;
  sha: string;
  encoding: "utf-8" | "base64";
};

// ---- PR overlay response types (Wave F.3) -----------------------------

export type PrOverlaySource = "base" | "scaffold" | "modified";

export type PrOverlayTreeEntry = {
  path: string;
  type: "blob";
  size?: number;
  source: PrOverlaySource;
};

export type PrOverlayWarning = {
  kind: "case_collision";
  paths: string[];
};

export type PrOverlayTreeResponse = {
  project_id: string;
  project_title: string;
  dominant_category: string;
  repo_full_name: string;
  base_ref: string;
  base_sha: string;
  tree: PrOverlayTreeEntry[];
  truncated: boolean;
  warnings: PrOverlayWarning[];
};

export type PrOverlayFileResponse = {
  path: string;
  /** Text content for ``scaffold`` / ``modified`` entries; ``null`` for
   *  ``source: "base"`` responses (FE re-issues via ``getRepoFile``). */
  content: string | null;
  binary: boolean;
  source: PrOverlaySource;
  encoding: "utf-8" | "base64";
};

// ---- Staleness response types (Wave F.5) ------------------------------

/** Drift signal for a PR overlay relative to the current default-branch
 *  head. Computed BE-side; the FE renders banner + per-file chevrons
 *  + the soft edit-block modal off this single payload.
 *
 *  ``legacy=true`` means no baseline was recorded (pre-F.5 project, or
 *  scaffold never opened post-merge). The row self-heals on the next
 *  ``/pr-overlay-tree`` fetch via the write-through in
 *  ``build_overlay_tree``; until then, treat the staleness signal as
 *  unknown and suppress badges/banners.
 *
 *  ``truncated=true`` means GitHub's compare API capped the file list
 *  at one page (~300 files). The displayed ``affected_files_count``
 *  is then a lower bound — F.6 will follow pagination.
 */
export type PrOverlayStalenessResponse = {
  is_stale: boolean;
  base_main_sha: string | null;
  current_main_sha: string | null;
  /** ISO-8601 timestamp of the head commit on main, or ``null`` when
   *  the SHAs are equal / the compare payload has no commits. */
  main_moved_at: string | null;
  affected_files_count: number;
  scaffold_files_count: number;
  /** First few overlapping paths (cap of 5 BE-side) so the FE can
   *  chevron specific rows without bloating the response. */
  affected_paths_sample: string[];
  last_partner_edit: string | null;
  scaffold_drafted_at: string | null;
  legacy: boolean;
  truncated: boolean;
};

// ---- Refresh PR response types (Wave F.6) -----------------------------

/** Wave F.6 — initial response from POST /refresh-overlay. The refresh
 *  is already complete server-side by the time this resolves (the
 *  scaffold adapter runs synchronously inside the route's
 *  run_in_executor); the FE then immediately calls getRefreshDiff
 *  with the returned ``refresh_id`` to load the diff payload. */
export type StartRefreshResponse = {
  scaffold_id: string;
  refresh_id: string;
  base_main_sha: string;
  changed_paths: string[];
  changed_count: number;
};

/** One per-file entry in the refresh diff payload. ``partner_edit`` is
 *  null for files the partner never edited since AI generation — the
 *  FE renders a 2-way diff in that case. Otherwise all three columns
 *  are populated; ``conflict: true`` highlights rows where the AI
 *  redraft diverges from both the AI-original baseline AND the
 *  partner-edited overlay. */
export type RefreshDiffFile = {
  path: string;
  base: string | null;
  partner_edit: string | null;
  ai_redraft: string | null;
  conflict: boolean;
};

export type RefreshDiffResponse = {
  refresh_id: string;
  status: "in_progress" | "completed" | "failed" | "resolved";
  previous_scaffold_id: string | null;
  new_scaffold_id: string | null;
  base_main_sha_before: string;
  base_main_sha_after: string | null;
  changed_paths: string[];
  files: RefreshDiffFile[];
};

export type RefreshDecisionKind =
  | "accept_redraft"
  | "keep_partner_edit"
  | "merged";

export type RefreshDecision = {
  decision: RefreshDecisionKind;
  /** Required when ``decision === "merged"`` — the partner-authored
   *  merge of partner_edit + ai_redraft. Ignored otherwise. */
  merged_content?: string;
};

export type RefreshResolveResponse = {
  scaffold_id: string;
  refresh_id: string;
  diff_summary: {
    accepted: number;
    kept: number;
    merged: number;
  };
};

/** Best-effort extract of an ``{error, message}`` payload from the
 *  Error.message string thrown by ``getJson``/``postJson``. Returns
 *  ``null`` when the message isn't in the canonical
 *  "VERB path failed: NNN STATUS — {detail-json}" shape — callers
 *  fall back to a generic error UI in that case. */
export function parseRepoBrowseError(
  err: unknown,
): { error: string; message: string | null; status: number | null } | null {
  if (!(err instanceof Error)) return null;
  const match = err.message.match(
    /failed:\s*(\d{3})\s*[^—]*—\s*(\{.*\})$/s,
  );
  if (!match) return null;
  const status = Number.parseInt(match[1], 10);
  try {
    const parsed = JSON.parse(match[2]);
    const detail = (parsed?.detail ?? parsed) as {
      error?: string;
      message?: string;
    };
    if (typeof detail?.error !== "string") return null;
    return {
      error: detail.error,
      message: typeof detail.message === "string" ? detail.message : null,
      status: Number.isFinite(status) ? status : null,
    };
  } catch {
    return null;
  }
}
