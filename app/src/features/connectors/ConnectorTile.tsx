// 4-state Live-tier connector tile.
//
// The B1.3 design spec is exactly four states; we hold that line.
// The mailto coming-soon and future-greyed tiles are SEPARATE
// components (ComingSoonTile, FutureTile) so this component never
// grows a 5th branch.
//
// State derivation:
//   idle       — status = not_connected | not_implemented
//   connecting — caller-driven transient (between user click and
//                redirect / dialog open). NOT a backend status.
//   connected  — status = connected
//   error      — status = needs_reauth | error
//
// The tile is provider-agnostic. Per-provider CTA labels and
// actions are passed in (the parent ConnectorsPage owns the
// provider-specific wiring).

import { ReactElement } from "react";

import type { ConnectorRuntimeState } from "./types";

export type TileState = "idle" | "connecting" | "connected" | "error";

export function deriveTileState(
  state: ConnectorRuntimeState,
  connecting: boolean,
): TileState {
  if (connecting) return "connecting";
  if (state.status === "connected") return "connected";
  if (state.status === "error" || state.status === "needs_reauth") {
    return "error";
  }
  return "idle";
}

export interface ConnectorTileProps {
  provider: string;
  displayName: string;
  summary: string;
  state: ConnectorRuntimeState;
  /** Transient client-side flag; true between the user clicking
   *  Connect and the redirect / dialog opening. */
  connecting?: boolean;
  ctaLabel: string;
  /** True when this provider isn't user-actionable yet (e.g.,
   *  Linear / CSV in C6 — backend returns `not_implemented`). The
   *  CTA still fires onConnect; the parent decides what to do
   *  (open a "coming in F4" dialog, etc.). */
  notImplemented?: boolean;
  onConnect: () => void;
  onSync: () => void;
  onManage: () => void;
  onRetry: () => void;
}

function fmtRelative(iso: string | null): string {
  if (!iso) return "—";
  const ts = Date.parse(iso);
  if (!Number.isFinite(ts)) return iso;
  const deltaSec = Math.max(0, Math.floor((Date.now() - ts) / 1000));
  if (deltaSec < 60) return "just now";
  if (deltaSec < 3600) {
    const m = Math.floor(deltaSec / 60);
    return `${m} min ago`;
  }
  if (deltaSec < 86400) {
    const h = Math.floor(deltaSec / 3600);
    return `${h}h ago`;
  }
  const d = Math.floor(deltaSec / 86400);
  return `${d}d ago`;
}

function logoLetters(displayName: string): string {
  const parts = displayName.replace(/[^A-Za-z0-9 /]/g, "").split(/[ /]+/);
  if (parts.length === 0) return "??";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + (parts[1][0] || "")).toUpperCase();
}

export function ConnectorTile({
  provider,
  displayName,
  summary,
  state,
  connecting,
  ctaLabel,
  notImplemented,
  onConnect,
  onSync,
  onManage,
  onRetry,
}: ConnectorTileProps): ReactElement {
  const tileState = deriveTileState(state, !!connecting);

  return (
    <div
      className="connector-tile"
      data-provider={provider}
      data-state={tileState}
    >
      <div className="connector-tile__head">
        <div className="connector-tile__logo">{logoLetters(displayName)}</div>
        <div className="connector-tile__name">{displayName}</div>
      </div>
      <p className="connector-tile__desc">{summary}</p>

      {tileState === "connecting" ? (
        <div className="connector-tile__connecting">
          <span className="connector-tile__spinner" aria-hidden />
          <span>Opening {displayName}…</span>
        </div>
      ) : null}

      {tileState === "error" ? (
        <div className="connector-tile__error-block">
          {state.account ? (
            <div className="connector-tile__connected-line">
              <span className="connector-tile__dot connector-tile__dot--green" aria-hidden />
              <span>
                Connected · {state.account}
                {state.primary_repo_full_name
                  ? ` / ${state.primary_repo_full_name}`
                  : ""}
                {state.repo_count > 0 ? ` · ${state.repo_count} repos` : ""}
              </span>
            </div>
          ) : null}
          <div className="connector-tile__error-pill" role="status">
            <span>
              {state.status === "needs_reauth"
                ? "Reconnect required"
                : "Sync failed"}
              {state.last_successful_sync_at
                ? ` · last successful ${fmtRelative(state.last_successful_sync_at)}`
                : ""}
            </span>
            <button
              type="button"
              className="connector-tile__error-action"
              onClick={onRetry}
            >
              Retry →
            </button>
          </div>
        </div>
      ) : null}

      {tileState === "connected" ? (
        <div className="connector-tile__connected-block">
          <div className="connector-tile__connected-line">
            <span className="connector-tile__dot connector-tile__dot--green" aria-hidden />
            <span>
              {state.account ? `Connected · ${state.account}` : "Connected"}
              {state.primary_repo_full_name
                ? ` / ${state.primary_repo_full_name}`
                : ""}
              {state.repo_count > 0 ? ` · ${state.repo_count} repos` : ""}
              {state.last_sync_at
                ? ` · last sync ${fmtRelative(state.last_sync_at)}`
                : ""}
            </span>
          </div>
          <div className="connector-tile__actions">
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              onClick={onSync}
            >
              Sync now
            </button>
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              onClick={onManage}
            >
              Manage →
            </button>
          </div>
        </div>
      ) : null}

      {tileState === "idle" ? (
        <button
          type="button"
          className="connector-tile__cta"
          onClick={onConnect}
          data-not-implemented={notImplemented ? "true" : undefined}
        >
          {ctaLabel}
        </button>
      ) : null}
    </div>
  );
}
