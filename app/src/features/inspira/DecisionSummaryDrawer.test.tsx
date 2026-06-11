// DecisionSummaryDrawer rendering + interaction tests. Pattern matches
// WorkspaceContext.test.tsx — react-dom/client + React 19 `act` (no
// testing-library dep on this codebase).

import React, { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DecisionSummaryDrawer } from "./DecisionSummaryDrawer";
import { mockDecisionSummary, serializeDecisionSummaryToMarkdown } from "./decisionSummary";

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => {
    root.unmount();
  });
  container.remove();
  vi.restoreAllMocks();
});

function renderDrawer(
  overrides: Partial<React.ComponentProps<typeof DecisionSummaryDrawer>> = {},
): {
  onClose: ReturnType<typeof vi.fn>;
  onGenerateArtifact: ReturnType<typeof vi.fn>;
  onSendBackForRevision: ReturnType<typeof vi.fn>;
  onRejectPlan: ReturnType<typeof vi.fn>;
} {
  const onClose = vi.fn();
  const onGenerateArtifact = vi.fn();
  const onSendBackForRevision = vi.fn();
  const onRejectPlan = vi.fn();
  act(() => {
    root.render(
      <DecisionSummaryDrawer
        summary={mockDecisionSummary}
        open
        projectId="test-project"
        onClose={onClose}
        onGenerateArtifact={onGenerateArtifact}
        onSendBackForRevision={onSendBackForRevision}
        onRejectPlan={onRejectPlan}
        {...overrides}
      />,
    );
  });
  return { onClose, onGenerateArtifact, onSendBackForRevision, onRejectPlan };
}

function $(selector: string): HTMLElement | null {
  return container.querySelector(selector) as HTMLElement | null;
}

function $$(selector: string): HTMLElement[] {
  return Array.from(container.querySelectorAll(selector)) as HTMLElement[];
}

describe("DecisionSummaryDrawer rendering", () => {
  it("renders all 5 section headings", () => {
    renderDrawer();
    const text = container.textContent ?? "";
    expect(text).toContain("What this addresses");
    expect(text).toContain("Decisions made (12)");
    expect(text).toContain("How Inspira reached these decisions");
    expect(text).toContain("Trade-offs Inspira considered");
    expect(text).toContain("Generate the artifact (code) →");
  });

  it("renders all 12 decisions across 5 topic groups", () => {
    renderDrawer();
    const decisions = $$(".decision-summary-decision__text");
    expect(decisions).toHaveLength(12);
    const topics = $$(".decision-summary-decision-group__topic");
    expect(topics).toHaveLength(5);
  });

  it("renders all 4 chips with the correct tone classes", () => {
    renderDrawer();
    const chips = $$(".decision-summary-chip");
    expect(chips).toHaveLength(4);
    const tones = chips.map((el) => el.className);
    expect(tones.filter((c) => c.includes("--gold"))).toHaveLength(2);
    expect(tones.filter((c) => c.includes("--sage"))).toHaveLength(1);
    expect(tones.filter((c) => c.includes("--rust"))).toHaveLength(1);
    // The rust chip carries a status dot.
    expect($$(".decision-summary-chip__dot")).toHaveLength(1);
  });

  it("renders 3 provenance paragraphs and 3 tradeoffs", () => {
    renderDrawer();
    const provenance = $$(".decision-summary-provenance p");
    expect(provenance).toHaveLength(3);
    const tradeoffs = $$(".decision-summary-tradeoff");
    expect(tradeoffs).toHaveLength(3);
  });

  it("renders nothing when open=false", () => {
    act(() => {
      root.render(
        <DecisionSummaryDrawer
          summary={mockDecisionSummary}
          open={false}
          projectId="p"
          onClose={() => undefined}
        />,
      );
    });
    expect($(".decision-summary-drawer")).toBeNull();
    expect($(".decision-summary-drawer__backdrop")).toBeNull();
  });
});

describe("DecisionSummaryDrawer dismiss paths", () => {
  it("backdrop click calls onClose", () => {
    const { onClose } = renderDrawer();
    const backdrop = $(".decision-summary-drawer__backdrop");
    expect(backdrop).not.toBeNull();
    act(() => {
      backdrop!.click();
    });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("'← Canvas' button calls onClose", () => {
    const { onClose } = renderDrawer();
    const back = $(".decision-summary-drawer__back");
    act(() => {
      back!.click();
    });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("Escape key calls onClose", () => {
    const { onClose } = renderDrawer();
    act(() => {
      document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    });
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});

describe("DecisionSummaryDrawer reasoning expander", () => {
  it("starts collapsed and expands on toggle", () => {
    renderDrawer();
    expect($(".decision-summary-reason")).toBeNull();
    const link = $(".decision-summary-card__link");
    act(() => {
      link!.click();
    });
    expect($(".decision-summary-reason")).not.toBeNull();
    expect($$(".decision-summary-reason__agent")).toHaveLength(5);
  });
});

describe("DecisionSummaryDrawer kebab menu", () => {
  it("Copy as markdown copies the serialized summary to the clipboard", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });
    renderDrawer();
    act(() => {
      $(".decision-summary-drawer__kebab")!.click();
    });
    const items = $$(".decision-summary-drawer__menu-item");
    const copyItem = items.find((el) => /Copy as markdown/.test(el.textContent ?? ""));
    expect(copyItem).toBeDefined();

    await act(async () => {
      copyItem!.click();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(writeText).toHaveBeenCalledTimes(1);
    expect(writeText).toHaveBeenCalledWith(
      serializeDecisionSummaryToMarkdown(mockDecisionSummary),
    );
  });

  it("Re-run summary is aria-disabled when no handler is provided", () => {
    renderDrawer({ onRerunSummary: undefined });
    act(() => {
      $(".decision-summary-drawer__kebab")!.click();
    });
    const items = $$(".decision-summary-drawer__menu-item");
    const rerun = items.find((el) => /Re-run summary/.test(el.textContent ?? ""));
    expect(rerun?.getAttribute("aria-disabled")).toBe("true");
  });

  it("clicking the disabled Re-run summary item is a no-op and keeps the menu open", () => {
    renderDrawer({ onRerunSummary: undefined });
    act(() => {
      $(".decision-summary-drawer__kebab")!.click();
    });
    const items = $$(".decision-summary-drawer__menu-item");
    const rerun = items.find((el) => /Re-run summary/.test(el.textContent ?? ""));
    act(() => {
      rerun!.click();
    });
    expect($(".decision-summary-drawer__menu")).not.toBeNull();
  });

  it("Print invokes window.print and closes the menu", () => {
    const printSpy = vi.spyOn(window, "print").mockImplementation(() => {
      /* no-op for tests */
    });
    renderDrawer();
    act(() => {
      $(".decision-summary-drawer__kebab")!.click();
    });
    const items = $$(".decision-summary-drawer__menu-item");
    const printItem = items.find((el) => /Print/.test(el.textContent ?? ""));
    act(() => {
      printItem!.click();
    });
    expect(printSpy).toHaveBeenCalledTimes(1);
    expect($(".decision-summary-drawer__menu")).toBeNull();
  });

  it("outside-mousedown closes the kebab menu", () => {
    renderDrawer();
    act(() => {
      $(".decision-summary-drawer__kebab")!.click();
    });
    expect($(".decision-summary-drawer__menu")).not.toBeNull();
    act(() => {
      document.dispatchEvent(
        new MouseEvent("mousedown", { bubbles: true }),
      );
    });
    expect($(".decision-summary-drawer__menu")).toBeNull();
  });

  it("copyState resets to idle 1.5s after a successful copy", async () => {
    vi.useFakeTimers();
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });
    renderDrawer();
    act(() => {
      $(".decision-summary-drawer__kebab")!.click();
    });
    const items = $$(".decision-summary-drawer__menu-item");
    const copyItem = items.find((el) => /Copy as markdown/.test(el.textContent ?? ""));
    await act(async () => {
      copyItem!.click();
      await Promise.resolve();
      await Promise.resolve();
    });
    // Re-open the menu — label should still read "Copied ✓".
    act(() => {
      $(".decision-summary-drawer__kebab")!.click();
    });
    const labelBefore = $$(".decision-summary-drawer__menu-item").find((el) =>
      /Copied|Copy as markdown/.test(el.textContent ?? ""),
    );
    expect(labelBefore?.textContent).toMatch(/Copied/);

    await act(async () => {
      vi.advanceTimersByTime(1600);
    });
    const labelAfter = $$(".decision-summary-drawer__menu-item").find((el) =>
      /Copied|Copy as markdown/.test(el.textContent ?? ""),
    );
    expect(labelAfter?.textContent).toMatch(/Copy as markdown/);
    vi.useRealTimers();
  });

  it("Re-run summary calls handler when provided", () => {
    const onRerunSummary = vi.fn();
    renderDrawer({ onRerunSummary });
    act(() => {
      $(".decision-summary-drawer__kebab")!.click();
    });
    const items = $$(".decision-summary-drawer__menu-item");
    const rerun = items.find((el) => /Re-run summary/.test(el.textContent ?? ""));
    act(() => {
      rerun!.click();
    });
    expect(onRerunSummary).toHaveBeenCalledTimes(1);
  });
});

describe("DecisionSummaryDrawer CTA state", () => {
  it("default state renders Generate / Send back / Reject CTAs", () => {
    const { onGenerateArtifact, onSendBackForRevision, onRejectPlan } =
      renderDrawer();
    const text = container.textContent ?? "";
    expect(text).toContain("Generate the artifact (code) →");
    expect(text).toContain("Send back to AI for revision");
    expect(text).toContain("Reject this plan");

    act(() => {
      $(".decision-summary-cta__btn")!.click();
    });
    expect(onGenerateArtifact).toHaveBeenCalledTimes(1);

    const ghosts = $$(".decision-summary-cta__link");
    act(() => {
      ghosts[0]!.click();
    });
    expect(onSendBackForRevision).toHaveBeenCalledTimes(1);
    act(() => {
      ghosts[1]!.click();
    });
    expect(onRejectPlan).toHaveBeenCalledTimes(1);
  });

  it("approved state renders 'Artifact ready · Open →' and dispatches inspira:open-artifact", () => {
    let received: unknown = null;
    const listener = (ev: Event) => {
      received = (ev as CustomEvent).detail;
    };
    window.addEventListener("inspira:open-artifact", listener);

    renderDrawer({ ctaState: "approved" });
    const text = container.textContent ?? "";
    expect(text).toContain("Artifact ready · Open →");
    expect(text).not.toContain("Send back to AI for revision");

    act(() => {
      $(".decision-summary-cta__btn--ready")!.click();
    });
    expect(received).toEqual({ projectId: "test-project" });

    expect($(".decision-summary-drawer")?.className).toContain(
      "decision-summary-drawer--dimmed",
    );

    window.removeEventListener("inspira:open-artifact", listener);
  });
});

describe("DecisionSummaryDrawer focus management", () => {
  it("focuses the drawer aside on open when no editable element has focus", () => {
    renderDrawer();
    const aside = $(".decision-summary-drawer");
    expect(document.activeElement).toBe(aside);
  });

  it("does not steal focus from an editable element", () => {
    const input = document.createElement("input");
    document.body.appendChild(input);
    input.focus();
    expect(document.activeElement).toBe(input);

    renderDrawer();
    expect(document.activeElement).toBe(input);

    input.remove();
  });
});

describe("DecisionSummaryDrawer attribution", () => {
  it("shows the relative time + sub-agent count", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-05-03T12:04:00.000Z"));
    renderDrawer();
    const attr = $(".decision-summary-drawer__attribution");
    expect(attr?.textContent).toContain("Orchestrator finished");
    expect(attr?.textContent).toContain("4 min ago");
    expect(attr?.textContent).toContain("5 sub-agents contributed");
    vi.useRealTimers();
  });
});
