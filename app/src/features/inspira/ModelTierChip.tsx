// Inspira — LLM model-tier chip.
//
// Small pill rendered in the TopicDetail composer row. Shows the tier
// the next turn will run under (label + credit-multiplier hint). Clicking
// opens a popover with the three tiers; rows unavailable on the user's
// plan render disabled with an "Upgrade" CTA inline.
//
// Behaviour:
//   * `value` is null = "use the persisted default" (caller leaves the
//     field unset on the API call so the backend resolves).
//   * Selecting a different tier is a per-turn override only — the
//     caller resets `value` back to null after the send.
//   * Selecting the CURRENT default clears the override (same as null).
//   * Clicking a disabled row opens the upgrade dialog instead of
//     changing the selection.
//
// Intentionally self-contained: inline styles only, no new CSS file.
// Follows the warm-editorial palette via CSS custom properties (paper
// edges, ink greys) already defined in App.css.

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactElement,
} from "react";

import { t } from "../../i18n";
import { HIDE_UPGRADE } from "../../lib/featureFlags";
import type {
  ModelTier,
  ModelTierCatalog,
  ModelTierInfo,
  UsageView,
} from "./api";

export type ModelTierChipProps = {
  catalog: ModelTierCatalog | null;
  // The explicit per-turn pick. null = "use catalog.current_default".
  value: ModelTier | null;
  onChange: (tier: ModelTier | null) => void;
  onRequestUpgrade: () => void;
  disabled?: boolean;
  // Optional. When provided, the menu shows a "Set {tier} as default"
  // action that persists the choice via the caller's
  // ``setPreferredModelTier`` handler — so the user doesn't have to
  // re-pick the tier before every turn. The caller is responsible for
  // updating ``catalog.current_default`` after a successful PATCH.
  onSetDefault?: (tier: ModelTier) => void;
  settingDefault?: boolean;
  // Optional. When provided, each tier row shows a "{percent}% used
  // this month" sub-line, in red at >=80%. Surfaces the #080 cap
  // counters directly in the picker so users see remaining headroom
  // before they pick a tier.
  usage?: UsageView | null;
};

// Format the multiplier the user sees. 1× / 3× / 5× — keep the "×"
// character (not "x") since the warm-editorial voice favours it.
function formatMultiplier(multiplier: number): string {
  const n = Number.isInteger(multiplier)
    ? multiplier.toString()
    : multiplier.toFixed(1);
  return `${n}\u00D7`;
}

function TierRow({
  info,
  selected,
  onSelect,
  onUpgrade,
  usagePercent,
}: {
  info: ModelTierInfo;
  selected: boolean;
  onSelect: () => void;
  onUpgrade: () => void;
  /** 0..1 — surfaced as "{N}% used this month" sub-line. Undefined when
   *  the user has no usage data for this tier (no row, or tier not in
   *  the user's plan). */
  usagePercent?: number;
}): ReactElement {
  const disabled = !info.available;
  const handleClick = () => {
    if (disabled) {
      // HIDE_UPGRADE: don't trigger the upgrade dialog from a tier-locked
      // chip. The chip stays disabled (visual feedback that the tier is
      // unavailable) but no upgrade flow can fire. Imported lazily at
      // module scope below to avoid a circular dep through this hot path.
      if (HIDE_UPGRADE) return;
      onUpgrade();
      return;
    }
    onSelect();
  };
  // Render the usage sub-line only when the user has any usage AND the
  // tier is enabled. Disabled rows already show an upgrade hint, so a
  // percentage there would clutter without helping (the user can't
  // pick this tier anyway).
  const showUsage =
    !disabled && usagePercent !== undefined && usagePercent > 0;
  const usageRedZone = showUsage && (usagePercent ?? 0) >= 0.8;
  const usagePct = showUsage
    ? Math.min(100, Math.round((usagePercent ?? 0) * 100))
    : 0;
  return (
    <button
      type="button"
      role="menuitemradio"
      aria-checked={selected}
      className="model-tier-chip__row"
      onClick={handleClick}
      style={{
        display: "flex",
        alignItems: "flex-start",
        gap: 12,
        width: "100%",
        padding: "10px 12px",
        border: "none",
        background: "transparent",
        textAlign: "left",
        cursor: "pointer",
        borderRadius: 8,
        color: disabled ? "var(--ink-3)" : "var(--ink)",
        opacity: disabled ? 0.7 : 1,
      }}
    >
      {/* Radio dot */}
      <span
        aria-hidden="true"
        style={{
          flex: "0 0 auto",
          width: 14,
          height: 14,
          marginTop: 3,
          borderRadius: "50%",
          border: "1.5px solid var(--ink-4, #c9c2b1)",
          background: selected ? "var(--ink, #2a241c)" : "transparent",
          boxShadow: selected
            ? "inset 0 0 0 2px var(--paper-1, #fff)"
            : "none",
          transition: "background 120ms ease",
        }}
      />
      <span style={{ flex: 1, minWidth: 0 }}>
        <span
          style={{
            display: "flex",
            alignItems: "baseline",
            gap: 8,
            justifyContent: "space-between",
          }}
        >
          <span
            style={{
              fontFamily: "var(--ff-serif)",
              fontSize: 14,
              fontWeight: 500,
              letterSpacing: "0.01em",
            }}
          >
            {info.label}
          </span>
          {/* T3.1: credits suffix removed (no credit ledger after PR 2). */}
        </span>
        <span
          style={{
            display: "block",
            marginTop: 2,
            fontFamily: "var(--ff-sans)",
            fontSize: 12,
            color: "var(--ink-3)",
            lineHeight: 1.4,
          }}
        >
          {info.description}
        </span>
        {disabled && !HIDE_UPGRADE ? (
          <span
            style={{
              display: "inline-block",
              marginTop: 6,
              fontFamily: "var(--ff-sans)",
              fontSize: 11,
              color: "var(--rust, #a95a2f)",
              letterSpacing: "0.03em",
            }}
          >
            {t("model_tier.upgrade_cta")}
            <span aria-hidden="true"> →</span>
          </span>
        ) : null}
        {showUsage ? (
          <span
            style={{
              display: "inline-block",
              marginTop: 6,
              fontFamily: "var(--ff-sans)",
              fontSize: 11,
              color: usageRedZone ? "var(--rust, #a95a2f)" : "var(--ink-3)",
              letterSpacing: "0.03em",
            }}
          >
            {t("usage.percent_used", { percent: usagePct })}
          </span>
        ) : null}
      </span>
    </button>
  );
}

export function ModelTierChip({
  catalog,
  value,
  onChange,
  onRequestUpgrade,
  disabled = false,
  onSetDefault,
  settingDefault = false,
  usage = null,
}: ModelTierChipProps): ReactElement | null {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);

  // Effective selected tier for display in the chip — the explicit
  // override, or the current default from the catalog.
  const effective: ModelTier | null = useMemo(() => {
    if (value !== null) return value;
    return catalog?.current_default ?? null;
  }, [value, catalog]);

  const effectiveInfo: ModelTierInfo | null = useMemo(() => {
    if (!catalog || !effective) return null;
    return catalog.tiers.find((t) => t.slug === effective) ?? null;
  }, [catalog, effective]);

  // Map tier slug → usage percent (0..1) for fast lookup in the row
  // render. Undefined entries mean "no row in /usage's tiers array",
  // which TierRow renders as no usage line at all.
  const usageByTier = useMemo<Partial<Record<ModelTier, number>>>(() => {
    const map: Partial<Record<ModelTier, number>> = {};
    if (!usage) return map;
    for (const row of usage.tiers) {
      map[row.tier] = row.percent;
    }
    return map;
  }, [usage]);

  // Close on outside click / Esc.
  useEffect(() => {
    if (!open) return;
    const onDocClick = (e: MouseEvent) => {
      const root = rootRef.current;
      if (root && !root.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDocClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDocClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const handleRowSelect = useCallback(
    (tier: ModelTier) => {
      // Selecting the current default clears the per-turn override.
      const asOverride =
        catalog && tier === catalog.current_default ? null : tier;
      onChange(asOverride);
      setOpen(false);
    },
    [catalog, onChange],
  );

  if (!catalog || !effectiveInfo) {
    // Render nothing until the catalog loads — the chip is a progressive
    // enhancement; its absence shouldn't block sending a turn.
    return null;
  }

  return (
    <div
      ref={rootRef}
      className="model-tier-chip__root"
      style={{ position: "relative" }}
    >
      <button
        type="button"
        className="model-tier-chip__trigger"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={t("model_tier.chip_aria", {
          label: effectiveInfo.label,
        })}
        disabled={disabled}
        onClick={() => setOpen((v) => !v)}
        style={{
          height: 28,
          padding: "0 12px",
          borderRadius: 999,
          border: "1px solid var(--paper-edge, #e4ddcb)",
          background: "transparent",
          color: "var(--ink-2, #423a2d)",
          fontFamily: "var(--ff-sans)",
          fontSize: 12,
          letterSpacing: "0.02em",
          cursor: disabled ? "not-allowed" : "pointer",
          display: "inline-flex",
          alignItems: "center",
          gap: 6,
          whiteSpace: "nowrap",
          transition:
            "background 120ms ease, color 120ms ease, border-color 120ms ease",
          opacity: disabled ? 0.5 : 1,
        }}
      >
        <span
          aria-hidden="true"
          style={{
            width: 6,
            height: 6,
            borderRadius: "50%",
            background: "var(--ink-3, #7a6f5e)",
          }}
        />
        <span>{effectiveInfo.label}</span>
        {/* Multiplier + caret hint — hidden on narrow screens so the chip
            doesn't push the send button off the composer pill. See the
            @media (max-width: 520px) rule on .model-tier-chip__trigger. */}
        {/* T3.1: removed the (middle-dot) Nx credits suffix on the chip since PR 2 killed the credits ledger. */}
        <span
          aria-hidden="true"
          className="model-tier-chip__caret"
          style={{
            marginLeft: 2,
            fontSize: 10,
            color: "var(--ink-3, #7a6f5e)",
          }}
        >
          {open ? "\u25B4" : "\u25BE"}
        </span>
      </button>

      {open ? (
        <div
          role="menu"
          aria-label={t("model_tier.menu_aria")}
          className="model-tier-chip__menu"
          style={{
            position: "absolute",
            bottom: "calc(100% + 8px)",
            right: 0,
            minWidth: 280,
            maxWidth: 340,
            // Theme-aware surface: --paper-lifted swaps automatically
            // between cream (light) and espresso (dark). Previously hard-
            // coded to --paper-1 which is only defined for light mode,
            // so the popup rendered as a glaring cream rectangle on the
            // dark Bookworm theme. Keeping --paper-lifted in sync with
            // other lifted cards (kickoff textarea, topic-detail header).
            background: "var(--paper-lifted, var(--paper-2, #fcf8ee))",
            border: "1px solid var(--paper-edge, #e4ddcb)",
            borderRadius: 10,
            boxShadow: "0 8px 24px rgba(0, 0, 0, 0.35)",
            padding: 6,
            zIndex: 40,
            color: "var(--ink)",
          }}
        >
          <div
            style={{
              padding: "8px 12px 4px",
              fontFamily: "var(--ff-sans)",
              fontSize: 11,
              textTransform: "uppercase",
              letterSpacing: "0.08em",
              color: "var(--ink-3, #7a6f5e)",
            }}
          >
            {t("model_tier.menu_heading")}
          </div>
          {catalog.tiers.map((info) => (
            <TierRow
              key={info.slug}
              info={info}
              selected={info.slug === effective}
              onSelect={() => handleRowSelect(info.slug)}
              onUpgrade={onRequestUpgrade}
              usagePercent={usageByTier[info.slug]}
            />
          ))}
          {/* Thin separator before the "set as default" action so
              it reads as a distinct follow-up, not another tier row. */}
          <div
            aria-hidden="true"
            style={{
              margin: "4px 10px",
              height: 1,
              background: "var(--paper-edge, #e4ddcb)",
              opacity: 0.6,
            }}
          />
          {/* "Set {tier} as default" button — addresses the 2026-04-23
              user complaint that the picker only applied per-turn and
              forced a re-pick before every send. Only enabled when the
              currently-selected tier differs from the persisted default
              AND that tier is actually available on the user's plan. */}
          {onSetDefault && effective ? (
            <button
              type="button"
              disabled={
                effective === catalog.current_default
                || !effectiveInfo.available
                || settingDefault
              }
              onClick={() => {
                if (effective && effective !== catalog.current_default) {
                  onSetDefault(effective);
                }
              }}
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                width: "100%",
                padding: "8px 12px",
                margin: "2px 0",
                border: "none",
                background: "transparent",
                fontFamily: "var(--ff-sans)",
                fontSize: 12,
                textAlign: "left",
                color: "var(--ink-2, var(--ink))",
                cursor:
                  effective === catalog.current_default
                  || !effectiveInfo.available
                  || settingDefault
                    ? "default"
                    : "pointer",
                opacity:
                  effective === catalog.current_default
                  || !effectiveInfo.available
                  || settingDefault
                    ? 0.5
                    : 1,
                borderRadius: 6,
              }}
            >
              <span>
                {effective === catalog.current_default
                  ? t("model_tier.is_default", {
                      label: effectiveInfo.label,
                    })
                  : t("model_tier.set_as_default", {
                      label: effectiveInfo.label,
                    })}
              </span>
              {settingDefault ? (
                <span
                  aria-hidden="true"
                  style={{
                    fontSize: 10,
                    color: "var(--ink-3)",
                  }}
                >
                  …
                </span>
              ) : null}
            </button>
          ) : null}
          <div
            style={{
              padding: "6px 12px 8px",
              fontFamily: "var(--ff-sans)",
              fontSize: 11,
              color: "var(--ink-3, #7a6f5e)",
              lineHeight: 1.4,
            }}
          >
            {t("model_tier.menu_footer")}
          </div>
        </div>
      ) : null}
    </div>
  );
}
