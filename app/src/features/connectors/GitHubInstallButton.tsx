// Wraps the connector tile's "Connect with GitHub" CTA.
//
// Owns the brief connecting state (between user click and the
// browser navigating to GitHub). Calls POST /oauth/start, then
// hard-navigates to the install URL via window.location.href.
// We don't open a popup — partners reported popup blockers
// killing the flow; full-page navigation is more reliable and
// the GitHub redirect lands them right back on /connectors.
//
// CSRF: state token is signed server-side and bound to (user_id,
// workspace_id) in the payload. The callback re-checks that the
// session user matches the bound user (W2 watch point #1).

import { ReactElement, useState } from "react";

import { startGitHubOAuth } from "./api";
import { ConnectorTile } from "./ConnectorTile";
import type { LiveConnectorPayload } from "./types";

export interface GitHubInstallButtonProps {
  payload: LiveConnectorPayload;
  onError: (message: string) => void;
  onSyncRequested: () => void;
  onManageRequested: () => void;
}

export function GitHubInstallButton({
  payload,
  onError,
  onSyncRequested,
  onManageRequested,
}: GitHubInstallButtonProps): ReactElement {
  const [connecting, setConnecting] = useState(false);

  const startConnect = async () => {
    // Open the new tab synchronously (inside the user-gesture
    // callstack) so popup blockers don't intercept; the BE call to
    // mint the install_url races; redirect the placeholder tab once
    // it returns.
    //
    // We deliberately do NOT pass "noopener,noreferrer" to window.open
    // here — Chrome severs the parent→child link when those features
    // are set, which makes `placeholder.location.href = ...` a silent
    // no-op (the blank tab just hangs forever). Removing them lets us
    // navigate the placeholder. We then null out the OAuth tab's
    // window.opener once GitHub takes over the page so the GitHub
    // origin can't reach back into Inspira.
    const placeholder = window.open("about:blank", "_blank");
    setConnecting(true);
    try {
      const { install_url } = await startGitHubOAuth();
      if (placeholder && !placeholder.closed) {
        try {
          // Detach so the GitHub-origin page can't access Inspira via
          // window.opener after navigation completes.
          (placeholder as Window & { opener: unknown }).opener = null;
        } catch {
          // Best-effort — some browsers throw on writing opener;
          // cross-origin policy blocks GitHub from doing anything
          // useful with it anyway once navigation lands.
        }
        placeholder.location.href = install_url;
        // Re-enable the button immediately — original tab keeps its
        // /connectors context; the user comes back to this surface
        // after granting access in the new tab.
        setConnecting(false);
      } else {
        // Popup blocked OR user closed the placeholder before we
        // could redirect it. Fall through to same-tab nav so they
        // can still complete the install.
        window.location.href = install_url;
      }
    } catch (exc) {
      placeholder?.close();
      setConnecting(false);
      const message =
        exc instanceof Error
          ? exc.message
          : "Couldn't start the GitHub connect flow. Try again.";
      onError(message);
    }
  };

  const retry =
    payload.state.status === "needs_reauth" ? startConnect : onSyncRequested;

  // Append a multi-account hint to the summary when the tile is in
  // idle state. Partners often install Inspira's GitHub App on
  // their personal account first, then realise they want the
  // throwaway / org account — without this hint, github.com routes
  // them to the existing installation page (its session, not ours).
  const isIdle = payload.state.status === "not_connected";
  const summary = isIdle
    ? `${payload.summary} · Tip: sign out of github.com first if you want to install on a different account.`
    : payload.summary;

  return (
    <ConnectorTile
      provider={payload.provider}
      displayName={payload.display_name}
      summary={summary}
      state={payload.state}
      connecting={connecting}
      ctaLabel="Connect with GitHub →"
      onConnect={startConnect}
      onSync={onSyncRequested}
      onManage={onManageRequested}
      onRetry={retry}
    />
  );
}
