// Account > AI model section.
//
// Three radio rows for the tier set the user's plan unlocks; a fourth
// "Use plan default" row lets the user clear the override. A single
// "Save" button persists the pick via api.setPreferredModelTier.
//
// Shape mirrors ThemeSection: section heading + body, with inline styles
// for the tier-specific ornament because the other sections don't use
// radio cards with a description. Tokens (--ink, --paper-edge, etc.)
// come from App.css so dark mode inherits automatically.

import { useCallback, useEffect, useState, type ReactElement } from "react";

import {
  api,
  type ModelTier,
  type ModelTierCatalog,
  type ModelTierInfo,
} from "../../inspira/api";
import { toast } from "../../../components/ToastProvider";
import { t } from "../../../i18n";

type Status =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "saving" }
  | { kind: "saved" }
  | { kind: "error"; message: string };

// The select value "__default__" means "clear the persisted pref". Using
// a sentinel string (instead of empty-string) keeps the typeguards tidy
// in case a future tier slug is ever empty.
const DEFAULT_SENTINEL = "__default__";

type Selection = ModelTier | typeof DEFAULT_SENTINEL;

function formatMultiplier(multiplier: number): string {
  const n = Number.isInteger(multiplier)
    ? multiplier.toString()
    : multiplier.toFixed(1);
  return `${n}\u00D7`;
}

export function ModelSection(): ReactElement {
  const [catalog, setCatalog] = useState<ModelTierCatalog | null>(null);
  const [selection, setSelection] = useState<Selection>(DEFAULT_SENTINEL);
  const [status, setStatus] = useState<Status>({ kind: "loading" });

  const refresh = useCallback(async () => {
    try {
      const next = await api.listModelTiers();
      setCatalog(next);
      setSelection(next.persisted_default ?? DEFAULT_SENTINEL);
      setStatus({ kind: "idle" });
    } catch (err) {
      console.error("[Inspira] model tier list failed", err);
      setStatus({
        kind: "error",
        message: t("model_tier.load_failed"),
      });
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const handleSave = useCallback(async () => {
    if (!catalog) return;
    const desired: ModelTier | null =
      selection === DEFAULT_SENTINEL ? null : selection;
    setStatus({ kind: "saving" });
    try {
      await api.setPreferredModelTier(desired);
      setStatus({ kind: "saved" });
      toast.success(t("model_tier.save_success"));
      // Refresh so current_default / persisted_default stay consistent.
      await refresh();
      window.setTimeout(() => {
        setStatus((s) => (s.kind === "saved" ? { kind: "idle" } : s));
      }, 2400);
    } catch (err) {
      console.error("[Inspira] model tier save failed", err);
      setStatus({
        kind: "error",
        message: t("model_tier.save_failed"),
      });
    }
  }, [catalog, selection, refresh]);

  const renderRow = (info: ModelTierInfo) => {
    const selected = selection === info.slug;
    const disabled = !info.available;
    return (
      <label
        key={info.slug}
        className="account-model__row"
        style={{
          display: "flex",
          alignItems: "flex-start",
          gap: 12,
          padding: "12px 0",
          borderBottom: "1px dashed var(--paper-edge, #e4ddcb)",
          cursor: disabled ? "not-allowed" : "pointer",
          opacity: disabled ? 0.6 : 1,
        }}
      >
        <input
          type="radio"
          name="preferred-model-tier"
          value={info.slug}
          checked={selected}
          disabled={disabled}
          onChange={() => setSelection(info.slug)}
          style={{
            flex: "0 0 auto",
            marginTop: 4,
            accentColor: "var(--ink, #2a241c)",
          }}
        />
        <span style={{ flex: 1, minWidth: 0 }}>
          <span
            style={{
              display: "flex",
              alignItems: "baseline",
              gap: 10,
              justifyContent: "space-between",
            }}
          >
            <span
              style={{
                fontFamily: "var(--ff-serif)",
                fontSize: 16,
                fontWeight: 500,
              }}
            >
              {info.label}
            </span>
            <span
              style={{
                fontFamily: "var(--ff-sans)",
                fontSize: 12,
                color: "var(--ink-3, #7a6f5e)",
                whiteSpace: "nowrap",
              }}
            >
              {t("model_tier.multiplier_hint", {
                multiplier: formatMultiplier(info.credit_multiplier),
              })}
            </span>
          </span>
          <span
            style={{
              display: "block",
              marginTop: 4,
              fontFamily: "var(--ff-sans)",
              fontSize: 13,
              color: "var(--ink-2, #423a2d)",
              lineHeight: 1.5,
            }}
          >
            {info.description}
          </span>
          {disabled ? (
            <span
              style={{
                display: "inline-block",
                marginTop: 6,
                fontFamily: "var(--ff-sans)",
                fontSize: 12,
                color: "var(--rust, #a95a2f)",
                letterSpacing: "0.02em",
              }}
            >
              {t("model_tier.upgrade_cta")}
              <span aria-hidden="true"> →</span>
            </span>
          ) : null}
        </span>
      </label>
    );
  };

  return (
    <section
      className="account-section"
      aria-labelledby="account-model-heading"
    >
      <h2 className="account-section__heading" id="account-model-heading">
        {t("model_tier.section_heading")}
      </h2>
      <div className="account-section__body">
        <p
          className="account-section__subtitle"
          style={{ marginBottom: 12 }}
        >
          {t("model_tier.section_subtitle")}
        </p>

        {status.kind === "loading" ? (
          <p
            style={{
              fontFamily: "var(--ff-sans)",
              fontSize: 13,
              color: "var(--ink-3)",
            }}
          >
            {t("model_tier.loading")}
          </p>
        ) : null}

        {status.kind === "error" ? (
          <p
            role="alert"
            style={{
              fontFamily: "var(--ff-sans)",
              fontSize: 13,
              color: "var(--rust, #a95a2f)",
            }}
          >
            {status.message}
          </p>
        ) : null}

        {catalog ? (
          <div role="radiogroup" aria-labelledby="account-model-heading">
            {/* "Use plan default" row pinned at top so clearing is obvious. */}
            <label
              className="account-model__row"
              style={{
                display: "flex",
                alignItems: "flex-start",
                gap: 12,
                padding: "12px 0",
                borderBottom: "1px dashed var(--paper-edge, #e4ddcb)",
                cursor: "pointer",
              }}
            >
              <input
                type="radio"
                name="preferred-model-tier"
                value={DEFAULT_SENTINEL}
                checked={selection === DEFAULT_SENTINEL}
                onChange={() => setSelection(DEFAULT_SENTINEL)}
                style={{
                  flex: "0 0 auto",
                  marginTop: 4,
                  accentColor: "var(--ink, #2a241c)",
                }}
              />
              <span style={{ flex: 1, minWidth: 0 }}>
                <span
                  style={{
                    fontFamily: "var(--ff-serif)",
                    fontSize: 16,
                    fontWeight: 500,
                  }}
                >
                  {t("model_tier.use_plan_default")}
                </span>
                <span
                  style={{
                    display: "block",
                    marginTop: 4,
                    fontFamily: "var(--ff-sans)",
                    fontSize: 13,
                    color: "var(--ink-3, #7a6f5e)",
                    lineHeight: 1.5,
                  }}
                >
                  {t("model_tier.use_plan_default_hint", {
                    tier:
                      catalog.tiers.find(
                        (x) => x.slug === catalog.plan_default,
                      )?.label ?? catalog.plan_default,
                  })}
                </span>
              </span>
            </label>

            {catalog.tiers.map(renderRow)}
          </div>
        ) : null}

        <div
          style={{
            marginTop: 16,
            display: "flex",
            alignItems: "center",
            gap: 12,
          }}
        >
          <button
            type="button"
            className="account-btn"
            onClick={() => void handleSave()}
            disabled={status.kind === "saving" || catalog === null}
          >
            {status.kind === "saving"
              ? t("model_tier.saving")
              : t("model_tier.save")}
          </button>
          {status.kind === "saved" ? (
            <span
              style={{
                fontFamily: "var(--ff-sans)",
                fontSize: 12,
                color: "var(--sage, #5a7a4a)",
              }}
            >
              {t("model_tier.saved")}
            </span>
          ) : null}
        </div>
      </div>
    </section>
  );
}
