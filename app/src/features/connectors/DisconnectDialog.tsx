// GitHub disconnect confirmation + post-disconnect callout.
//
// Two-step UX: first a confirmation (so a misclick on Manage →
// Disconnect doesn't nuke the install), then the callout with
// the github.com/settings/installations link so partners know
// to revoke the App-side install too. The backend DELETE only
// clears the local credential row — GitHub still has the App
// installed until the partner removes it from their org/account
// settings page.

import { ReactElement, useState } from "react";

import { disconnectGitHub } from "./api";

export interface DisconnectDialogProps {
  open: boolean;
  account: string | null;
  onClose: () => void;
  onDisconnected: () => void;
}

type Phase = "confirm" | "post-disconnect" | "submitting" | "error";

export function DisconnectDialog({
  open,
  account,
  onClose,
  onDisconnected,
}: DisconnectDialogProps): ReactElement | null {
  const [phase, setPhase] = useState<Phase>("confirm");
  const [error, setError] = useState<string | null>(null);

  if (!open) return null;

  const submit = async () => {
    setPhase("submitting");
    setError(null);
    try {
      await disconnectGitHub();
      setPhase("post-disconnect");
      onDisconnected();
    } catch (exc) {
      setError(
        exc instanceof Error
          ? exc.message
          : "Couldn't disconnect — try again.",
      );
      setPhase("error");
    }
  };

  const handleClose = () => {
    setPhase("confirm");
    setError(null);
    onClose();
  };

  return (
    <div
      className="cw-dialog__backdrop"
      role="dialog"
      aria-modal="true"
      aria-labelledby="disconnect-dialog-title"
      onClick={(e) => {
        if (e.target === e.currentTarget) handleClose();
      }}
    >
      <div className="card cw-dialog">
        {phase === "post-disconnect" ? (
          <>
            <header className="cw-dialog__header">
              <h2
                id="disconnect-dialog-title"
                className="section-title"
              >
                GitHub disconnected
              </h2>
            </header>
            <p className="meta cw-dialog__intro">
              Inspira no longer reads your repo. To fully revoke
              the GitHub App installation, also remove it from{" "}
              <a
                href="https://github.com/settings/installations"
                target="_blank"
                rel="noreferrer noopener"
                className="connector-callout__link"
              >
                github.com/settings/installations
              </a>
              .
            </p>
            <div className="cw-dialog__actions">
              <button
                type="button"
                className="btn btn--primary"
                onClick={handleClose}
              >
                Done
              </button>
            </div>
          </>
        ) : (
          <>
            <header className="cw-dialog__header">
              <h2
                id="disconnect-dialog-title"
                className="section-title"
              >
                Disconnect GitHub?
              </h2>
              <button
                type="button"
                className="btn btn--icon btn--ghost"
                onClick={handleClose}
                aria-label="Close dialog"
              >
                ×
              </button>
            </header>
            <p className="meta cw-dialog__intro">
              {account ? (
                <>
                  Inspira will stop syncing <strong>{account}</strong>.
                  Your existing snapshots are kept for audit.
                </>
              ) : (
                <>
                  Inspira will stop syncing this workspace's GitHub
                  connector. Existing snapshots are kept for audit.
                </>
              )}
            </p>
            {error ? (
              <div className="cw-dialog__error" role="alert">
                {error}
              </div>
            ) : null}
            <div className="cw-dialog__actions">
              <button
                type="button"
                className="btn btn--ghost"
                onClick={handleClose}
                disabled={phase === "submitting"}
              >
                Cancel
              </button>
              <button
                type="button"
                className="btn btn--danger"
                onClick={submit}
                disabled={phase === "submitting"}
              >
                {phase === "submitting" ? "Disconnecting…" : "Disconnect"}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
