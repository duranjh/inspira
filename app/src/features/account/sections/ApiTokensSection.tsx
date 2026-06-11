// Account > API tokens section.
//
// Personal Access Tokens for external automations -- Zapier workflows,
// the Inspira MCP server, a user's own script.  Users mint named tokens
// here and paste the raw string into whichever integration needs it.
//
// Flow:
//   1. Click "Create new token" -> inline form asking for a label.
//   2. Submit -> backend returns the raw token exactly once.
//   3. Modal view swaps to a copy-once pane with a monospace field and
//      "Copy" button.  The raw value never makes another round trip.
//   4. User clicks "I've saved it, close" -> form clears, list refreshes.
//
// The list view below renders every token the user has ever minted,
// newest first.  Revoked tokens stay in the list (greyed out) so the
// user can see the audit trail; active tokens carry a "Revoke" button.
//
// This section bounces with a polite system-note when the caller is the
// anonymous/system user -- PATs only make sense for real accounts (they
// outlive any single tab, so an anon session with a PAT is a
// credential-rotation footgun).

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
  type ReactElement,
} from "react";

import { api, type AccessTokenSummary } from "../../inspira/api";
import { toast } from "../../../components/ToastProvider";
import { t } from "../../../i18n";

export type ApiTokensSectionProps = {
  isSystem: boolean;
};

type LoadState =
  | { kind: "loading" }
  | { kind: "ready"; tokens: AccessTokenSummary[] }
  | { kind: "error"; message: string };

type DialogState =
  | { kind: "closed" }
  | { kind: "name"; name: string; submitting: boolean; error: string | null }
  | {
      kind: "copy";
      token_id: string;
      name: string;
      raw: string;
      copied: boolean;
    };

// Format an ISO-8601 stamp as a short local date/time.  We deliberately
// drop seconds -- the list view doesn't need them.  Falls back to the
// raw string when ``Date`` can't parse it (robustness on odd backends).
function formatTimestamp(raw: string | null | undefined): string {
  if (!raw) return "";
  const d = new Date(raw);
  if (Number.isNaN(d.getTime())) return raw;
  return d.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

export function ApiTokensSection({
  isSystem,
}: ApiTokensSectionProps): ReactElement {
  const [loadState, setLoadState] = useState<LoadState>({ kind: "loading" });
  const [dialog, setDialog] = useState<DialogState>({ kind: "closed" });
  const [revokingId, setRevokingId] = useState<string | null>(null);
  // Auto-focus the name input the moment the dialog opens.  requestAnimationFrame
  // lets the DOM node mount first so ref.current is valid.
  const nameInputRef = useRef<HTMLInputElement | null>(null);
  const copyInputRef = useRef<HTMLInputElement | null>(null);

  const refresh = useCallback(async () => {
    if (isSystem) {
      // Short-circuit -- the backend would 403 and we don't want to
      // present that to the user as an error.
      setLoadState({ kind: "ready", tokens: [] });
      return;
    }
    setLoadState({ kind: "loading" });
    try {
      const { tokens } = await api.listAccessTokens();
      setLoadState({ kind: "ready", tokens });
    } catch (err) {
      console.error("[Inspira] API tokens list failed", err);
      setLoadState({
        kind: "error",
        message: t("api_tokens.load_error"),
      });
    }
  }, [isSystem]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Focus the name input on mount of the "name" dialog phase.
  useEffect(() => {
    if (dialog.kind === "name") {
      const id = window.requestAnimationFrame(() => {
        nameInputRef.current?.focus();
      });
      return () => window.cancelAnimationFrame(id);
    }
    return undefined;
  }, [dialog.kind]);

  // Select the raw token on mount of the "copy" dialog phase so a
  // keyboard user can press Cmd-C immediately without hunting for
  // the field.
  useEffect(() => {
    if (dialog.kind === "copy") {
      const id = window.requestAnimationFrame(() => {
        const el = copyInputRef.current;
        if (el) {
          el.focus();
          try {
            el.select();
          } catch {
            /* non-selectable input types are fine to ignore */
          }
        }
      });
      return () => window.cancelAnimationFrame(id);
    }
    return undefined;
  }, [dialog.kind]);

  const handleOpenCreate = useCallback(() => {
    setDialog({ kind: "name", name: "", submitting: false, error: null });
  }, []);

  const handleNameSubmit = useCallback(
    async (e: FormEvent<HTMLFormElement>) => {
      e.preventDefault();
      if (dialog.kind !== "name") return;
      const trimmed = dialog.name.trim();
      if (!trimmed) {
        setDialog({
          ...dialog,
          error: t("api_tokens.create_error_empty"),
        });
        return;
      }
      setDialog({ ...dialog, submitting: true, error: null });
      try {
        const minted = await api.mintAccessToken(trimmed);
        setDialog({
          kind: "copy",
          token_id: minted.token_id,
          name: minted.name,
          raw: minted.token,
          copied: false,
        });
        // Optimistic refresh so the list has the new row when the
        // user closes the dialog.  The new row won't carry the raw
        // token -- it's already in the copy dialog.
        void refresh();
      } catch (err) {
        console.error("[Inspira] API token create failed", err);
        setDialog({
          ...dialog,
          submitting: false,
          error: t("api_tokens.create_error"),
        });
      }
    },
    [dialog, refresh],
  );

  const handleCopy = useCallback(async () => {
    if (dialog.kind !== "copy") return;
    const raw = dialog.raw;
    try {
      await navigator.clipboard.writeText(raw);
      setDialog({ ...dialog, copied: true });
      toast.success(t("api_tokens.copy_copied"));
    } catch {
      // Common on insecure HTTP origins and older browsers.  Fall
      // back to "select and copy manually" -- the field is already
      // selected from the mount effect above.
      toast.info(t("api_tokens.copy_copy_failed"));
      copyInputRef.current?.select();
    }
  }, [dialog]);

  const handleCloseDialog = useCallback(() => {
    setDialog({ kind: "closed" });
  }, []);

  const handleRevoke = useCallback(
    async (token: AccessTokenSummary) => {
      setRevokingId(token.token_id);
      try {
        await api.revokeAccessToken(token.token_id);
        toast.success(t("api_tokens.revoke_success"));
        await refresh();
      } catch {
        toast.error(t("api_tokens.revoke_error"));
      } finally {
        setRevokingId(null);
      }
    },
    [refresh],
  );

  // Group active tokens first, revoked at the bottom.  The list view
  // keeps revoked rows visible (audit trail) but de-emphasised.
  const groupedTokens = useMemo(() => {
    if (loadState.kind !== "ready") {
      return { active: [] as AccessTokenSummary[], revoked: [] as AccessTokenSummary[] };
    }
    const active: AccessTokenSummary[] = [];
    const revoked: AccessTokenSummary[] = [];
    for (const tok of loadState.tokens) {
      if (tok.revoked_at) revoked.push(tok);
      else active.push(tok);
    }
    return { active, revoked };
  }, [loadState]);

  return (
    <section
      className="account-section"
      aria-labelledby="account-api-tokens-heading"
    >
      <p className="account-theme-label">{t("api_tokens.eyebrow")}</p>
      <h2
        className="account-section__heading"
        id="account-api-tokens-heading"
      >
        {t("api_tokens.heading")}
      </h2>
      <p className="account-section__subtitle">
        {t("api_tokens.subtitle")}
      </p>
      <div className="account-section__body">
        {isSystem ? (
          <p className="account-status">
            <em>{t("api_tokens.system_note")}</em>
          </p>
        ) : (
          <>
            {dialog.kind === "closed" ? (
              <button
                type="button"
                className="account-btn"
                onClick={handleOpenCreate}
              >
                {t("api_tokens.create_button")}
              </button>
            ) : dialog.kind === "name" ? (
              <form
                className="account-danger__confirm"
                onSubmit={handleNameSubmit}
                noValidate
              >
                <h3 className="account-danger__heading">
                  {t("api_tokens.create_dialog_title")}
                </h3>
                <p className="account-field__hint">
                  {t("api_tokens.create_dialog_body")}
                </p>
                <div className="account-field">
                  <label
                    className="account-field__label"
                    htmlFor="api-token-name"
                  >
                    {t("api_tokens.name_label")}
                  </label>
                  <input
                    id="api-token-name"
                    ref={nameInputRef}
                    type="text"
                    className="account-field__input"
                    value={dialog.name}
                    onChange={(e) =>
                      setDialog({ ...dialog, name: e.target.value, error: null })
                    }
                    placeholder={t("api_tokens.name_placeholder")}
                    disabled={dialog.submitting}
                    maxLength={80}
                    autoComplete="off"
                    spellCheck={false}
                  />
                </div>
                {dialog.error ? (
                  <p
                    className="account-status account-status--error"
                    role="alert"
                    aria-live="assertive"
                  >
                    {dialog.error}
                  </p>
                ) : null}
                <div className="account-danger__actions">
                  <button
                    type="submit"
                    className="account-btn"
                    disabled={dialog.submitting || !dialog.name.trim()}
                  >
                    {dialog.submitting
                      ? t("api_tokens.create_submitting")
                      : t("api_tokens.create_submit")}
                  </button>
                  <button
                    type="button"
                    className="account-btn account-btn--ghost"
                    onClick={handleCloseDialog}
                    disabled={dialog.submitting}
                  >
                    {t("api_tokens.create_cancel")}
                  </button>
                </div>
              </form>
            ) : (
              // Copy-once phase.  The raw token appears in a monospace
              // read-only field that's selected on mount, with a big
              // Copy button next to it.  No "Regenerate" affordance on
              // purpose -- if they lose this token, they create a
              // fresh one.  We never serve the raw value again.
              <div className="account-danger__confirm" role="group">
                <h3 className="account-danger__heading">
                  {t("api_tokens.copy_once_title")}
                </h3>
                <p className="account-danger__body">
                  {t("api_tokens.copy_once_body")}
                </p>
                <div className="account-field">
                  <label
                    className="account-field__label"
                    htmlFor="api-token-raw"
                  >
                    {dialog.name}
                  </label>
                  <input
                    id="api-token-raw"
                    ref={copyInputRef}
                    type="text"
                    className="account-field__input-readonly"
                    value={dialog.raw}
                    readOnly
                    spellCheck={false}
                    style={{ fontFamily: "var(--ff-mono)" }}
                    onFocus={(e) => e.target.select()}
                  />
                </div>
                <div className="account-danger__actions">
                  <button
                    type="button"
                    className="account-btn"
                    onClick={handleCopy}
                  >
                    {dialog.copied
                      ? t("api_tokens.copy_copied")
                      : t("api_tokens.copy_button")}
                  </button>
                  <button
                    type="button"
                    className="account-btn account-btn--ghost"
                    onClick={handleCloseDialog}
                  >
                    {t("api_tokens.copy_close")}
                  </button>
                </div>
              </div>
            )}

            {/* ---- List ---- */}
            {loadState.kind === "loading" ? (
              <p className="account-status">
                <em>{t("api_tokens.loading")}</em>
              </p>
            ) : loadState.kind === "error" ? (
              <p
                className="account-status account-status--error"
                role="alert"
                aria-live="polite"
              >
                {t("api_tokens.load_error")}
              </p>
            ) : loadState.tokens.length === 0 ? (
              <p className="account-status">
                <em>{t("api_tokens.empty")}</em>
              </p>
            ) : (
              <ul
                className="account-tokens-list"
                aria-label={t("api_tokens.heading")}
                style={{
                  listStyle: "none",
                  padding: 0,
                  margin: 0,
                  display: "flex",
                  flexDirection: "column",
                  gap: 12,
                }}
              >
                {[...groupedTokens.active, ...groupedTokens.revoked].map(
                  (tok) => {
                    const isRevoked = Boolean(tok.revoked_at);
                    return (
                      <li
                        key={tok.token_id}
                        className="account-token-row"
                        style={{
                          display: "flex",
                          flexDirection: "row",
                          alignItems: "flex-start",
                          justifyContent: "space-between",
                          gap: 16,
                          padding: "12px 14px",
                          border: "1px solid var(--paper-edge)",
                          borderRadius: 10,
                          opacity: isRevoked ? 0.55 : 1,
                        }}
                      >
                        <div
                          style={{
                            display: "flex",
                            flexDirection: "column",
                            gap: 4,
                            minWidth: 0,
                          }}
                        >
                          <strong
                            style={{
                              fontFamily: "var(--ff-serif)",
                              fontWeight: 500,
                              fontSize: 16,
                              color: "var(--ink)",
                              textDecoration: isRevoked
                                ? "line-through"
                                : "none",
                            }}
                          >
                            {tok.name}
                          </strong>
                          <span
                            style={{
                              fontFamily: "var(--ff-mono)",
                              fontSize: 11,
                              color: "var(--ink-3)",
                            }}
                          >
                            {t("api_tokens.created_label")}{" "}
                            {formatTimestamp(tok.created_at)}
                            {" \u00B7 "}
                            {tok.last_used_at
                              ? `${t("api_tokens.last_used_label")} ${formatTimestamp(tok.last_used_at)}`
                              : t("api_tokens.last_used_never")}
                            {isRevoked
                              ? ` \u00B7 ${t(
                                  "api_tokens.revoked_label",
                                )} ${formatTimestamp(tok.revoked_at)}`
                              : ` \u00B7 ${t("api_tokens.active_label")}`}
                          </span>
                        </div>
                        {!isRevoked ? (
                          <button
                            type="button"
                            className="account-btn account-btn--ghost"
                            onClick={() => void handleRevoke(tok)}
                            disabled={revokingId === tok.token_id}
                          >
                            {revokingId === tok.token_id
                              ? t("api_tokens.revoking")
                              : t("api_tokens.revoke_button")}
                          </button>
                        ) : null}
                      </li>
                    );
                  },
                )}
              </ul>
            )}
          </>
        )}
      </div>
    </section>
  );
}
