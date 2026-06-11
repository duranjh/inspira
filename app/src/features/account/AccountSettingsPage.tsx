// Inspira — Account Settings page.
//
// Full-viewport paper overlay (fixed inset:0) that hosts the user's self-
// service controls: profile, password, theme, and danger zone. The page
// is intentionally simple — no routing, no side nav — so it reads more
// like a quiet editorial sheet than a settings dashboard.
//
// Composition:
//   - ProfileSection      — display name (editable), email (read-only)
//   - PasswordSection     — change password form, Coming-soon-friendly
//   - ThemeSection        — light / dark / system, persisted locally
//   - DangerZoneSection   — typed-confirmation delete, Coming-soon-friendly
//
// Props:
//   user              — the currently signed-in (or system) AuthedUser
//   onClose           — caller decides how to unmount (usually sets state
//                       back to the canvas phase)
//   onProfileUpdated  — optional; caller can mirror the updated user into
//                       its own state so the top-bar avatar stays fresh

import { useCallback, useEffect, useRef } from "react";

import { ApiTokensSection } from "./sections/ApiTokensSection";
import { ByokSection } from "./sections/ByokSection";
import { DangerZoneSection } from "./sections/DangerZoneSection";
import { EmailPreferencesSection } from "./sections/EmailPreferencesSection";
import { ModelSection } from "./sections/ModelSection";
import { PasswordSection } from "./sections/PasswordSection";
import { ProfileSection } from "./sections/ProfileSection";
import { SecuritySection } from "./sections/SecuritySection";
import { ThemeSection } from "./sections/ThemeSection";
import type { AuthedUser } from "../inspira/api";
import { toast } from "../../components/ToastProvider";
import "./account.css";

import { t } from "../../i18n";

export type AccountSettingsPageProps = {
  user: AuthedUser;
  onClose: () => void;
  onProfileUpdated?: (updated: AuthedUser) => void;
};

export function AccountSettingsPage({
  user,
  onClose,
  onProfileUpdated,
}: AccountSettingsPageProps) {
  const displayNameInputRef = useRef<HTMLInputElement | null>(null);

  const handleResetTours = useCallback(() => {
    try {
      localStorage.removeItem("inspira_onboarded_canvas");
      localStorage.removeItem("inspira_onboarded_homepage");
      // Also clear the v4 workspace tour flag so partners can replay
      // the welcome modal + spotlight sequence after a settings reset.
      localStorage.removeItem("inspira_workspace_tour_completed");
    } catch {
      /* storage disabled — ignore */
    }
    toast.success(t("toast.tours_reset"));
  }, []);

  // Esc closes. Capture phase so the InspiraApp's own Esc binding for
  // topic detail / help overlay doesn't race with this one.
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

  // Lock background scroll while the overlay is up.
  useEffect(() => {
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, []);

  // Focus the first input on open. requestAnimationFrame avoids
  // focusing before the overlay's fade-in animation settles on low-end
  // devices where mount and focus can otherwise race.
  useEffect(() => {
    const id = window.requestAnimationFrame(() => {
      displayNameInputRef.current?.focus();
      // Leave the cursor at the end of the existing value rather than
      // selecting it all, which would make the first keystroke overwrite
      // the user's current name by accident.
      const el = displayNameInputRef.current;
      if (el) {
        const len = el.value.length;
        try {
          el.setSelectionRange(len, len);
        } catch {
          // Some input types don't support selection; safe to ignore.
        }
      }
    });
    return () => window.cancelAnimationFrame(id);
  }, []);

  return (
    <div
      className="account-page"
      role="dialog"
      aria-modal="true"
      aria-label={t("account.page.aria")}
    >
      <header className="account-page__topbar">
        <h1 className="account-page__brand">{t("account.page.heading")}</h1>
        <button
          type="button"
          className="account-page__close"
          onClick={onClose}
          aria-label={t("account.page.close_aria")}
          title={t("account.page.close_title")}
        >
          {"\u00D7"}
        </button>
      </header>
      <div className="account-page__inner">
        <ProfileSection
          ref={displayNameInputRef}
          user={user}
          onProfileUpdated={onProfileUpdated}
        />
        <PasswordSection isSystem={user.is_system} />
        <SecuritySection isSystem={user.is_system} />
        <ThemeSection />
        <EmailPreferencesSection isSystem={user.is_system} />
        <ModelSection />
        <ByokSection />
        <section
          className="account-section"
          aria-labelledby="account-billing-heading"
        >
          <h2
            className="account-section__heading"
            id="account-billing-heading"
          >
            {t("billing.account_settings.link_label")}
          </h2>
          <div className="account-section__body">
            <p className="account-section__subtitle">
              {t("billing.account_settings.link_help")}
            </p>
            <button
              type="button"
              className="account-btn account-btn--ghost"
              onClick={() => {
                window.location.assign("/billing");
              }}
            >
              {t("billing.account_settings.link_cta")}
            </button>
          </div>
        </section>
        <section
          className="account-section"
          aria-labelledby="account-members-heading"
        >
          <h2
            className="account-section__heading"
            id="account-members-heading"
          >
            {t("account.members.link_label")}
          </h2>
          <div className="account-section__body">
            <p className="account-section__subtitle">
              {t("account.members.link_help")}
            </p>
            <button
              type="button"
              className="account-btn account-btn--ghost"
              onClick={() => {
                window.location.assign("/members");
              }}
            >
              {t("account.members.link_cta")}
            </button>
          </div>
        </section>
        <section className="account-section" aria-labelledby="account-tours-heading">
          <h2 className="account-section__heading" id="account-tours-heading">
            {t("account.reset_tours_label")}
          </h2>
          <div className="account-section__body">
            <p className="account-section__subtitle">{t("account.reset_tours_help")}</p>
            <button
              type="button"
              className="account-btn account-btn--ghost"
              onClick={handleResetTours}
            >
              {t("account.reset_tours_label")}
            </button>
          </div>
        </section>
        <ApiTokensSection isSystem={user.is_system} />
        <DangerZoneSection isSystem={user.is_system} />
      </div>
    </div>
  );
}
