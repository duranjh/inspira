// ExampleBanner — a soft sage strip shown when the active project is an
// example seed. Rendered from InspiraApp.tsx (NOT from ProjectCanvas.tsx)
// using position:absolute at
// top:72px so it sits just below the top bar without touching the canvas
// component itself.
//
// Clicking "Start a fresh one" calls the `onStartFresh` prop, which maps
// to InspiraApp's `startNewProject` handler.

import { t } from "../../i18n";

export type ExampleBannerProps = {
  onStartFresh: () => void;
};

export function ExampleBanner({ onStartFresh }: ExampleBannerProps) {
  return (
    <div
      className="example-banner"
      role="status"
      aria-label={t("example_banner.title")}
      style={{
        position: "absolute",
        top: "72px",
        left: "50%",
        transform: "translateX(-50%)",
        zIndex: 20,
        pointerEvents: "auto",
        display: "flex",
        alignItems: "center",
        gap: "12px",
        padding: "8px 18px",
        borderRadius: "20px",
        // Sage-tinted paper mixed at ~12% so the banner reads in both
        // warm-light and warm-dark themes. --sage and --paper both
        // invert via the theme tokens, so no hardcoded fallback snap.
        background:
          "color-mix(in srgb, var(--sage, #6a9a7a) 12%, var(--paper, #f5f0e6))",
        border:
          "1px solid color-mix(in srgb, var(--sage, #6a9a7a) 35%, transparent)",
        boxShadow: "0 2px 8px rgba(43,37,32,0.08)",
        fontSize: "0.8125rem",
        color: "var(--ink-2, #4a413a)",
        whiteSpace: "nowrap",
      }}
    >
      <span aria-hidden="true" style={{ fontSize: "1rem" }}>🌱</span>
      <span>
        <strong style={{ fontWeight: 600 }}>
          {t("example_banner.title")}
        </strong>
        {" — "}
        {t("example_banner.body")}
      </span>
      <button
        type="button"
        onClick={onStartFresh}
        style={{
          marginLeft: "4px",
          padding: "4px 12px",
          borderRadius: "12px",
          border: "1px solid var(--sage, #6a9a7a)",
          background: "var(--sage, #6a9a7a)",
          // --paper inverts so the label stays legible on the sage pill
          // in both themes (cream in light, near-black in modern-light,
          // cream-dark in warm-dark).
          color: "var(--paper, #ffffff)",
          fontSize: "0.8125rem",
          fontWeight: 600,
          cursor: "pointer",
          lineHeight: 1.4,
          transition: "opacity 0.15s",
        }}
        onMouseEnter={(e) => {
          (e.currentTarget as HTMLButtonElement).style.opacity = "0.85";
        }}
        onMouseLeave={(e) => {
          (e.currentTarget as HTMLButtonElement).style.opacity = "1";
        }}
      >
        {t("example_banner.cta")}
      </button>
    </div>
  );
}
