// Inspira — cookie-consent banner.
//
// Tiny bottom-centered bar that shows on first visit until the user
// picks "Accept" (stores `all`) or "Only essential" (stores `essential`)
// in localStorage under `inspira_cookie_consent`. Subsequent visits read
// the stored value on mount and stay hidden.
//
// The analytics agent (running separately) wires up Plausible/PostHog
// and is expected to read the same localStorage key before firing any
// non-essential event. This file owns the UI and the consent key; it
// deliberately does not know about analytics providers.
//
// Keyboard behaviour:
//   * Opens in its own focus trap so Tab cycles between the two buttons.
//   * Escape dismisses as "essential only" — the less-invasive default
//     so a keyboard user who panics on the overlay lands in the
//     privacy-preserving branch.
//   * On mount the primary "Accept" button is given focus so a screen
//     reader announces the banner as a live region.

import { useCallback, useEffect, useRef, useState } from "react";

import { t } from "../i18n";

const STORAGE_KEY = "inspira_cookie_consent";
export type ConsentValue = "all" | "essential";

// Exposed for the analytics integration to read synchronously without
// duplicating the localStorage-key string.
export function readCookieConsent(): ConsentValue | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (raw === "all" || raw === "essential") return raw;
    return null;
  } catch {
    return null;
  }
}

function writeCookieConsent(value: ConsentValue): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(STORAGE_KEY, value);
  } catch {
    // localStorage disabled (incognito + strict mode, say) — the banner
    // simply reappears on next mount. Acceptable.
  }
}

export function CookieBanner() {
  const [visible, setVisible] = useState(false);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const acceptRef = useRef<HTMLButtonElement | null>(null);
  const essentialRef = useRef<HTMLButtonElement | null>(null);

  // First-mount read. We start hidden so there's no flash of the banner
  // for returning visitors who already chose.
  useEffect(() => {
    if (readCookieConsent() === null) setVisible(true);
  }, []);

  // Focus the primary action on open so keyboard/screen-reader users
  // land in the banner automatically.
  useEffect(() => {
    if (!visible) return;
    const id = window.requestAnimationFrame(() => {
      acceptRef.current?.focus();
    });
    return () => window.cancelAnimationFrame(id);
  }, [visible]);

  const dismiss = useCallback(
    (value: ConsentValue) => {
      writeCookieConsent(value);
      setVisible(false);
      // Broadcast a simple event so the analytics init (whenever it
      // mounts) can re-read consent without polling.
      if (typeof window !== "undefined") {
        try {
          window.dispatchEvent(
            new CustomEvent("inspira:cookie-consent", {
              detail: { value },
            }),
          );
        } catch {
          /* CustomEvent unsupported — noop */
        }
      }
    },
    [],
  );

  const handleKey = useCallback(
    (e: React.KeyboardEvent<HTMLDivElement>) => {
      if (e.key === "Escape") {
        e.preventDefault();
        dismiss("essential");
        return;
      }
      if (e.key !== "Tab") return;
      const first = acceptRef.current;
      const last = essentialRef.current;
      if (!first || !last) return;
      // Two-button focus trap — Tab cycles forward, Shift+Tab cycles back.
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    },
    [dismiss],
  );

  if (!visible) return null;

  return (
    <div
      ref={containerRef}
      role="region"
      aria-labelledby="inspira-cookie-banner-text"
      className="cookie-banner"
      style={wrapperStyle}
      onKeyDown={handleKey}
    >
      <div style={cardStyle}>
        <p id="inspira-cookie-banner-text" style={copyStyle}>
          {t("cookie_banner.body")}
        </p>
        <div style={rowStyle}>
          <button
            ref={acceptRef}
            type="button"
            onClick={() => dismiss("all")}
            style={primaryBtnStyle}
          >
            {t("cookie_banner.accept")}
          </button>
          <button
            ref={essentialRef}
            type="button"
            onClick={() => dismiss("essential")}
            style={secondaryBtnStyle}
          >
            {t("cookie_banner.essential_only")}
          </button>
        </div>
      </div>
    </div>
  );
}

// --------------------------------------------------------------------
// Inline styles. Kept local so the banner doesn't need a CSS import and
// can land anywhere in the tree without coupling to App.css order. All
// colour tokens fall back to the warm-light palette if the CSS custom
// props aren't inherited (e.g. the legal pages might mount the banner
// before App.css has loaded).
// --------------------------------------------------------------------

const wrapperStyle: React.CSSProperties = {
  position: "fixed",
  left: "50%",
  bottom: 24,
  transform: "translateX(-50%)",
  zIndex: 1000,
  width: "min(640px, calc(100vw - 32px))",
  pointerEvents: "auto",
};

const cardStyle: React.CSSProperties = {
  background: "var(--paper, #F5F0E6)",
  border: "1px solid var(--paper-edge, #DBCFB6)",
  borderRadius: 14,
  padding: "16px 20px",
  boxShadow: "0 24px 48px -20px rgba(43, 37, 32, 0.35)",
  display: "flex",
  flexDirection: "column",
  gap: 14,
  fontFamily: "var(--ff-sans, system-ui, sans-serif)",
  color: "var(--ink, #2B2520)",
};

const copyStyle: React.CSSProperties = {
  margin: 0,
  fontFamily: "var(--ff-serif, Georgia, serif)",
  fontStyle: "italic",
  fontSize: 15,
  lineHeight: 1.5,
  color: "var(--ink-2, #4A413A)",
};

const rowStyle: React.CSSProperties = {
  display: "flex",
  flexWrap: "wrap",
  gap: 10,
  justifyContent: "flex-end",
};

const baseBtnStyle: React.CSSProperties = {
  fontFamily: "var(--ff-sans, system-ui, sans-serif)",
  fontSize: 14,
  fontWeight: 500,
  padding: "8px 16px",
  borderRadius: 999,
  cursor: "pointer",
  border: "1px solid transparent",
  transition: "background 0.15s ease, border-color 0.15s ease",
};

const primaryBtnStyle: React.CSSProperties = {
  ...baseBtnStyle,
  background: "var(--sage, #568868)",
  color: "#FDFBF6",
  borderColor: "var(--sage, #568868)",
};

const secondaryBtnStyle: React.CSSProperties = {
  ...baseBtnStyle,
  background: "transparent",
  color: "var(--ink, #2B2520)",
  borderColor: "var(--paper-edge, #DBCFB6)",
};
