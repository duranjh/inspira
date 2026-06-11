// Account > Profile section.
//
// Lets the user edit their display name. Email is read-only — we treat it
// as the immutable account identifier for now and nudge users toward
// support if they need to change it. Submits on blur (when the value
// actually changed) and on Enter.
//
// The update call goes through api.updateProfile which POSTs to
// /api/auth/profile. That route doesn't exist on the backend yet, so calls
// will 404. We surface "Coming soon" via the toast and keep the typed-in
// value in local state so the user isn't confused by a rollback.

import {
  forwardRef,
  useCallback,
  useEffect,
  useRef,
  useState,
  type KeyboardEvent,
} from "react";

import { api, type AuthedUser } from "../../inspira/api";
import { toast } from "../../../components/ToastProvider";
import { t } from "../../../i18n";
import { parseStatus } from "../../../lib/httpStatus";

export type ProfileSectionProps = {
  user: AuthedUser;
  onProfileUpdated?: (updated: AuthedUser) => void;
};

type Status =
  | { kind: "idle" }
  | { kind: "saving" }
  | { kind: "saved" }
  | { kind: "error"; message: string };

// Parse the HTTP status out of the Error.message shape produced by
// api.ts's postJson helper. Same pattern AuthPanel uses.
export const ProfileSection = forwardRef<HTMLInputElement, ProfileSectionProps>(
  function ProfileSection({ user, onProfileUpdated }, forwardedNameInputRef) {
    const [displayName, setDisplayName] = useState(user.display_name);
    const [status, setStatus] = useState<Status>({ kind: "idle" });
    // Stash the last-saved value so we only submit when it actually changed.
    const lastSavedRef = useRef<string>(user.display_name);
    // Timer that clears the "Saved" line back to idle after a beat.
    const savedTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

    // Keep local state in sync if the parent hands us a fresh user after a
    // successful update somewhere else (e.g. another tab).
    useEffect(() => {
      setDisplayName(user.display_name);
      lastSavedRef.current = user.display_name;
    }, [user.display_name]);

    useEffect(() => {
      return () => {
        if (savedTimerRef.current !== null) {
          clearTimeout(savedTimerRef.current);
        }
      };
    }, []);

    const submit = useCallback(async () => {
      const trimmed = displayName.trim();
      if (!trimmed) {
        setStatus({
          kind: "error",
          message: t("account.profile.name_empty_error"),
        });
        return;
      }
      if (trimmed === lastSavedRef.current) {
        // No-op — don't round-trip when nothing changed.
        return;
      }
      setStatus({ kind: "saving" });
      try {
        const res = await api.updateProfile({ display_name: trimmed });
        lastSavedRef.current = res.user.display_name;
        setDisplayName(res.user.display_name);
        onProfileUpdated?.(res.user);
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
          toast.info("Coming soon — profile updates land with the next backend release.");
          setStatus({ kind: "idle" });
        } else {
          const message = t("account.profile.save_error");
          setStatus({ kind: "error", message });
          toast.error(message);
        }
      }
    }, [displayName, onProfileUpdated]);

    const handleKeyDown = useCallback(
      (e: KeyboardEvent<HTMLInputElement>) => {
        if (e.key === "Enter") {
          e.preventDefault();
          void submit();
          (e.target as HTMLInputElement).blur();
        }
      },
      [submit],
    );

    const saving = status.kind === "saving";
    const statusLine =
      status.kind === "saved"
        ? { className: "account-status account-status--saved", text: t("account.profile.saved") }
        : status.kind === "error"
          ? {
              className: "account-status account-status--error",
              text: status.message,
            }
          : status.kind === "saving"
            ? { className: "account-status", text: t("account.profile.saving") }
            : { className: "account-status", text: "\u00A0" };

    return (
      <section className="account-section" aria-labelledby="account-profile-heading">
        <h2 className="account-section__heading" id="account-profile-heading">
          {t("account.profile.heading")}
        </h2>
        <p className="account-section__subtitle">
          {t("account.profile.subtitle")}
        </p>
        <div className="account-section__body">
          <div className="account-field">
            <label className="account-field__label" htmlFor="account-display-name">
              {t("account.profile.display_name_label")}
            </label>
            <input
              ref={forwardedNameInputRef}
              id="account-display-name"
              type="text"
              className="account-field__input"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              onBlur={() => void submit()}
              onKeyDown={handleKeyDown}
              disabled={saving}
              autoComplete="name"
              spellCheck={false}
            />
          </div>

          <div className="account-field">
            <label className="account-field__label" htmlFor="account-email">
              {t("account.profile.email_label")}
            </label>
            <input
              id="account-email"
              type="email"
              className="account-field__input-readonly"
              value={user.email}
              readOnly
              aria-readonly="true"
              tabIndex={-1}
              autoComplete="email"
            />
            <p className="account-field__hint">
              <em>
                {t("account.profile.email_hint")}
              </em>
            </p>
          </div>

          <p className={statusLine.className} role="status" aria-live="polite">
            {statusLine.text}
          </p>
        </div>
      </section>
    );
  },
);
