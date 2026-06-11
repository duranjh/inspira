// Inspira — legal content overlay (privacy policy / terms of service).
//
// A lightweight modal-style reader for the two static legal documents
// we need public before launch. Kept as plain React text so the content
// is readable in source control and easy for a lawyer to mark up. Not
// routed — opens from footer links on the kickoff, auth-gate, and
// projects-list screens, closes with Esc, click-outside, or the × button.
//
// IMPORTANT: this is a first draft, not legal advice. Before public
// launch, have a lawyer review both documents. Everything here follows
// the pattern of "plain-English, honest, narrow in scope" — the product
// is a thinking space, not a high-risk data processor — but jurisdictions
// vary and a real legal review is non-negotiable.

import { useCallback, useEffect, useRef } from "react";

import { t } from "../i18n";

export type LegalOverlayKind = "privacy" | "terms";

export type LegalOverlayProps = {
  open: boolean;
  kind: LegalOverlayKind;
  onClose: () => void;
};

export function LegalOverlay({ open, kind, onClose }: LegalOverlayProps) {
  const closeButtonRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const id = window.requestAnimationFrame(() => {
      closeButtonRef.current?.focus();
    });
    return () => window.cancelAnimationFrame(id);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  const handleBackdropPointerDown = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      if (e.target === e.currentTarget) onClose();
    },
    [onClose],
  );

  if (!open) return null;

  const title = kind === "privacy" ? t("legal.title.privacy") : t("legal.title.terms");
  const lastUpdated = "April 2026";

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={title}
      onPointerDown={handleBackdropPointerDown}
      style={backdropStyle}
    >
      <div style={cardStyle} role="document">
        <header style={headerStyle}>
          <div>
            <div style={eyebrowStyle}>{t("legal.eyebrow")}</div>
            <h1 style={titleStyle}>{title}</h1>
            <div style={updatedStyle}>{t("legal.last_updated", { date: lastUpdated })}</div>
          </div>
          <button
            ref={closeButtonRef}
            type="button"
            onClick={onClose}
            aria-label={t("legal.close_aria")}
            style={closeStyle}
          >
            ×
          </button>
        </header>
        <div style={bodyStyle}>
          {kind === "privacy" ? <PrivacyContent /> : <TermsContent />}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------
// Privacy policy content. First-draft language — lawyer review before
// public launch. The tone matches Inspira's warm-editorial register:
// plain English, honest, narrow in scope. No legalese theater.
// ---------------------------------------------------------------------

function PrivacyContent() {
  return (
    <>
      <p>
        Inspira turns customer feedback into ready-to-build features.
        We've built it to collect as little about you as possible.
        This page is what we actually do with the data you give us.
      </p>

      <h2>{t("legal_overlay.h2.privacy.what_we_store")}</h2>
      <p>When you make an account, we store:</p>
      <ul>
        <li>
          The email address you sign up with and (if you provide one) a
          display name.
        </li>
        <li>
          A salted password hash. We never see or store your password.
        </li>
        <li>
          Your projects — the topics, questions, decisions, and notes you
          create. These are private to your account. We do not read them
          except when required to run the product (e.g., sending a prompt
          to an AI provider to generate a response, per your request).
        </li>
        <li>
          Basic usage metadata (sign-in timestamps, last activity, credit
          balances) so we can keep the product running and prevent abuse.
        </li>
      </ul>

      <h2>{t("legal_overlay.h2.privacy.what_we_share")}</h2>
      <p>
        When you use an AI feature (kickoff, topic Q&A, summary, code
        scaffold), the text of your prompt and the minimum context we
        need to answer it are sent to the AI provider powering that
        feature — currently OpenAI or Anthropic. Their data-handling
        terms govern what happens on their end. We do not sell or share
        your data with anyone else.
      </p>
      <p>
        If you pay for a subscription or a credit pack, our billing
        provider (Stripe) processes the payment. We store the minimum
        identifier we need to match a payment to your account; we never
        see your card number.
      </p>

      <h2>{t("legal_overlay.h2.privacy.how_long")}</h2>
      <p>
        Your projects stay until you delete them. Deleted projects are
        removed from our live database immediately; encrypted backups
        may retain them for up to 30 days before rotation. If you delete
        your account, we delete your data on the same schedule.
      </p>

      <h2>{t("legal_overlay.h2.privacy.your_rights")}</h2>
      <p>
        You can view, export, correct, or delete any of your data from
        the account settings page. If you can't find what you need
        there, email us and we'll handle it manually within 30 days.
      </p>

      <h2>{t("legal_overlay.h2.privacy.cookies")}</h2>
      <p>
        We use a single signed session cookie to keep you logged in.
        That's it — no trackers, no advertising IDs, no third-party
        analytics cookies.
      </p>

      <h2>{t("legal_overlay.h2.privacy.contact")}</h2>
      <p>
        Questions, requests, or concerns: email the address listed in
        the app's support footer. We read every message and reply within
        a few business days.
      </p>

      <p style={fineprintStyle}>
        This is a first draft. A lawyer will review before public
        launch; if any detail here diverges from that review, the
        reviewed version governs.
      </p>
    </>
  );
}

// ---------------------------------------------------------------------
// Terms of service. Same first-draft caveats apply.
// ---------------------------------------------------------------------

function TermsContent() {
  return (
    <>
      <p>
        These terms cover what you can expect from Inspira and what we
        need from you in return. By using the product, you're agreeing
        to them.
      </p>

      <h2>{t("legal_overlay.h2.terms.your_account")}</h2>
      <p>
        You're responsible for keeping your login credentials private
        and for activity that happens under your account. Let us know
        promptly if you suspect your account has been accessed by
        someone else.
      </p>

      <h2>{t("legal_overlay.h2.terms.yours")}</h2>
      <p>
        You keep all rights to the projects, topics, decisions, and
        notes you create in Inspira. We don't claim ownership of your
        content, and we don't use it to train AI models. We only access
        it to operate the product on your behalf — for example, sending
        a prompt to an AI provider when you ask for help.
      </p>

      <h2>{t("legal_overlay.h2.terms.not_allowed")}</h2>
      <p>
        Don't use Inspira to generate, store, or distribute content that
        is illegal, that infringes someone else's rights, or that is
        intended to harass, defraud, or harm others. Don't attempt to
        reverse-engineer, scrape, or overload the service. Don't use
        the product to build a competing one.
      </p>

      <h2>{t("legal_overlay.h2.terms.paid_plans")}</h2>
      <p>
        Some features (like the code scaffold generator) consume
        credits. Free accounts start with a monthly credit allowance;
        paid plans carry a larger allowance and may include additional
        features. Credits are not redeemable for cash and expire at the
        end of your billing cycle unless stated otherwise.
      </p>
      <p>
        You can cancel a paid plan at any time from account settings.
        Cancellation takes effect at the end of the current billing
        cycle; we don't pro-rate partial months.
      </p>

      <h2>{t("legal_overlay.h2.terms.as_is")}</h2>
      <p>
        Inspira is provided as-is, without warranties. AI responses are
        generated; they may be incomplete or wrong. Please verify
        anything important before acting on it.
      </p>

      <h2>{t("legal_overlay.h2.terms.liability")}</h2>
      <p>
        To the extent allowed by law, our total liability for any claim
        arising out of your use of Inspira is limited to the amount you
        paid us in the 12 months before the claim — or $100, whichever
        is greater if you're on a free plan.
      </p>

      <h2>{t("legal_overlay.h2.terms.changes")}</h2>
      <p>
        We may update these terms. We'll notify you in-app for material
        changes; continuing to use the product after a change means you
        accept the new terms.
      </p>

      <h2>{t("legal_overlay.h2.terms.ending")}</h2>
      <p>
        You can close your account from account settings at any time.
        We may suspend or close an account that violates these terms,
        usually after a warning. Data deletion follows the schedule in
        the privacy policy.
      </p>

      <p style={fineprintStyle}>
        This is a first draft. A lawyer will review before public
        launch; the reviewed version will govern.
      </p>
    </>
  );
}

// ---- Inline styles (keeps the scope local, App.css untouched) -------

const backdropStyle: React.CSSProperties = {
  position: "fixed",
  inset: 0,
  background: "rgba(43, 37, 32, 0.55)",
  display: "flex",
  alignItems: "flex-start",
  justifyContent: "center",
  padding: "40px 20px",
  zIndex: 200,
  overflowY: "auto",
};

const cardStyle: React.CSSProperties = {
  maxWidth: 680,
  width: "100%",
  // --paper-lifted flips to warm espresso in dark mode; --border-soft
  // flips to a cream-alpha border. Fallbacks keep the old look intact
  // outside the token scope.
  background: "var(--paper-lifted, #FAF5EA)",
  border: "1px solid var(--border-soft, rgba(43, 37, 32, 0.08))",
  borderRadius: 14,
  padding: "32px 40px 40px",
  boxShadow: "0 24px 64px -28px rgba(43, 37, 32, 0.32)",
  fontFamily: "var(--ff-text, Georgia, serif)",
  color: "var(--ink-1, #2B2520)",
  lineHeight: 1.6,
};

const headerStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "flex-start",
  justifyContent: "space-between",
  gap: 16,
  marginBottom: 24,
  paddingBottom: 16,
  borderBottom: "1px solid var(--border-soft, rgba(43, 37, 32, 0.1))",
};

const eyebrowStyle: React.CSSProperties = {
  fontFamily: "var(--ff-mono, ui-monospace, monospace)",
  fontSize: 11,
  letterSpacing: "0.14em",
  textTransform: "uppercase",
  color: "var(--ink-3, #847B72)",
  marginBottom: 6,
};

const titleStyle: React.CSSProperties = {
  margin: 0,
  fontFamily: "var(--ff-display, Georgia, serif)",
  fontWeight: 500,
  fontSize: 28,
  lineHeight: 1.2,
  letterSpacing: "-0.01em",
};

const updatedStyle: React.CSSProperties = {
  marginTop: 6,
  fontFamily: "var(--ff-mono, ui-monospace, monospace)",
  fontSize: 11,
  color: "var(--ink-3, #847B72)",
};

const closeStyle: React.CSSProperties = {
  appearance: "none",
  background: "transparent",
  border: 0,
  fontSize: 28,
  lineHeight: 1,
  cursor: "pointer",
  color: "var(--ink-3, #847B72)",
  padding: "2px 10px",
  borderRadius: 8,
};

const bodyStyle: React.CSSProperties = {
  fontSize: 15,
};

const fineprintStyle: React.CSSProperties = {
  marginTop: 32,
  paddingTop: 16,
  borderTop: "1px solid var(--border-soft, rgba(43, 37, 32, 0.08))",
  fontSize: 13,
  color: "var(--ink-3, #847B72)",
  fontStyle: "italic",
};
