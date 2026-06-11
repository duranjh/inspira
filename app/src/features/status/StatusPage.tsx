/**
 * Inspira — public status page (/status).
 *
 * No auth required. Polls GET /api/status every 30s and renders three
 * warm editorial cards (Database, OpenAI, Stripe) with a sage / gold /
 * rust status dot matching Inspira's palette rather than the generic
 * traffic-light green/yellow/red.
 *
 * The endpoint returns:
 *   {
 *     "status": "ok" | "degraded" | "down",
 *     "checks": { "db": "ok"|"fail", "openai": "ok"|"fail"|"unknown",
 *                 "stripe": "ok"|"fail"|"unknown",
 *                 "db_latency_ms"?: number },
 *     "version": "<git_sha or build id>",
 *     "generated_at": "<iso>"
 *   }
 *
 * The backend caches the response for 30s so we can poll comfortably.
 * Latency thresholds and overall rollup live server-side; the frontend
 * just paints the result.
 */

import { useEffect, useMemo, useRef, useState } from "react";

import { t } from "../../i18n";
import { Head } from "../marketing/Head";

type Overall = "ok" | "degraded" | "down" | "unknown";
type CheckState = "ok" | "fail" | "unknown";
type CardStatus = "ok" | "degraded" | "down" | "unknown";

interface StatusResponse {
  status: Overall;
  checks: {
    db: CheckState;
    openai: CheckState;
    stripe: CheckState;
    db_latency_ms?: number;
  };
  version: string;
  generated_at: string;
}

const POLL_INTERVAL_MS = 30_000;
const PROBE_TIMEOUT_MS = 12_000;

const API_BASE =
  (import.meta.env.VITE_INSPIRA_API_URL as string | undefined) ??
  "https://api.tryinspira.com";

function checkStateToCard(state: CheckState): CardStatus {
  switch (state) {
    case "ok":
      return "ok";
    case "fail":
      return "down";
    default:
      return "unknown";
  }
}

function formatClock(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function dotColor(status: CardStatus): string {
  switch (status) {
    case "ok":
      return "var(--sage, #568868)";
    case "degraded":
      return "var(--gold, #8D6A23)";
    case "down":
      return "var(--rust, #9A4E38)";
    default:
      return "var(--ink-4, #706055)";
  }
}

function statusLabel(status: CardStatus): string {
  switch (status) {
    case "ok":
      return t("status.status.ok");
    case "degraded":
      return t("status.status.degraded");
    case "down":
      return t("status.status.down");
    default:
      // "unknown" on a non-actively-probed check reads as "Not monitored"
      // rather than the ambiguous "Checking…" — we intentionally don't
      // probe some surfaces (e.g. the frontend from itself) and the user
      // deserves a single honest pill instead of two states that both
      // imply "transient".
      return t("status.status.not_monitored");
  }
}

interface StatusCardProps {
  title: string;
  subtitle: string;
  cardStatus: CardStatus;
  detail?: string;
}

function StatusCard({ title, subtitle, cardStatus, detail }: StatusCardProps) {
  const card: React.CSSProperties = {
    background: "var(--paper-2, #EEE6D6)",
    border: "1px solid var(--paper-edge, #DBCFB6)",
    borderRadius: 12,
    padding: "24px 28px",
    minHeight: 140,
    display: "flex",
    flexDirection: "column",
    gap: 10,
  };
  const header: React.CSSProperties = {
    display: "flex",
    alignItems: "center",
    gap: 12,
  };
  const dot: React.CSSProperties = {
    display: "inline-block",
    width: 12,
    height: 12,
    borderRadius: 999,
    background: dotColor(cardStatus),
    boxShadow: `0 0 0 3px color-mix(in srgb, ${dotColor(cardStatus)} 20%, transparent)`,
    flexShrink: 0,
  };
  const heading: React.CSSProperties = {
    fontFamily: "var(--ff-serif, Georgia, serif)",
    fontSize: 20,
    fontWeight: 500,
    color: "var(--ink, #2B2520)",
    margin: 0,
  };
  const sub: React.CSSProperties = {
    fontFamily: "var(--ff-serif, Georgia, serif)",
    fontStyle: "italic",
    fontSize: 13,
    color: "var(--ink-3, #6B5F54)",
    margin: 0,
  };
  const detailRow: React.CSSProperties = {
    fontFamily: "var(--ff-sans, system-ui, sans-serif)",
    fontSize: 13,
    color: "var(--ink-2, #4A413A)",
    display: "flex",
    justifyContent: "space-between",
    marginTop: "auto",
    paddingTop: 12,
    borderTop: "1px solid var(--paper-edge, #DBCFB6)",
  };

  return (
    <article style={card} aria-label={t("status.card.aria_label", { title })}>
      <div style={header}>
        <span style={dot} aria-hidden="true" />
        <h2 style={heading}>{title}</h2>
      </div>
      <p style={sub}>{subtitle}</p>
      <div style={detailRow}>
        <span>{statusLabel(cardStatus)}</span>
        <span>{detail ?? ""}</span>
      </div>
    </article>
  );
}

async function fetchStatus(): Promise<StatusResponse | null> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), PROBE_TIMEOUT_MS);
  try {
    const res = await fetch(`${API_BASE}/api/status`, {
      method: "GET",
      signal: controller.signal,
      credentials: "omit",
      cache: "no-store",
    });
    if (!res.ok) return null;
    const body = (await res.json()) as StatusResponse;
    return body;
  } catch {
    return null;
  } finally {
    window.clearTimeout(timer);
  }
}

export default function StatusPage() {
  const [data, setData] = useState<StatusResponse | null>(null);
  const [unreachable, setUnreachable] = useState(false);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  useEffect(() => {
    async function runOnce(): Promise<void> {
      const result = await fetchStatus();
      if (!mounted.current) return;
      if (result === null) {
        setUnreachable(true);
        return;
      }
      setUnreachable(false);
      setData(result);
    }

    void runOnce();
    const id = window.setInterval(runOnce, POLL_INTERVAL_MS);
    return () => window.clearInterval(id);
  }, []);

  const overall: Overall = useMemo(() => {
    if (unreachable && data === null) return "down";
    if (data === null) return "unknown";
    return data.status;
  }, [data, unreachable]);

  const overallHeadline = (() => {
    switch (overall) {
      case "ok":
        return t("status.headline.ok");
      case "degraded":
        return t("status.headline.degraded");
      case "down":
        return t("status.headline.down");
      default:
        return t("status.headline.checking");
    }
  })();

  const page: React.CSSProperties = {
    minHeight: "100dvh",
    background: "var(--paper, #F5F0E6)",
    color: "var(--ink, #2B2520)",
    padding: "64px 24px",
    fontFamily: "var(--ff-sans, system-ui, sans-serif)",
  };
  const container: React.CSSProperties = {
    maxWidth: 900,
    margin: "0 auto",
  };
  const eyebrow: React.CSSProperties = {
    fontFamily: "var(--ff-sans, system-ui, sans-serif)",
    fontSize: 12,
    fontWeight: 500,
    letterSpacing: "0.08em",
    textTransform: "uppercase",
    color: "var(--ink-3, #6B5F54)",
    margin: "0 0 12px",
  };
  const headline: React.CSSProperties = {
    fontFamily: "var(--ff-serif, Georgia, serif)",
    fontSize: 40,
    fontWeight: 400,
    color: "var(--ink, #2B2520)",
    margin: "0 0 12px",
    lineHeight: 1.1,
  };
  const subhead: React.CSSProperties = {
    fontFamily: "var(--ff-serif, Georgia, serif)",
    fontStyle: "italic",
    fontSize: 16,
    color: "var(--ink-3, #6B5F54)",
    margin: "0 0 40px",
  };
  const grid: React.CSSProperties = {
    display: "grid",
    gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))",
    gap: 16,
  };
  const footer: React.CSSProperties = {
    fontFamily: "var(--ff-serif, Georgia, serif)",
    fontStyle: "italic",
    fontSize: 13,
    color: "var(--ink-4, #706055)",
    margin: "40px 0 0",
    textAlign: "center",
  };

  const dbCheck = data?.checks.db ?? "unknown";
  const openaiCheck = data?.checks.openai ?? "unknown";
  const stripeCheck = data?.checks.stripe ?? "unknown";

  const dbCard = checkStateToCard(dbCheck);
  const openaiCard = checkStateToCard(openaiCheck);
  const stripeCard = checkStateToCard(stripeCheck);

  const dbDetail =
    data?.checks.db_latency_ms !== undefined
      ? `${data.checks.db_latency_ms}ms`
      : undefined;

  const lastCheckedDisplay = data?.generated_at
    ? t("status.last_checked", { time: formatClock(data.generated_at) })
    : t("status.last_checked_unknown");

  const versionLabel = data?.version
    ? t("status.version", { version: data.version })
    : null;

  return (
    <main style={page} role="main">
      <Head
        title={t("status.meta.title")}
        description={t("status.meta.description")}
        canonical="https://tryinspira.com/status"
        ogImage="/og/og-status.png"
        robots="noindex,nofollow"
      />
      <div style={container}>
        <p style={eyebrow}>{t("status.eyebrow")}</p>
        <h1 style={headline}>{overallHeadline}</h1>
        <p style={subhead}>{t("status.subhead")}</p>

        <div style={grid}>
          <StatusCard
            title={t("status.card.database.title")}
            subtitle={t("status.card.database.subtitle")}
            cardStatus={dbCard}
            detail={dbDetail}
          />
          <StatusCard
            title={t("status.card.openai.title")}
            subtitle={t("status.card.openai.subtitle")}
            cardStatus={openaiCard}
            // T5.8: dropped the "not actively probed" detail row when
            // the card is in "unknown" state. The card's status pill
            // already says "Not monitored" — surfacing the same idea
            // twice (pill + detail) read as a duplicate label. The
            // pill alone is cleaner.
          />
          <StatusCard
            title={t("status.card.stripe.title")}
            subtitle={t("status.card.stripe.subtitle")}
            cardStatus={stripeCard}
          />
        </div>

        <p style={footer}>
          {lastCheckedDisplay}
          {versionLabel ? <> · {versionLabel}</> : null}
        </p>
      </div>
    </main>
  );
}
