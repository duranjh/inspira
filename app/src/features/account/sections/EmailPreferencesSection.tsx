// Account > Email preferences (C2).
//
// Three groups:
//   - Product     — weekly digest, feature launches, changelog
//   - Summaries   — workspace summary, project activity
//   - Security    — password reset, new device; disabled-always-on
//
// Autosave pattern mirrors ProfileSection: flipping a toggle fires the
// PATCH immediately (no Save button), we show an inline "Saved." for
// 2.4s, then fade back to idle. An in-flight toggle shows "Saving…" on
// the group so rapid clicking doesn't look like a silent no-op.

import { useCallback, useEffect, useRef, useState } from "react";

import {
  api,
  type EmailPreferences,
  type EmailPreferencesGroupKey,
} from "../../inspira/api";
import { toast } from "../../../components/ToastProvider";
import { t } from "../../../i18n";
import { parseStatus } from "../../../lib/httpStatus";

export type EmailPreferencesSectionProps = {
  isSystem: boolean;
};
type LoadState =
  | { kind: "loading" }
  | { kind: "ready"; prefs: EmailPreferences }
  | { kind: "error" }
  | { kind: "unavailable" };

type Status =
  | { kind: "idle" }
  | { kind: "saving" }
  | { kind: "saved" }
  | { kind: "error"; message: string };

// Defaults used before the backend round-trips so the toggles render
// immediately. Matches the "opt-in by default" stance across the product.
const DEFAULT_PREFS: EmailPreferences = {
  product: {
    weekly_digest: true,
    feature_launches: true,
    changelog: false,
  },
  summaries: {
    workspace_summary: true,
    project_activity: false,
  },
  security: {
    password_reset: true,
    new_device: true,
  },
};

export function EmailPreferencesSection({
  isSystem,
}: EmailPreferencesSectionProps) {
  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const [status, setStatus] = useState<Status>({ kind: "idle" });
  const savedTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (isSystem) return;
    let cancelled = false;
    void (async () => {
      try {
        const res = await api.getEmailPreferences();
        if (cancelled) return;
        setState({ kind: "ready", prefs: res });
      } catch (err) {
        if (cancelled) return;
        const code = parseStatus(err);
        if (code === 404) {
          // Fall back to defaults so the UI is still usable; the toggles
          // flip optimistically and surface a "Coming soon" toast on the
          // first PATCH attempt.
          setState({ kind: "unavailable" });
          return;
        }
        setState({ kind: "error" });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [isSystem]);

  useEffect(() => {
    return () => {
      if (savedTimerRef.current !== null) {
        clearTimeout(savedTimerRef.current);
      }
    };
  }, []);

  const currentPrefs: EmailPreferences =
    state.kind === "ready" ? state.prefs : DEFAULT_PREFS;

  const handleToggle = useCallback(
    async (
      group: EmailPreferencesGroupKey,
      key: string,
      nextValue: boolean,
    ) => {
      // Optimistic update — flip the local state first so the toggle
      // feels instant.
      setState((prev) => {
        const baseline = prev.kind === "ready" ? prev.prefs : DEFAULT_PREFS;
        const merged = {
          ...baseline,
          [group]: {
            ...(baseline as unknown as Record<string, Record<string, boolean>>)[
              group
            ],
            [key]: nextValue,
          },
        } as EmailPreferences;
        return { kind: "ready", prefs: merged };
      });
      setStatus({ kind: "saving" });
      try {
        const res = await api.updateEmailPreference({
          group,
          key,
          value: nextValue,
        });
        setState({ kind: "ready", prefs: res });
        setStatus({ kind: "saved" });
        if (savedTimerRef.current !== null) {
          clearTimeout(savedTimerRef.current);
        }
        savedTimerRef.current = setTimeout(() => {
          setStatus({ kind: "idle" });
        }, 2400);
      } catch (err) {
        const code = parseStatus(err);
        if (code === 404) {
          toast.info(t("account.email_prefs.unavailable"));
          setStatus({ kind: "idle" });
          return;
        }
        setStatus({
          kind: "error",
          message: t("account.email_prefs.save_error"),
        });
        toast.error(t("account.email_prefs.save_error"));
      }
    },
    [],
  );

  if (isSystem) {
    return (
      <section
        className="account-section"
        aria-labelledby="account-email-prefs-heading"
      >
        <h2
          className="account-section__heading"
          id="account-email-prefs-heading"
        >
          {t("account.email_prefs.heading")}
        </h2>
        <p className="account-section__subtitle">
          {t("account.danger.system_note")}
        </p>
      </section>
    );
  }

  const statusLine =
    status.kind === "saved"
      ? {
          className: "account-status account-status--saved",
          text: t("account.email_prefs.saved"),
        }
      : status.kind === "error"
        ? {
            className: "account-status account-status--error",
            text: status.message,
          }
        : status.kind === "saving"
          ? {
              className: "account-status",
              text: t("account.email_prefs.saving"),
            }
          : { className: "account-status", text: "\u00A0" };

  return (
    <section
      className="account-section"
      aria-labelledby="account-email-prefs-heading"
    >
      <h2
        className="account-section__heading"
        id="account-email-prefs-heading"
      >
        {t("account.email_prefs.heading")}
      </h2>
      <p className="account-section__subtitle">
        {t("account.email_prefs.subtitle")}
      </p>
      <div className="account-section__body">
        {state.kind === "loading" ? (
          <p className="account-status" role="status" aria-live="polite">
            {t("account.email_prefs.loading")}
          </p>
        ) : state.kind === "error" ? (
          <p className="account-status account-status--error" role="alert">
            {t("account.email_prefs.load_error")}
          </p>
        ) : null}

        <EmailPrefsGroup
          titleKey="account.email_prefs.group_product"
          groupKey="product"
          rows={[
            {
              key: "weekly_digest",
              labelKey: "account.email_prefs.weekly_digest",
              value: currentPrefs.product.weekly_digest,
            },
            {
              key: "feature_launches",
              labelKey: "account.email_prefs.feature_launches",
              value: currentPrefs.product.feature_launches,
            },
            {
              key: "changelog",
              labelKey: "account.email_prefs.changelog",
              value: currentPrefs.product.changelog,
            },
          ]}
          onToggle={handleToggle}
        />

        <EmailPrefsGroup
          titleKey="account.email_prefs.group_summaries"
          groupKey="summaries"
          rows={[
            {
              key: "workspace_summary",
              labelKey: "account.email_prefs.workspace_summary",
              value: currentPrefs.summaries.workspace_summary,
            },
            {
              key: "project_activity",
              labelKey: "account.email_prefs.project_activity",
              value: currentPrefs.summaries.project_activity,
            },
          ]}
          onToggle={handleToggle}
        />

        <EmailPrefsGroup
          titleKey="account.email_prefs.group_security"
          groupKey="security"
          helperKey="account.email_prefs.security_always_on"
          rows={[
            {
              key: "password_reset",
              labelKey: "account.email_prefs.password_reset",
              value: currentPrefs.security.password_reset,
              disabled: true,
            },
            {
              key: "new_device",
              labelKey: "account.email_prefs.new_device",
              value: currentPrefs.security.new_device,
              disabled: true,
            },
          ]}
          onToggle={handleToggle}
        />

        <p className="account-section__subtitle account-email-prefs__footer">
          <em>{t("account.email_prefs.footer_note")}</em>
        </p>

        <p className={statusLine.className} role="status" aria-live="polite">
          {statusLine.text}
        </p>
      </div>
    </section>
  );
}

// ---- Group subcomponent ------------------------------------------------

type EmailPrefsGroupProps = {
  titleKey: string;
  groupKey: EmailPreferencesGroupKey;
  helperKey?: string;
  rows: Array<{
    key: string;
    labelKey: string;
    value: boolean;
    disabled?: boolean;
  }>;
  onToggle: (
    group: EmailPreferencesGroupKey,
    key: string,
    nextValue: boolean,
  ) => void;
};

function EmailPrefsGroup({
  titleKey,
  groupKey,
  helperKey,
  rows,
  onToggle,
}: EmailPrefsGroupProps) {
  return (
    <div className="account-email-prefs__group">
      <h3 className="account-email-prefs__group-title">{t(titleKey)}</h3>
      {helperKey ? (
        <p className="account-email-prefs__group-helper">
          <em>{t(helperKey)}</em>
        </p>
      ) : null}
      <ul className="account-email-prefs__list">
        {rows.map((row) => (
          <li key={row.key} className="account-email-prefs__row">
            <label className="account-email-prefs__label">
              <input
                type="checkbox"
                className="account-email-prefs__checkbox"
                checked={row.value}
                onChange={(e) =>
                  onToggle(groupKey, row.key, e.target.checked)
                }
                disabled={row.disabled}
              />
              <span className="account-email-prefs__label-text">
                {t(row.labelKey)}
              </span>
            </label>
          </li>
        ))}
      </ul>
    </div>
  );
}
