// Account > Security > Active sessions table.
//
// Lists every device currently signed into the account. The row matching
// `current: true` is highlighted with a sage left border. Per-row revoke
// button kills that session alone; "Sign out other sessions" at the
// bottom revokes everything except the current device.
//
// Backend routes are stubs today — 404s surface a quiet "coming soon"
// toast and leave the list empty so design review can still inspect
// structure without a server round-trip.

import { useCallback, useEffect, useState } from "react";

import { api, type AuthSessionRow } from "../../inspira/api";
import { toast } from "../../../components/ToastProvider";
import { formatRelativeTime, t } from "../../../i18n";
import { parseStatus } from "../../../lib/httpStatus";
type LoadState =
  | { kind: "loading" }
  | { kind: "ready"; sessions: AuthSessionRow[] }
  | { kind: "error" }
  | { kind: "unavailable" };

export function ActiveSessionsTable() {
  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const [revokingId, setRevokingId] = useState<string | null>(null);
  const [revokingAll, setRevokingAll] = useState(false);

  const refresh = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      const res = await api.listSessions();
      setState({ kind: "ready", sessions: res.sessions });
    } catch (err) {
      const status = parseStatus(err);
      if (status === 404) {
        setState({ kind: "unavailable" });
        return;
      }
      setState({ kind: "error" });
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const handleRevoke = useCallback(
    async (id: string) => {
      setRevokingId(id);
      try {
        await api.revokeSession(id);
        await refresh();
      } catch (err) {
        const status = parseStatus(err);
        if (status === 404) {
          toast.info(t("account.security.unavailable"));
        } else {
          toast.error(t("account.security.sessions_error"));
        }
      } finally {
        setRevokingId(null);
      }
    },
    [refresh],
  );

  const handleRevokeAll = useCallback(async () => {
    setRevokingAll(true);
    try {
      await api.revokeAllOtherSessions();
      toast.success(t("account.security.sign_out_others_done"));
      await refresh();
    } catch (err) {
      const status = parseStatus(err);
      if (status === 404) {
        toast.info(t("account.security.unavailable"));
      } else {
        toast.error(t("account.security.sessions_error"));
      }
    } finally {
      setRevokingAll(false);
    }
  }, [refresh]);

  if (state.kind === "loading") {
    return (
      <p className="account-status" role="status" aria-live="polite">
        {t("account.security.sessions_loading")}
      </p>
    );
  }

  if (state.kind === "error") {
    return (
      <p className="account-status account-status--error" role="alert">
        {t("account.security.sessions_error")}
      </p>
    );
  }

  if (state.kind === "unavailable") {
    return (
      <p className="account-status" role="status">
        <em>{t("account.security.unavailable")}</em>
      </p>
    );
  }

  if (state.sessions.length === 0) {
    return (
      <p className="account-status" role="status">
        {t("account.security.sessions_empty")}
      </p>
    );
  }

  const otherCount = state.sessions.filter((s) => !s.current).length;

  return (
    <div className="account-sessions">
      <table className="account-sessions__table">
        <thead>
          <tr>
            <th className="account-sessions__th">
              {t("account.sessions.col_device")}
            </th>
            <th className="account-sessions__th">
              {t("account.sessions.col_location")}
            </th>
            <th className="account-sessions__th">
              {t("account.sessions.col_ip")}
            </th>
            <th className="account-sessions__th">
              {t("account.sessions.col_last_active")}
            </th>
            <th className="account-sessions__th">
              <span className="account-sessions__sr">
                {t("account.sessions.col_actions")}
              </span>
            </th>
          </tr>
        </thead>
        <tbody>
          {state.sessions.map((session) => (
            <tr
              key={session.id}
              className={
                session.current
                  ? "account-sessions__row account-sessions__row--current"
                  : "account-sessions__row"
              }
            >
              <td className="account-sessions__td">
                <div className="account-sessions__device">
                  <span className="account-sessions__device-name">
                    {session.device}
                  </span>
                  {session.current ? (
                    <span className="account-sessions__this-device">
                      {t("account.security.session_this_device")}
                    </span>
                  ) : null}
                </div>
              </td>
              <td className="account-sessions__td">{session.location}</td>
              <td className="account-sessions__td account-sessions__td--mono">
                {session.ip}
              </td>
              <td className="account-sessions__td">
                {formatRelativeTime(session.last_active)}
              </td>
              <td className="account-sessions__td account-sessions__td--actions">
                {session.current ? null : (
                  <button
                    type="button"
                    className="account-sessions__revoke"
                    onClick={() => handleRevoke(session.id)}
                    disabled={revokingId === session.id}
                  >
                    {revokingId === session.id
                      ? t("account.security.session_revoking")
                      : t("account.security.session_revoke")}
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {otherCount > 0 ? (
        <div className="account-sessions__footer">
          <button
            type="button"
            className="account-btn account-btn--ghost"
            onClick={handleRevokeAll}
            disabled={revokingAll}
          >
            {revokingAll
              ? t("account.security.signing_out_others")
              : t("account.security.sign_out_others")}
          </button>
        </div>
      ) : null}
    </div>
  );
}
