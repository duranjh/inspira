// /connectors — the B1.3 design surface.
//
// Composition:
//   ┌ AuthedShell (top-bar + nav) ─────────────────────────────┐
//   │                                                          │
//   │  Connectors                                              │
//   │  Plug Inspira into your repo and feedback channels.      │
//   │                                                          │
//   │  [Empty-state banner if 0 active connections]            │
//   │                                                          │
//   │  ── Live connectors ────────────────────────────────────│
//   │  [GitHub tile] [Linear tile] [CSV/JSON tile]             │
//   │                                                          │
//   │  ── Coming soon — design partners only ─────────────────│
//   │  [Intercom] [Productboard] [Salesforce] [Help Scout]     │
//   │                                                          │
//   │  ── Future connectors ──────────────────────────────────│
//   │  [Jira] [Zendesk] [Notion]                               │
//   │                                                          │
//   └──────────────────────────────────────────────────────────┘
//
// Tier discipline:
//   - LIVE   tiles use ConnectorTile (4 states only).
//   - SOON   tiles use ComingSoonTile (mailto only).
//   - FUTURE tiles use FutureTile (greyed, no CTA).
//
// Refresh policy: getConnectors() runs on mount + after every
// state-changing action (connect / sync / disconnect). The 60-min
// polling scheduler updates the backend; the FE re-fetches when
// the user comes back to /connectors or after a manual sync to
// flip the tile from idle → connected without waiting a tick.

import { ReactElement, useCallback, useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import { toast } from "../../components/ToastProvider";
import { HttpError } from "../../lib/httpClient";
import { AuthedShell } from "../shared/AuthedShell";
import { useWorkspaceContext } from "../workspaces/WorkspaceContext";
import { ComingSoonTile } from "./ComingSoonTile";
import { CsvPasteDialog, parseFeedbackPaste } from "./CsvPasteDialog";
import { DisconnectDialog } from "./DisconnectDialog";
import { FutureTile } from "./FutureTile";
import { GitHubInstallButton } from "./GitHubInstallButton";
import { LinearConnectDialog } from "./LinearConnectDialog";
import { ConnectorTile } from "./ConnectorTile";
import {
  connectLinear,
  disconnectLinear,
  getConnectors,
  importCsvRows,
  triggerGitHubSync,
  triggerLinearSync,
} from "./api";
import type { ConnectorsResponse, LiveConnectorPayload } from "./types";

export function ConnectorsPage(): ReactElement {
  return (
    <AuthedShell>
      <ConnectorsPageBody />
    </AuthedShell>
  );
}

/**
 * Body lives in a separate component so AuthedShell's first-run
 * gate (0 workspaces) wraps the body. AuthedShell renders the
 * FirstRunCard in its place when no workspace exists.
 */
function ConnectorsPageBody(): ReactElement {
  const ctx = useWorkspaceContext();
  const navigate = useNavigate();
  const location = useLocation();

  const [data, setData] = useState<ConnectorsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [errorBanner, setErrorBanner] = useState<string | null>(null);

  const [linearOpen, setLinearOpen] = useState(false);
  const [csvOpen, setCsvOpen] = useState(false);
  const [disconnectOpen, setDisconnectOpen] = useState(false);
  const csvFileInputRef = useRef<HTMLInputElement | null>(null);

  const refresh = useCallback(async () => {
    if (!ctx.activeWorkspace) {
      setData(null);
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      const res = await getConnectors();
      setData(res);
      setErrorBanner(null);
    } catch (exc) {
      setErrorBanner(
        exc instanceof Error
          ? exc.message
          : "Couldn't load connectors — refresh the page.",
      );
    } finally {
      setLoading(false);
    }
  }, [ctx.activeWorkspace]);

  // Used by both the file-picker path (hidden <input type="file">) and
  // the paste-dialog path. Returns the same shape both paths expect.
  const handleCsvImport = useCallback(
    async (rows: Parameters<typeof importCsvRows>[0]) => {
      const result = await importCsvRows(rows);
      if (result.inserted > 0 && result.skipped > 0) {
        toast.success(
          `Imported ${result.inserted} new item${
            result.inserted === 1 ? "" : "s"
          } (${result.skipped} duplicate${
            result.skipped === 1 ? "" : "s"
          } skipped).`,
        );
      } else if (result.inserted > 0) {
        toast.success(
          `Imported ${result.inserted} item${
            result.inserted === 1 ? "" : "s"
          }.`,
        );
      } else {
        toast.info(
          `No new items imported (${result.skipped} duplicate${
            result.skipped === 1 ? "" : "s"
          } skipped).`,
        );
      }
      void refresh();
    },
    [refresh],
  );

  const handleCsvFileChosen = useCallback(
    async (event: React.ChangeEvent<HTMLInputElement>) => {
      const file = event.target.files?.[0];
      // Always reset the input so the same file can be chosen twice
      // in a row (browsers de-duplicate identical paths otherwise).
      event.target.value = "";
      if (!file) return;
      const sizeMb = file.size / (1024 * 1024);
      if (sizeMb > 50) {
        toast.error(
          `File is ${sizeMb.toFixed(1)} MB — Inspira accepts up to 50 MB. Split the file or upgrade.`,
        );
        return;
      }
      let text: string;
      try {
        text = await file.text();
      } catch (exc) {
        toast.error(
          exc instanceof Error
            ? `Couldn't read the file: ${exc.message}`
            : "Couldn't read the file.",
        );
        return;
      }
      const parse = parseFeedbackPaste(text);
      if (parse.error) {
        toast.error(parse.error);
        return;
      }
      if (parse.rows.length === 0) {
        toast.error("File parsed cleanly but had no rows.");
        return;
      }
      try {
        await handleCsvImport(parse.rows);
      } catch (exc) {
        toast.error(
          exc instanceof Error
            ? `Import failed: ${exc.message}`
            : "Import failed. Try again.",
        );
      }
    },
    [handleCsvImport],
  );

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Toast on OAuth-callback redirect. The backend redirects with
  // `?status=connected` on success or `?status=error&reason=...`
  // on failure; we surface a toast and strip the params from the
  // URL so a refresh doesn't re-fire.
  useEffect(() => {
    const params = new URLSearchParams(location.search);
    const status = params.get("status");
    const reason = params.get("reason");
    if (!status) return;
    if (status === "connected") {
      toast.success("GitHub connected.");
    } else if (status === "error") {
      const msg = reason
        ? `Couldn't connect GitHub — ${reason.replace(/_/g, " ")}.`
        : "Couldn't connect GitHub.";
      toast.error(msg);
    }
    // Strip query params without re-rendering the route hierarchy.
    navigate(location.pathname, { replace: true });
    void refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const github = data?.live.find((d) => d.provider === "github");
  const linear = data?.live.find((d) => d.provider === "linear");
  const csv = data?.live.find((d) => d.provider === "csv_json");

  const hasAnyConnection =
    !!data &&
    data.live.some((d) => d.state.status === "connected");

  return (
    <div className="connectors-page">
      <header className="connectors-page__header">
        <p className="eyebrow">Connectors</p>
        <h1 className="display connectors-page__title">
          Plug <em>Inspira</em> into your channels.
        </h1>
        <p className="meta connectors-page__lede">
          Pull in repo context and feedback. The AI does the triage from
          there.
        </p>
      </header>

      {!loading && !hasAnyConnection ? (
        <div className="connectors-page__empty-banner">
          Inspira is quiet. Connect a feedback channel to wake it up.
        </div>
      ) : null}

      {errorBanner ? (
        <div className="connectors-page__error" role="alert">
          {errorBanner}
        </div>
      ) : null}

      {loading || !data ? (
        <div className="connectors-page__loading" aria-live="polite">
          Loading connectors…
        </div>
      ) : (
        <>
          <section className="connectors-page__section">
            <header className="connectors-page__section-head">
              <h2 className="section-title">Live connectors</h2>
              <span className="chip chip--sage">Available now</span>
            </header>
            <div className="connectors-page__tiles connectors-page__tiles--3">
              {github ? (
                <GitHubInstallButton
                  payload={github}
                  onError={(message) => toast.error(message)}
                  onSyncRequested={async () => {
                    try {
                      await triggerGitHubSync();
                      toast.info("Sync queued.");
                      void refresh();
                    } catch (exc) {
                      toast.error(
                        exc instanceof Error
                          ? exc.message
                          : "Couldn't queue sync.",
                      );
                    }
                  }}
                  onManageRequested={() => setDisconnectOpen(true)}
                />
              ) : null}
              {linear ? (
                <LinearTile
                  payload={linear}
                  onOpen={() => setLinearOpen(true)}
                  onSync={async () => {
                    try {
                      await triggerLinearSync();
                      toast.info("Linear sync queued.");
                      void refresh();
                    } catch (exc) {
                      toast.error(
                        exc instanceof Error
                          ? exc.message
                          : "Couldn't queue Linear sync.",
                      );
                    }
                  }}
                />
              ) : null}
              {csv ? (
                <CsvTile
                  payload={csv}
                  onOpen={() => csvFileInputRef.current?.click()}
                />
              ) : null}
              {/* Hidden file input — triggered by the CsvTile click.
               * Native file picker UX — opens the OS file explorer
               * directly. The CsvPasteDialog (paste-text
               * fallback) is still mounted below for power-users who
               * want to paste raw CSV/JSON; we keep the dialog ref
               * in case we surface it from a "or paste instead" link
               * later. */}
              <input
                ref={csvFileInputRef}
                type="file"
                accept=".csv,.json,text/csv,application/json"
                onChange={handleCsvFileChosen}
                style={{ display: "none" }}
                aria-hidden="true"
              />
            </div>
            {/* Product decision: give partners a one-click
                way to grab a 10-row sample CSV so they can demo the
                CSV-import flow without hunting for their own data. */}
            <p className="meta connectors-page__sample">
              No data handy?{" "}
              <a
                href="/inspira-sample-feedback.csv"
                download="inspira-sample-feedback.csv"
                className="connectors-page__sample-link"
              >
                Download a 10-row sample CSV →
              </a>
            </p>
          </section>

          <section className="connectors-page__section">
            <header className="connectors-page__section-head">
              <h2 className="section-title">
                Coming soon — design partners only
              </h2>
              <span className="chip chip--gold">Mailto launches it</span>
            </header>
            <p className="meta connectors-page__section-lede">
              We're partnering with 2–3 software teams per integration
              before opening publicly. Reach out if you want to be one.
            </p>
            <div className="connectors-page__tiles connectors-page__tiles--2">
              {data.coming_soon.map((d) => (
                <ComingSoonTile key={d.provider} payload={d} />
              ))}
            </div>
          </section>

          <section className="connectors-page__section">
            <header className="connectors-page__section-head">
              <h2 className="section-title connectors-page__section-title--muted">
                Future connectors
              </h2>
              <span className="chip chip--ghost">Backlog</span>
            </header>
            <p className="meta connectors-page__section-lede">
              We'll prioritize these after the design-partner connectors
              above land.
            </p>
            <div className="connectors-page__tiles connectors-page__tiles--row">
              {data.future.map((d) => (
                <FutureTile key={d.provider} payload={d} />
              ))}
            </div>
          </section>
        </>
      )}

      <LinearConnectDialog
        open={linearOpen}
        onClose={() => setLinearOpen(false)}
        onConnect={async (apiKey) => {
          try {
            const result = await connectLinear(apiKey);
            toast.success(
              result.account.name
                ? `Linear connected as ${result.account.name}.`
                : "Linear connected.",
            );
            void refresh();
          } catch (exc) {
            // Re-throw so the dialog surfaces the error inline.
            if (exc instanceof HttpError && exc.status === 401) {
              throw new Error(
                "Linear rejected the API key. Double-check it's active.",
              );
            }
            throw exc;
          }
        }}
      />

      <CsvPasteDialog
        open={csvOpen}
        onClose={() => setCsvOpen(false)}
        onImport={async (rows) => {
          // Same import + toast flow as the file-picker path; the
          // shared handler keeps the two entry points behaviorally
          // identical.
          await handleCsvImport(rows);
        }}
      />

      <DisconnectDialog
        open={disconnectOpen}
        account={github?.state.account ?? null}
        onClose={() => setDisconnectOpen(false)}
        onDisconnected={() => {
          toast.success("GitHub disconnected.");
          void refresh();
        }}
      />
    </div>
  );
}

/** Linear tile — uses the generic ConnectorTile in idle/error/connected
 *  states. Connect → opens LinearConnectDialog; Sync → triggerLinearSync. */
function LinearTile({
  payload,
  onOpen,
  onSync,
}: {
  payload: LiveConnectorPayload;
  onOpen: () => void;
  onSync: () => void;
}): ReactElement {
  return (
    <ConnectorTile
      provider={payload.provider}
      displayName={payload.display_name}
      summary={payload.summary}
      state={payload.state}
      ctaLabel="Connect Linear →"
      onConnect={onOpen}
      onSync={onSync}
      onManage={onOpen}
      onRetry={onOpen}
    />
  );
}

/** CSV / JSON paste-in tile — uses the generic ConnectorTile.
 *  Connect → opens CsvPasteDialog. */
function CsvTile({
  payload,
  onOpen,
}: {
  payload: LiveConnectorPayload;
  onOpen: () => void;
}): ReactElement {
  return (
    <ConnectorTile
      provider={payload.provider}
      displayName={payload.display_name}
      summary={payload.summary}
      state={payload.state}
      ctaLabel="Drop in feedback →"
      notImplemented={payload.state.status === "not_implemented"}
      onConnect={onOpen}
      onSync={() => {
        /* F4 wires re-import; no-op until backend lands. */
      }}
      onManage={onOpen}
      onRetry={onOpen}
    />
  );
}
