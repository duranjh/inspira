// Decision summary types, mock fixture, and markdown serializer for the
// orchestrator output drawer (B2.5). Field names + nested shape under
// `summary_json` match α's `orchestrator_runs.summary_json` contract
// emitted on the SSE `decision_summary.ready` event. λ (Wave 2 SSE
// wiring) will assemble the outer DecisionSummary by joining
// summary_json with run metadata + per-theme labels + agent / chip /
// provenance traces from agent_runs + decision_provenance.

export type DecisionTone = "sage" | "gold" | "rust";
export type CtaState = "default" | "approved";

export type DecisionChip = {
  label: string;
  tone: DecisionTone;
  hasDot?: boolean;
};

export type SubAgent = {
  name: string;
  text: string;
};

// One theme = one cluster of feedback that the orchestrator routed to a
// sub-agent. Shape mirrors α's emit at services/.../orchestrator.py:
// `{theme_id, project_id, status, decisions_count, highlights[≤3]}`.
export type DecisionSummaryTheme = {
  theme_id: string;
  project_id: string | null;
  status: "completed" | "error";
  decisions_count: number;
  highlights: string[];
};

export type DecisionSummaryFailedTheme = {
  theme_id: string;
  error: string;
};

// Cross-theme conflict resolved by the orchestrator. α's emit shape.
export type DecisionSummaryConflict = {
  subject: string;
  resolution_text: string;
  decision_a_id: string;
  decision_b_id: string;
};

// α's orchestrator_runs.summary_json shape (snake_case to match wire).
export type DecisionSummaryJson = {
  themes: DecisionSummaryTheme[];
  failed_themes: DecisionSummaryFailedTheme[];
  conflicts: DecisionSummaryConflict[];
  headline: string;
};

// What the drawer consumes. λ will hand-assemble this from α's SSE
// stream + joins; the dev-only mock trigger emits this shape directly
// so the drawer requires zero changes when the SSE wiring lands.
export type DecisionSummary = {
  summary_json: DecisionSummaryJson;
  // Joined by λ from v2_projects.title via theme.project_id, keyed by
  // theme_id. Missing labels fall back to the theme_id at render time.
  theme_labels: Record<string, string>;
  // From orchestrator_runs row (set by λ).
  run_id: string;
  finishedAt: string;
  subAgentCount: number;
  // Computed by λ from agent_runs + decision_provenance + feedback_items
  // joins — not part of α's summary_json contract. Display-only.
  chips: DecisionChip[];
  provenance: [string, string, string];
  agents: SubAgent[];
};

// Sample IDs — UUID-ish but obviously fake so they can't be confused for
// real data. Acme is the only org reference (capability/voice rule).
const T1 = "theme-acme-reproduce";
const T2 = "theme-acme-root-cause";
const T3 = "theme-acme-fix-login";
const T4 = "theme-acme-test-matrix";
const T5 = "theme-acme-ship";

export const mockDecisionSummary: DecisionSummary = {
  summary_json: {
    headline:
      "A regression in iOS Safari 17.4 broke the login flow. 12 customer reports across Acme support and the App Store between Apr 26–30 confirm the trigger. Estimated revenue at risk: 8% of mobile MAU. The fix touches the OAuth redirect handler in the auth service.",
    themes: [
      {
        theme_id: T1,
        project_id: "proj-acme-reproduce",
        status: "completed",
        decisions_count: 3,
        highlights: [
          "Use iOS 17.4 in Safari for reproduction. Cold-cache + clean state.",
          "Capture HAR + console logs from the affected session.",
          "Tag regression to Apr 25 deploy via git bisect.",
        ],
      },
      {
        theme_id: T2,
        project_id: "proj-acme-root-cause",
        status: "completed",
        decisions_count: 2,
        highlights: [
          "SameSite=None cookie rejected by Safari ITP — confirmed via HAR.",
          "Service worker cache serving stale auth token as secondary factor.",
        ],
      },
      {
        theme_id: T3,
        project_id: "proj-acme-fix-login",
        status: "completed",
        decisions_count: 2,
        highlights: [
          "Migrate to partitioned cookies for Safari ITP compliance.",
          "Add fallback redirect via URL token for blocked cookies.",
        ],
      },
      {
        theme_id: T4,
        project_id: "proj-acme-test-matrix",
        status: "completed",
        decisions_count: 2,
        highlights: [
          "Run matrix: Safari 17.4, Chrome 124, Firefox 126 on mobile + desktop.",
          "Add BrowserStack automated regression for login flow.",
        ],
      },
      {
        theme_id: T5,
        project_id: "proj-acme-ship",
        status: "completed",
        decisions_count: 3,
        highlights: [
          "Feature-flag rollout: 10% → 50% → 100% over 3 days.",
          "Rollback plan: kill switch via flag if login failure rate spikes above 2%.",
          "Post-ship monitor for 48h before closing the incident.",
        ],
      },
    ],
    failed_themes: [],
    conflicts: [
      {
        subject: "Universal fix vs iOS-only fix",
        resolution_text: "Chose iOS-only to limit blast radius.",
        decision_a_id: "dec-acme-universal",
        decision_b_id: "dec-acme-ios-only",
      },
      {
        subject: "Hot patch vs scheduled release",
        resolution_text: "Chose hot patch given severity 5.",
        decision_a_id: "dec-acme-hot-patch",
        decision_b_id: "dec-acme-scheduled",
      },
      {
        subject: "Fix in OAuth client vs server",
        resolution_text: "Chose client (smaller surface area).",
        decision_a_id: "dec-acme-oauth-client",
        decision_b_id: "dec-acme-oauth-server",
      },
    ],
  },
  theme_labels: {
    [T1]: "Reproduce the bug",
    [T2]: "Identify root cause",
    [T3]: "Fix login flow",
    [T4]: "Test across browsers",
    [T5]: "Ship to production",
  },
  run_id: "run-acme-2026-05-03-mock",
  finishedAt: "2026-05-03T12:00:00.000Z",
  subAgentCount: 5,
  chips: [
    { label: "12 cited items", tone: "gold" },
    { label: "3 sources", tone: "sage" },
    { label: "severity 5", tone: "rust", hasDot: true },
    { label: "ROI 8.4/10", tone: "gold" },
  ],
  provenance: [
    "Source data: 12 feedback items across Linear (4), Acme support (5), and App Store reviews (3). Dedup confidence: 87%. ROI weighted by severity, recency, and customer revenue tier.",
    `Sub-agent reasoning: 5 sub-agents ran in parallel. The 'Identify root cause' sub-agent flagged a Safari 17.4 regression as the most likely trigger; the 'Fix login flow' sub-agent proposed a fallback to query-string-based redirect handling for iOS Safari only.`,
    "Conflicts resolved: 0 (sub-agents converged on a single fix path).",
  ],
  agents: [
    {
      name: "Sub-agent · Reproduce",
      text: "Focused on isolating the trigger. Confirmed Safari 17.4 + ITP as the key variable by cross-referencing 3 support tickets with the Apr 25 deploy manifest. Recommended cold-cache reproduction to rule out service worker interference.",
    },
    {
      name: "Sub-agent · Root cause",
      text: `Traced the failure to SameSite=None cookie handling in Safari 17.4's updated ITP policy. The auth service sets SameSite=None on the session cookie, which Safari now rejects in cross-site contexts. Secondary: service worker caches stale token.`,
    },
    {
      name: "Sub-agent · Fix",
      text: "Evaluated three options: (1) partitioned cookies (chosen — future-proof), (2) localStorage token fallback (rejected — XSS surface), (3) URL-token redirect (chosen as fallback). Chose client-side fix to limit blast radius.",
    },
    {
      name: "Sub-agent · Test",
      text: `Generated browser matrix from Acme's analytics data. Safari 17.4 is 34% of mobile traffic. Added BrowserStack automation to catch future Safari regressions in CI.`,
    },
    {
      name: "Sub-agent · Ship",
      text: `Recommended phased rollout given severity 5. Feature flag allows instant rollback. 48h monitoring window matches Acme's incident SLA.`,
    },
  ],
};

export function totalDecisionCount(summary: DecisionSummary): number {
  return summary.summary_json.themes.reduce(
    (sum, t) => sum + t.decisions_count,
    0,
  );
}

// Render a topic prefix for a theme. Falls back to the raw theme_id if
// λ's label join didn't carry one.
export function themeLabelFor(
  summary: DecisionSummary,
  theme: DecisionSummaryTheme,
): string {
  return summary.theme_labels[theme.theme_id] ?? theme.theme_id;
}

// Format finishedAt as a short relative string for the attribution row.
// Pure so tests can pin `now`.
export function relativeTimeFrom(finishedAtIso: string, now: Date): string {
  const finished = new Date(finishedAtIso).getTime();
  if (Number.isNaN(finished)) return "just now";
  const diffMs = Math.max(0, now.getTime() - finished);
  const sec = Math.floor(diffMs / 1000);
  if (sec < 60) return "just now";
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min} min ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr} hr ago`;
  const day = Math.floor(hr / 24);
  return `${day} day${day === 1 ? "" : "s"} ago`;
}

export function serializeDecisionSummaryToMarkdown(
  s: DecisionSummary,
): string {
  const total = totalDecisionCount(s);
  const lines: string[] = [];

  lines.push("# Inspira's summary");
  lines.push("");
  lines.push(
    `_Orchestrator finished ${s.finishedAt} · ${s.subAgentCount} sub-agents contributed._`,
  );
  lines.push("");

  lines.push("## What this addresses");
  lines.push("");
  lines.push(s.summary_json.headline);
  lines.push("");
  for (const chip of s.chips) {
    lines.push(`- ${chip.label}`);
  }
  lines.push("");

  lines.push(`## Decisions made (${total})`);
  lines.push("");
  for (const theme of s.summary_json.themes) {
    lines.push(`### ${themeLabelFor(s, theme)}`);
    lines.push("");
    for (const h of theme.highlights) {
      lines.push(`- ${h}`);
    }
    lines.push("");
  }
  if (s.summary_json.failed_themes.length > 0) {
    lines.push("### Failed themes");
    lines.push("");
    for (const f of s.summary_json.failed_themes) {
      lines.push(`- ${f.theme_id}: ${f.error}`);
    }
    lines.push("");
  }

  lines.push("## How Inspira reached these decisions");
  lines.push("");
  lines.push(s.provenance[0]);
  lines.push("");
  lines.push(s.provenance[1]);
  lines.push("");
  lines.push(s.provenance[2]);
  lines.push("");
  lines.push("### Sub-agent reasoning");
  lines.push("");
  for (const a of s.agents) {
    lines.push(`**${a.name}** — ${a.text}`);
    lines.push("");
  }

  lines.push("## Trade-offs Inspira considered");
  lines.push("");
  for (const c of s.summary_json.conflicts) {
    lines.push(`- ${c.subject}: ${c.resolution_text}`);
  }

  return lines.join("\n");
}
