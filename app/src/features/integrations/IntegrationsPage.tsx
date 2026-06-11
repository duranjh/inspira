// Inspira — Integrations page.
//
// Landing surface at /integrations that surfaces three connection paths:
//   1. "Bring your own API key" — deep-link to Account Settings BYOK.
//   2. "Personal access tokens"  — deep-link to Account Settings PAT.
//   3. "Connect from Claude or ChatGPT" — copy-paste setup instructions
//      plus the two canonical endpoints:
//        - mcp.tryinspira.com (MCP streamable HTTP) for Claude.ai
//        - api.tryinspira.com/openapi.json for ChatGPT Custom GPTs
//
// The page is intentionally un-chromed: it reuses the existing marketing
// layout (MarketingLayout) so the warm editorial palette carries over,
// and it never imports the in-app canvas or InspiraApp. A visitor who
// signs out, reloads, or hits it from an external link sees the same
// surface whether or not they're authenticated.
//
// No new backend routes are required — the integrations page just points
// at existing ones.

import { useCallback, useMemo, useState, type ReactElement } from "react";
import { Link } from "react-router-dom";

import { Head } from "../marketing/Head";
import { MarketingLayout } from "../marketing/MarketingLayout";
import { t } from "../../i18n";
import "../marketing/marketing.css";

type IntegrationCard = {
  id: string;
  title: string;
  description: string;
  ctaLabel: string;
  ctaHref: string;
  // Optional rich block rendered under the description — used by the
  // Claude/ChatGPT card to show its setup snippets.
  renderBody?: () => ReactElement;
};

const MCP_URL = "https://mcp.tryinspira.com";
const OPENAPI_URL = "https://api.tryinspira.com/openapi.json";

export function IntegrationsPage() {
  return (
    <MarketingLayout>
      <Head
        title={t("marketing.integrations.meta.title")}
        description={t("marketing.integrations.meta.description")}
        canonical="https://tryinspira.com/integrations"
        ogImage="/og/og-integrations.png"
      />
      <article className="marketing-article" aria-labelledby="integrations-title">
        <p className="marketing-article__eyebrow">Integrations</p>
        <h1 className="marketing-article__title" id="integrations-title">
          Drive Inspira from your Claude or ChatGPT subscription
        </h1>
        <p>
          Inspira is a thinking canvas, not a chat window. If you already pay
          for Claude Pro or ChatGPT Plus, you can keep the conversation where
          it lives — and let Inspira persist the canvas behind it. Pick the
          path that matches your subscription below.
        </p>

        <IntegrationCardStack />

        <h2>How it works</h2>
        <p>
          Your LLM (Claude or ChatGPT) is the planner. Inspira is the memory.
          The model asks, you answer, and every exchange writes a turn onto
          the right topic. Decisions get captured as decisions; new branches
          spawn new topics; the whole graph stays addressable from the model
          side. LLM cost stays on Anthropic or OpenAI's tab — we never bill
          for a token we didn't run.
        </p>

        <h2>Need help?</h2>
        <p>
          The{" "}
          <Link to="/integrations/chatgpt">ChatGPT Custom GPT guide</Link> and{" "}
          <Link to="/integrations/claude">Claude MCP guide</Link> walk through
          the whole flow, including the starter prompts we recommend.
        </p>
      </article>
    </MarketingLayout>
  );
}

function IntegrationCardStack() {
  const cards = useMemo<IntegrationCard[]>(
    () => [
      {
        id: "byok",
        title: "Bring your own API key",
        description:
          "Plug your own Anthropic or OpenAI key into Inspira's planner. " +
          "LLM calls run through your account; Inspira still charges credits " +
          "for non-LLM features. Good for power users who want tighter cost " +
          "control than our default tier gives them.",
        ctaLabel: "Open BYOK settings",
        ctaHref: "/?account=byok",
      },
      {
        id: "pat",
        title: "Personal access tokens",
        description:
          "Mint a token to authenticate API calls from third-party tools. " +
          "Tokens are long-lived, revocable, and scoped to your account. " +
          "Required for the Claude / ChatGPT path below.",
        ctaLabel: "Open API tokens",
        ctaHref: "/?account=api-tokens",
      },
      {
        id: "mcp",
        title: "Connect from Claude or ChatGPT",
        description:
          "Drive your canvas from inside your existing Claude Pro or " +
          "ChatGPT Plus subscription. Inspira exposes an MCP server for " +
          "Claude.ai and an OpenAPI schema for ChatGPT Custom GPTs. " +
          "Copy the endpoint that matches your client and paste it into " +
          "the integration wizard.",
        ctaLabel: "View setup steps",
        ctaHref: "#mcp-setup",
        renderBody: () => <ExternalClientSetup />,
      },
    ],
    [],
  );

  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "1fr",
        gap: 24,
        margin: "32px 0 40px",
      }}
    >
      {cards.map((card) => (
        <IntegrationCardView key={card.id} card={card} />
      ))}
    </div>
  );
}

function IntegrationCardView({ card }: { card: IntegrationCard }) {
  return (
    <section
      aria-labelledby={`integ-${card.id}-title`}
      style={{
        background: "var(--paper, #F5F0E6)",
        border: "1px solid var(--paper-edge, #E3D9C6)",
        borderRadius: 8,
        padding: 28,
      }}
    >
      <h3
        id={`integ-${card.id}-title`}
        style={{ margin: 0, fontWeight: 400, fontSize: 22 }}
      >
        {card.title}
      </h3>
      <p style={{ marginTop: 10, marginBottom: 18, lineHeight: 1.55 }}>
        {card.description}
      </p>
      {card.renderBody ? card.renderBody() : null}
      <div style={{ marginTop: 18 }}>
        <a
          href={card.ctaHref}
          style={{
            display: "inline-block",
            padding: "8px 16px",
            borderRadius: 999,
            border: "1px solid var(--paper-edge, #E3D9C6)",
            color: "var(--ink, #2B2520)",
            textDecoration: "none",
            fontFamily: "var(--ff-sans, system-ui, sans-serif)",
            fontSize: 13,
            letterSpacing: "0.05em",
          }}
        >
          {card.ctaLabel}
        </a>
      </div>
    </section>
  );
}

function ExternalClientSetup() {
  const [copiedKey, setCopiedKey] = useState<string | null>(null);

  const onCopy = useCallback(async (key: string, value: string) => {
    try {
      await navigator.clipboard.writeText(value);
      setCopiedKey(key);
      window.setTimeout(() => setCopiedKey(null), 2000);
    } catch {
      // clipboard unavailable (e.g. http) — no-op.
    }
  }, []);

  return (
    <div id="mcp-setup" style={{ display: "grid", gap: 18 }}>
      <CopyField
        label="Claude.ai — MCP server URL"
        value={MCP_URL}
        copied={copiedKey === "mcp"}
        onCopy={() => onCopy("mcp", MCP_URL)}
      />
      <CopyField
        label="ChatGPT Custom GPT — OpenAPI schema URL"
        value={OPENAPI_URL}
        copied={copiedKey === "openapi"}
        onCopy={() => onCopy("openapi", OPENAPI_URL)}
      />
      <p
        style={{
          margin: 0,
          fontSize: 13,
          color: "var(--ink-3, #7A6F64)",
          lineHeight: 1.55,
        }}
      >
        Both clients ask for an API key. Paste the Personal Access Token you
        minted in Account Settings and set the forwarding scheme to{" "}
        <code>Bearer</code>. The full step-by-step lives in the{" "}
        <Link to="/integrations/chatgpt">ChatGPT guide</Link> and the{" "}
        <Link to="/integrations/claude">Claude guide</Link>.
      </p>
    </div>
  );
}

function CopyField({
  label,
  value,
  copied,
  onCopy,
}: {
  label: string;
  value: string;
  copied: boolean;
  onCopy: () => void;
}) {
  return (
    <div>
      <div
        style={{
          fontSize: 11,
          letterSpacing: "0.2em",
          textTransform: "uppercase",
          color: "var(--ink-3, #7A6F64)",
          marginBottom: 6,
          fontFamily: "var(--ff-sans, system-ui, sans-serif)",
        }}
      >
        {label}
      </div>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          background: "var(--paper-deep, #F1E9D6)",
          border: "1px solid var(--paper-edge, #E3D9C6)",
          borderRadius: 6,
          padding: "10px 12px",
        }}
      >
        <code
          style={{
            flex: 1,
            fontSize: 14,
            fontFamily:
              "SFMono-Regular, Menlo, Consolas, monospace",
            color: "var(--ink, #2B2520)",
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {value}
        </code>
        <button
          type="button"
          onClick={onCopy}
          style={{
            flex: "none",
            padding: "4px 10px",
            fontSize: 12,
            border: "1px solid var(--paper-edge, #E3D9C6)",
            background: "var(--paper, #F5F0E6)",
            borderRadius: 4,
            cursor: "pointer",
            fontFamily: "var(--ff-sans, system-ui, sans-serif)",
          }}
        >
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
    </div>
  );
}
