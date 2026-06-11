// Account > Bring Your Own Key (BYOK).
//
// Technical users paste their own OpenAI / Anthropic key here. From that
// point on, planner turns that target the matching provider bill the
// user's account directly (Inspira credits stay untouched) and the
// composer shows a "Your key" badge so they know where the cost is going.
//
// Shape follows ModelSection: warm-editorial heading + body, inline
// styles drawing on the account palette tokens already in account.css.
// Nothing persists locally — the server is the only source of truth.
//
// The raw key NEVER round-trips through this component after a save. The
// input is cleared on success; the configured state is rendered as a
// "Verified on …" pill. To rotate, the user removes the key and pastes a
// new one.

import {
  useCallback,
  useEffect,
  useState,
  type ReactElement,
} from "react";

import { api, type ByokProvider, type ByokStatus } from "../../inspira/api";
import { toast } from "../../../components/ToastProvider";
import { t } from "../../../i18n";

type ProviderChoice = ByokProvider;

type SaveStatus =
  | { kind: "idle" }
  | { kind: "verifying" }
  | { kind: "error"; message: string };

// Short descriptor shown under the status pill for each provider so the
// user sees at a glance which key goes where. Kept inline because the
// strings are tiny and flipping the locale is done via `t(...)`.
function providerLabel(provider: ProviderChoice): string {
  return provider === "openai"
    ? t("byok.provider.openai_label")
    : t("byok.provider.anthropic_label");
}

function providerPlaceholder(provider: ProviderChoice): string {
  return provider === "openai" ? "sk-proj-..." : "sk-ant-...";
}

function formatVerifiedAt(iso: string | null): string {
  if (!iso) return "";
  try {
    const d = new Date(iso);
    return d.toLocaleDateString(undefined, {
      year: "numeric",
      month: "short",
      day: "numeric",
    });
  } catch {
    return iso;
  }
}

export function ByokSection(): ReactElement {
  const [status, setStatus] = useState<ByokStatus | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [provider, setProvider] = useState<ProviderChoice>("openai");
  const [keyInput, setKeyInput] = useState("");
  const [saveState, setSaveState] = useState<SaveStatus>({ kind: "idle" });

  const refresh = useCallback(async () => {
    try {
      const next = await api.getByokStatus();
      setStatus(next);
      setLoadError(null);
    } catch (err) {
      console.error("[Inspira] BYOK status load failed", err);
      setLoadError(t("byok.load_failed"));
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const handleSave = useCallback(
    async (ev: React.FormEvent) => {
      ev.preventDefault();
      const key = keyInput.trim();
      if (!key) return;
      setSaveState({ kind: "verifying" });
      try {
        await api.saveByokKey(provider, key);
        setKeyInput("");
        setSaveState({ kind: "idle" });
        toast.success(t("byok.save_success"));
        await refresh();
      } catch (err) {
        console.error("[Inspira] BYOK save failed", err);
        const rawMessage = err instanceof Error ? err.message : String(err);
        // The backend returns a structured 400 with
        // `key_verification_failed` when the provider itself rejects the
        // key. Surface the clearer copy in that case.
        const friendly = rawMessage.includes("key_verification_failed")
          ? t("byok.save_rejected")
          : t("byok.save_failed");
        setSaveState({ kind: "error", message: friendly });
        toast.error(friendly);
      }
    },
    [keyInput, provider, refresh],
  );

  const handleRemove = useCallback(
    async (target: ProviderChoice) => {
      try {
        await api.removeByokKey(target);
        toast.success(t("byok.remove_success"));
        await refresh();
      } catch (err) {
        console.error("[Inspira] BYOK remove failed", err);
        toast.error(t("byok.remove_failed"));
      }
    },
    [refresh],
  );

  const configured = status?.[provider]?.configured ?? false;
  const verifiedAt = status?.[provider]?.last_verified_at ?? null;

  return (
    <section className="account-section" aria-labelledby="account-byok-heading">
      <h2 className="account-section__heading" id="account-byok-heading">
        <span
          style={{
            fontFamily: "var(--ff-sans)",
            fontSize: 11,
            letterSpacing: "0.08em",
            textTransform: "uppercase",
            color: "var(--ink-3, #7a6f5e)",
            marginRight: 10,
          }}
        >
          {t("byok.eyebrow")}
        </span>
        {t("byok.section_heading")}
      </h2>
      <div className="account-section__body">
        <p className="account-section__subtitle" style={{ marginBottom: 16 }}>
          {t("byok.section_subtitle")}
        </p>

        {loadError ? (
          <p
            role="alert"
            style={{
              fontFamily: "var(--ff-sans)",
              fontSize: 13,
              color: "var(--rust, #a95a2f)",
              marginBottom: 12,
            }}
          >
            {loadError}
          </p>
        ) : null}

        {/* Provider picker — two radio cards. */}
        <div
          role="radiogroup"
          aria-label={t("byok.provider_picker_aria")}
          style={{
            display: "flex",
            gap: 12,
            marginBottom: 18,
            flexWrap: "wrap",
          }}
        >
          {(["openai", "anthropic"] as ProviderChoice[]).map((p) => {
            const isSelected = provider === p;
            const entry = status?.[p];
            const pConfigured = entry?.configured ?? false;
            return (
              <label
                key={p}
                style={{
                  flex: "1 1 220px",
                  display: "flex",
                  alignItems: "flex-start",
                  gap: 10,
                  padding: "12px 14px",
                  border: `1px solid ${
                    isSelected
                      ? "var(--ink, #2a241c)"
                      : "var(--paper-edge, #e4ddcb)"
                  }`,
                  borderRadius: 6,
                  background: isSelected
                    ? "var(--paper-hi, #fbf4e4)"
                    : "transparent",
                  cursor: "pointer",
                }}
              >
                <input
                  type="radio"
                  name="byok-provider"
                  value={p}
                  checked={isSelected}
                  onChange={() => {
                    setProvider(p);
                    setKeyInput("");
                    setSaveState({ kind: "idle" });
                  }}
                  style={{
                    flex: "0 0 auto",
                    marginTop: 3,
                    accentColor: "var(--ink, #2a241c)",
                  }}
                />
                <span style={{ flex: 1, minWidth: 0 }}>
                  <span
                    style={{
                      display: "block",
                      fontFamily: "var(--ff-serif)",
                      fontSize: 15,
                      fontWeight: 500,
                    }}
                  >
                    {providerLabel(p)}
                  </span>
                  <span
                    style={{
                      display: "block",
                      marginTop: 2,
                      fontFamily: "var(--ff-sans)",
                      fontSize: 12,
                      color: pConfigured
                        ? "var(--sage, #5a7a4a)"
                        : "var(--ink-3, #7a6f5e)",
                    }}
                  >
                    {pConfigured
                      ? t("byok.verified_on", {
                          date: formatVerifiedAt(
                            entry?.last_verified_at ?? null,
                          ),
                        })
                      : t("byok.not_configured")}
                  </span>
                </span>
              </label>
            );
          })}
        </div>

        {/* Save form for the currently-selected provider. */}
        <form onSubmit={handleSave}>
          <label
            htmlFor={`byok-key-${provider}`}
            style={{
              display: "block",
              fontFamily: "var(--ff-sans)",
              fontSize: 12,
              color: "var(--ink-2, #423a2d)",
              marginBottom: 6,
              letterSpacing: "0.02em",
            }}
          >
            {t("byok.key_input_label", { provider: providerLabel(provider) })}
          </label>
          <input
            id={`byok-key-${provider}`}
            type="password"
            autoComplete="off"
            spellCheck={false}
            value={keyInput}
            onChange={(e) => {
              setKeyInput(e.target.value);
              if (saveState.kind === "error") {
                setSaveState({ kind: "idle" });
              }
            }}
            placeholder={providerPlaceholder(provider)}
            disabled={saveState.kind === "verifying"}
            style={{
              width: "100%",
              padding: "10px 12px",
              fontFamily: "var(--ff-mono, monospace)",
              fontSize: 13,
              border: "1px solid var(--paper-edge, #e4ddcb)",
              borderRadius: 4,
              background: "var(--paper, #f7efd8)",
              color: "var(--ink, #2a241c)",
            }}
          />
          {saveState.kind === "error" ? (
            <p
              role="alert"
              style={{
                marginTop: 6,
                fontFamily: "var(--ff-sans)",
                fontSize: 12,
                color: "var(--rust, #a95a2f)",
              }}
            >
              {saveState.message}
            </p>
          ) : null}

          <div
            style={{
              display: "flex",
              gap: 10,
              marginTop: 14,
              alignItems: "center",
              flexWrap: "wrap",
            }}
          >
            <button
              type="submit"
              className="account-btn"
              disabled={!keyInput.trim() || saveState.kind === "verifying"}
            >
              {saveState.kind === "verifying"
                ? t("byok.verifying")
                : t("byok.save_and_verify")}
            </button>
            {configured ? (
              <button
                type="button"
                className="account-btn account-btn--ghost"
                onClick={() => void handleRemove(provider)}
                disabled={saveState.kind === "verifying"}
              >
                {t("byok.remove_key", { provider: providerLabel(provider) })}
              </button>
            ) : null}
          </div>
        </form>

        <p
          style={{
            marginTop: 20,
            fontFamily: "var(--ff-sans)",
            fontSize: 12,
            color: "var(--ink-3, #7a6f5e)",
            lineHeight: 1.55,
          }}
        >
          {t("byok.footer_disclaimer")}
        </p>
      </div>
    </section>
  );
}
