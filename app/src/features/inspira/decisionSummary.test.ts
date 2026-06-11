import { describe, expect, it } from "vitest";

import {
  mockDecisionSummary,
  relativeTimeFrom,
  serializeDecisionSummaryToMarkdown,
  totalDecisionCount,
} from "./decisionSummary";

describe("mockDecisionSummary", () => {
  it("has exactly 12 decisions across 5 themes", () => {
    expect(totalDecisionCount(mockDecisionSummary)).toBe(12);
    expect(mockDecisionSummary.summary_json.themes).toHaveLength(5);
  });

  it("every theme has a label in theme_labels (λ-supplied join)", () => {
    for (const theme of mockDecisionSummary.summary_json.themes) {
      expect(mockDecisionSummary.theme_labels[theme.theme_id]).toBeTruthy();
    }
  });

  it("highlights stay within α's 3-per-theme cap", () => {
    for (const theme of mockDecisionSummary.summary_json.themes) {
      expect(theme.highlights.length).toBeLessThanOrEqual(3);
      expect(theme.highlights.length).toBeLessThanOrEqual(theme.decisions_count);
    }
  });

  it("has exactly 5 sub-agents matching subAgentCount", () => {
    expect(mockDecisionSummary.agents).toHaveLength(5);
    expect(mockDecisionSummary.subAgentCount).toBe(5);
  });

  it("has exactly 3 provenance paragraphs and 3 conflicts", () => {
    expect(mockDecisionSummary.provenance).toHaveLength(3);
    expect(mockDecisionSummary.summary_json.conflicts).toHaveLength(3);
  });

  it("uses Acme as the only org reference (capability/voice sweep)", () => {
    const blob = JSON.stringify(mockDecisionSummary);
    const banned = ["Snapchat", "Meta", "Facebook", "Google", "Apple Inc"];
    for (const term of banned) {
      expect(blob).not.toContain(term);
    }
    expect(blob).toContain("Acme");
  });

  it("chip tones are sage / gold / rust only", () => {
    for (const chip of mockDecisionSummary.chips) {
      expect(["sage", "gold", "rust"]).toContain(chip.tone);
    }
  });
});

describe("relativeTimeFrom", () => {
  const finishedAt = "2026-05-03T12:00:00.000Z";

  it("renders 'just now' under a minute", () => {
    expect(
      relativeTimeFrom(finishedAt, new Date("2026-05-03T12:00:30.000Z")),
    ).toBe("just now");
  });

  it("renders 'N min ago' for minute-scale gaps", () => {
    expect(
      relativeTimeFrom(finishedAt, new Date("2026-05-03T12:04:00.000Z")),
    ).toBe("4 min ago");
  });

  it("renders 'N hr ago' for hour-scale gaps", () => {
    expect(
      relativeTimeFrom(finishedAt, new Date("2026-05-03T15:30:00.000Z")),
    ).toBe("3 hr ago");
  });

  it("renders 'N day(s) ago' for day-scale gaps with singular/plural", () => {
    expect(
      relativeTimeFrom(finishedAt, new Date("2026-05-04T12:00:00.000Z")),
    ).toBe("1 day ago");
    expect(
      relativeTimeFrom(finishedAt, new Date("2026-05-06T12:00:00.000Z")),
    ).toBe("3 days ago");
  });

  it("clamps negative gaps to 'just now'", () => {
    expect(
      relativeTimeFrom(finishedAt, new Date("2026-05-03T11:00:00.000Z")),
    ).toBe("just now");
  });
});

describe("serializeDecisionSummaryToMarkdown", () => {
  it("includes the H1 title and attribution row", () => {
    const md = serializeDecisionSummaryToMarkdown(mockDecisionSummary);
    expect(md).toMatch(/^# Inspira's summary/);
    expect(md).toContain("Orchestrator finished");
    expect(md).toContain("5 sub-agents contributed");
  });

  it("includes all 5 section H2 headings", () => {
    const md = serializeDecisionSummaryToMarkdown(mockDecisionSummary);
    expect(md).toContain("## What this addresses");
    expect(md).toContain("## Decisions made (12)");
    expect(md).toContain("## How Inspira reached these decisions");
    expect(md).toContain("## Trade-offs Inspira considered");
  });

  it("groups highlights under H3 theme-label headings", () => {
    const md = serializeDecisionSummaryToMarkdown(mockDecisionSummary);
    for (const theme of mockDecisionSummary.summary_json.themes) {
      const label = mockDecisionSummary.theme_labels[theme.theme_id];
      expect(md).toContain(`### ${label}`);
      for (const h of theme.highlights) {
        expect(md).toContain(`- ${h}`);
      }
    }
  });

  it("includes all chips, agents, provenance, and conflicts", () => {
    const md = serializeDecisionSummaryToMarkdown(mockDecisionSummary);
    for (const chip of mockDecisionSummary.chips) {
      expect(md).toContain(`- ${chip.label}`);
    }
    for (const a of mockDecisionSummary.agents) {
      expect(md).toContain(`**${a.name}** — ${a.text}`);
    }
    for (const p of mockDecisionSummary.provenance) {
      expect(md).toContain(p);
    }
    for (const c of mockDecisionSummary.summary_json.conflicts) {
      expect(md).toContain(`- ${c.subject}: ${c.resolution_text}`);
    }
  });
});
