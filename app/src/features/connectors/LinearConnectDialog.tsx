// Linear connect dialog — paste-API-key flow.
//
// C6 ships the UI surface; F4 lands the backend
// /api/v2/connectors/linear/connect endpoint that validates the
// key against Linear's `viewer` GraphQL endpoint, encrypts it via
// Fernet, and persists workspace-scoped via the same composite-PK
// row that the GitHub connector uses.
//
// The "Connect" button is wired but disabled in C6 until the
// backend is ready — partners can read the doc-link copy and
// understand what they'll be pasting in.

import { ReactElement, useState } from "react";

const LINEAR_KEY_DOCS = "https://linear.app/settings/api";

export interface LinearConnectDialogProps {
  open: boolean;
  onClose: () => void;
  onConnect: (apiKey: string) => Promise<void> | void;
  /** Disable the Connect button (e.g., F4 backend not yet
   *  available). Form validation still runs. */
  connectDisabledReason?: string;
}

export function LinearConnectDialog({
  open,
  onClose,
  onConnect,
  connectDisabledReason,
}: LinearConnectDialogProps): ReactElement | null {
  const [apiKey, setApiKey] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!open) return null;

  const trimmed = apiKey.trim();
  const looksValid = /^lin_(api|oauth)_[a-zA-Z0-9_-]{20,}$/.test(trimmed);

  const submit = async () => {
    if (!looksValid || connectDisabledReason) return;
    setSubmitting(true);
    setError(null);
    try {
      await onConnect(trimmed);
      setApiKey("");
      onClose();
    } catch (exc) {
      setError(
        exc instanceof Error
          ? exc.message
          : "Couldn't connect — verify the key is active and try again.",
      );
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div
      className="cw-dialog__backdrop"
      role="dialog"
      aria-modal="true"
      aria-labelledby="linear-dialog-title"
      onClick={(e) => {
        if (e.target === e.currentTarget && !submitting) onClose();
      }}
    >
      <div className="card cw-dialog">
        <header className="cw-dialog__header">
          <h2 id="linear-dialog-title" className="section-title">
            Connect Linear
          </h2>
          <button
            type="button"
            className="btn btn--icon btn--ghost"
            onClick={onClose}
            aria-label="Close dialog"
            disabled={submitting}
          >
            ×
          </button>
        </header>
        <p className="meta cw-dialog__intro">
          Generate a personal API key in{" "}
          <a
            href={LINEAR_KEY_DOCS}
            target="_blank"
            rel="noreferrer noopener"
            className="connector-callout__link"
          >
            Linear Settings · API
          </a>{" "}
          and paste it here. Inspira encrypts and stores it scoped to
          this workspace.
        </p>

        <label className="cw-dialog__field">
          <span className="cw-dialog__label">Linear API key</span>
          <span className="cw-dialog__hint">
            Starts with <code>lin_api_</code> or <code>lin_oauth_</code>.
          </span>
          <input
            type="password"
            className="cw-dialog__input"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            placeholder="lin_api_…"
            autoComplete="off"
            spellCheck={false}
            disabled={submitting}
          />
        </label>

        {connectDisabledReason ? (
          <div className="cw-dialog__hint" role="status">
            {connectDisabledReason}
          </div>
        ) : null}
        {error ? (
          <div className="cw-dialog__error" role="alert">
            {error}
          </div>
        ) : null}

        <div className="cw-dialog__actions">
          <button
            type="button"
            className="btn btn--ghost"
            onClick={onClose}
            disabled={submitting}
          >
            Cancel
          </button>
          <button
            type="button"
            className="btn btn--primary"
            onClick={submit}
            disabled={!looksValid || submitting || !!connectDisabledReason}
          >
            {submitting ? "Connecting…" : "Connect Linear"}
          </button>
        </div>
      </div>
    </div>
  );
}
