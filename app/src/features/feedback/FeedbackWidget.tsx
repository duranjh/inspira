// Inspira — Feedback widget (C5).
//
// A floating sage pill anchored bottom-right, visible on authenticated
// app routes only. Opens a small paper-card modal with a type selector
// (Bug / Idea / Something else), a message textarea, an optional
// screenshot attachment, and a "follow up with me" checkbox.
//
// Route gating:
//   - Hidden on anonymous (is_system) visitors — we fetch /api/auth/me
//     on mount and bail when the user is anonymous.
//   - Hidden on marketing routes — we keep an allowlist of paths the
//     launcher must NOT appear on. Pathname changes are tracked via
//     popstate + a monkey-patched history.pushState / replaceState so
//     the widget hides/reveals during soft navigations.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { api, type FeedbackType, type AuthedUser } from "../inspira/api";
import { submitFeedback } from "./api";
import { t } from "../../i18n";
import "./feedback.css";
import { parseStatus } from "../../lib/httpStatus";

// Marketing / legal routes where the pill MUST be hidden. Root ("/")
// is on the list because the marketing homepage renders there for
// signed-out visitors — the InspiraApp kickoff screen also renders at
// "/" but it's only shown to authenticated/anonymous users who have
// already landed past the marketing shell.
const MARKETING_ROUTES = new Set<string>([
  "/",
  "/features",
  "/about",
  "/pricing",
  "/legal",
  "/status",
  "/unsubscribe",
]);

function normalizePath(pathname: string): string {
  if (!pathname) return "/";
  // Strip trailing slash except for the root.
  if (pathname.length > 1 && pathname.endsWith("/")) {
    return pathname.slice(0, -1);
  }
  return pathname;
}

function isMarketingRoute(pathname: string): boolean {
  const normalized = normalizePath(pathname);
  if (MARKETING_ROUTES.has(normalized)) return true;
  // Treat legal sub-paths (e.g. /legal/privacy) as marketing.
  if (normalized.startsWith("/legal/")) return true;
  return false;
}

/**
 * Subscribe to pathname changes. React-router doesn't own the whole
 * app (App.tsx routes by raw pathname), so we wire into history +
 * popstate manually. Returns the current pathname as state and keeps
 * it fresh on both hard and soft navigations.
 */
function usePathname(): string {
  const [pathname, setPathname] = useState<string>(() =>
    typeof window === "undefined" ? "/" : window.location.pathname,
  );

  useEffect(() => {
    if (typeof window === "undefined") return;
    const update = () => setPathname(window.location.pathname);

    // popstate fires on back/forward.
    window.addEventListener("popstate", update);

    // history.pushState / replaceState don't fire a native event, so
    // we wrap them. We save the originals on the window to avoid
    // double-wrapping when multiple instances mount.
    type HistoryMethod = (
      data: unknown,
      unused: string,
      url?: string | URL | null,
    ) => void;
    type PatchedHistory = History & {
      __inspiraPatched?: boolean;
    };
    const h = window.history as PatchedHistory;
    if (!h.__inspiraPatched) {
      const origPush = window.history.pushState.bind(window.history) as HistoryMethod;
      const origReplace = window.history.replaceState.bind(window.history) as HistoryMethod;
      window.history.pushState = function (
        data: unknown,
        unused: string,
        url?: string | URL | null,
      ) {
        origPush(data, unused, url ?? null);
        window.dispatchEvent(new Event("inspira:pathname-changed"));
      };
      window.history.replaceState = function (
        data: unknown,
        unused: string,
        url?: string | URL | null,
      ) {
        origReplace(data, unused, url ?? null);
        window.dispatchEvent(new Event("inspira:pathname-changed"));
      };
      h.__inspiraPatched = true;
    }
    window.addEventListener("inspira:pathname-changed", update);

    return () => {
      window.removeEventListener("popstate", update);
      window.removeEventListener("inspira:pathname-changed", update);
    };
  }, []);

  return pathname;
}
export function FeedbackWidget() {
  const pathname = usePathname();
  const [user, setUser] = useState<AuthedUser | null>(null);
  const [modalOpen, setModalOpen] = useState(false);

  // Fetch the current user on mount to decide visibility. Anonymous
  // (is_system) visitors never see the pill. If the call fails we
  // treat the widget as hidden.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const me = await api.me();
        if (!cancelled) setUser(me);
      } catch {
        if (!cancelled) setUser(null);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const visible = useMemo(() => {
    if (!user) return false;
    if (user.is_system) return false;
    if (isMarketingRoute(pathname)) return false;
    return true;
  }, [user, pathname]);

  if (!visible) return null;

  return (
    <>
      <button
        type="button"
        className="feedback-launcher"
        onClick={() => setModalOpen(true)}
        aria-label={t("feedback.launcher_aria")}
      >
        {t("feedback.launcher_label")}
      </button>
      {modalOpen ? (
        <FeedbackModal
          userEmail={user?.email ?? null}
          onClose={() => setModalOpen(false)}
        />
      ) : null}
    </>
  );
}

// ---- Modal ------------------------------------------------------------

type FeedbackModalProps = {
  userEmail: string | null;
  onClose: () => void;
};

function FeedbackModal({ userEmail, onClose }: FeedbackModalProps) {
  const [type, setType] = useState<FeedbackType>("bug");
  const [message, setMessage] = useState("");
  const [screenshot, setScreenshot] = useState<{
    name: string;
    dataUrl: string;
  } | null>(null);
  const [followUp, setFollowUp] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);
  const autoCloseRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
      }
    };
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, [onClose]);

  useEffect(() => {
    return () => {
      if (autoCloseRef.current !== null) {
        clearTimeout(autoCloseRef.current);
      }
    };
  }, []);

  const handleFile = useCallback((file: File | null) => {
    if (!file) {
      setScreenshot(null);
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      const result = reader.result;
      if (typeof result === "string") {
        setScreenshot({ name: file.name, dataUrl: result });
      }
    };
    reader.readAsDataURL(file);
  }, []);

  const trimmed = message.trim();
  const canSubmit = trimmed.length >= 10 && !submitting;

  const handleSubmit = useCallback(
    async (e: React.FormEvent<HTMLFormElement>) => {
      e.preventDefault();
      if (trimmed.length < 10) {
        setError(t("feedback.min_length_error"));
        return;
      }
      setSubmitting(true);
      setError(null);
      try {
        await submitFeedback({
          type,
          message: trimmed,
          screenshot: screenshot?.dataUrl ?? null,
          follow_up_email: followUp ? userEmail : null,
        });
        setSuccess(true);
        autoCloseRef.current = setTimeout(() => {
          onClose();
        }, 2000);
      } catch (err) {
        const code = parseStatus(err);
        if (code === 404) {
          setError(t("feedback.submit_error"));
        } else {
          setError(t("feedback.submit_error"));
        }
      } finally {
        setSubmitting(false);
      }
    },
    [trimmed, type, screenshot, followUp, userEmail, onClose],
  );

  return (
    <div
      className="feedback-modal"
      role="dialog"
      aria-modal="true"
      aria-labelledby="feedback-title"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="feedback-modal__card">
        <header className="feedback-modal__header">
          <div>
            <h3 className="feedback-modal__title" id="feedback-title">
              {t("feedback.modal_title")}
            </h3>
            <p className="feedback-modal__subtitle">
              {t("feedback.modal_subtitle")}
            </p>
          </div>
          <button
            type="button"
            className="feedback-modal__close"
            onClick={onClose}
            aria-label={t("feedback.close_aria")}
          >
            {"\u00D7"}
          </button>
        </header>
        {success ? (
          <div className="feedback-modal__success">
            <p className="feedback-modal__success-text">
              {t("feedback.success")}
            </p>
          </div>
        ) : (
          <form
            className="feedback-modal__body"
            onSubmit={handleSubmit}
            noValidate
          >
            <div className="feedback-modal__field">
              <span className="feedback-modal__label">
                {t("feedback.type_label")}
              </span>
              <div
                className="feedback-type"
                role="radiogroup"
                aria-label={t("feedback.type_label")}
              >
                {(
                  [
                    { value: "bug", labelKey: "feedback.type_bug" },
                    { value: "idea", labelKey: "feedback.type_idea" },
                    { value: "other", labelKey: "feedback.type_other" },
                  ] as Array<{ value: FeedbackType; labelKey: string }>
                ).map((opt) => (
                  <span key={opt.value} className="feedback-type__pill">
                    <input
                      type="radio"
                      name="feedback-type"
                      className="feedback-type__radio"
                      value={opt.value}
                      checked={type === opt.value}
                      onChange={() => setType(opt.value)}
                      id={`feedback-type-${opt.value}`}
                    />
                    <label
                      htmlFor={`feedback-type-${opt.value}`}
                      className="feedback-type__label"
                    >
                      {t(opt.labelKey)}
                    </label>
                  </span>
                ))}
              </div>
            </div>

            <div className="feedback-modal__field">
              <label
                className="feedback-modal__label"
                htmlFor="feedback-message"
              >
                {t("feedback.message_label")}
              </label>
              <textarea
                id="feedback-message"
                className="feedback-modal__textarea"
                value={message}
                onChange={(e) => setMessage(e.target.value)}
                placeholder={t("feedback.message_placeholder")}
                disabled={submitting}
                required
                minLength={10}
                rows={5}
              />
            </div>

            <div className="feedback-modal__field">
              <span className="feedback-modal__label">
                {t("feedback.screenshot_label")}
              </span>
              <div className="feedback-screenshot">
                {screenshot ? (
                  <div className="feedback-screenshot__attached">
                    <span className="feedback-screenshot__filename">
                      {screenshot.name}
                    </span>
                    <button
                      type="button"
                      className="feedback-screenshot__remove"
                      onClick={() => setScreenshot(null)}
                      aria-label={t("feedback.remove_screenshot")}
                    >
                      {"\u00D7"}
                    </button>
                  </div>
                ) : (
                  <label className="feedback-screenshot__empty">
                    <input
                      type="file"
                      accept="image/*"
                      onChange={(e) =>
                        handleFile(e.target.files?.[0] ?? null)
                      }
                      disabled={submitting}
                    />
                    {t("feedback.add_screenshot")}
                  </label>
                )}
              </div>
            </div>

            {userEmail ? (
              <label className="feedback-followup">
                <input
                  type="checkbox"
                  className="feedback-followup__checkbox"
                  checked={followUp}
                  onChange={(e) => setFollowUp(e.target.checked)}
                  disabled={submitting}
                />
                {t("feedback.follow_up_label")}
              </label>
            ) : null}

            {error ? (
              <p className="feedback-modal__error" role="alert">
                {error}
              </p>
            ) : null}

            <div className="feedback-modal__actions">
              <button
                type="button"
                className="account-btn account-btn--ghost"
                onClick={onClose}
                disabled={submitting}
              >
                {t("feedback.cancel")}
              </button>
              <button
                type="submit"
                className="account-btn"
                disabled={!canSubmit}
              >
                {submitting
                  ? t("feedback.submitting")
                  : t("feedback.submit")}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
