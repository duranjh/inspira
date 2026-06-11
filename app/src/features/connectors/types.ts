// Type shapes for the /api/v2/connectors response.
//
// Mirrors the backend payload composed in
// services/planning_studio_service/connectors/router.py
// (`_live_descriptor_payload`, `_coming_soon_payload`,
// `_future_payload`). Kept in this file rather than baked into
// api.ts so ConnectorTile / ComingSoonTile / FutureTile can import
// the types without dragging the api in.

export type ConnectorStatus =
  | "not_connected"
  | "not_implemented"
  | "connected"
  | "needs_reauth"
  | "error";

export interface ConnectorRuntimeState {
  status: ConnectorStatus;
  account: string | null;
  primary_repo_full_name: string | null;
  repo_count: number;
  last_sync_at: string | null;
  last_successful_sync_at: string | null;
  last_error: string | null;
}

export interface LiveConnectorPayload {
  provider: string;
  display_name: string;
  summary: string;
  logo_slug: string;
  state: ConnectorRuntimeState;
  actions: Record<string, string>;
}

export interface ComingSoonConnectorPayload {
  provider: string;
  display_name: string;
  summary: string;
  contact_route: string;
}

export interface FutureConnectorPayload {
  provider: string;
  display_name: string;
  summary: string;
}

export interface ConnectorsResponse {
  live: LiveConnectorPayload[];
  coming_soon: ComingSoonConnectorPayload[];
  future: FutureConnectorPayload[];
}
