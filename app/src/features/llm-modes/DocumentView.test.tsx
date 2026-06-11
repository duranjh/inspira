// Tests for DocumentView (#094 part 4b + 4c).
//
// Coverage:
//   - Empty state: doc-type-aware Generate CTA + brief explainer.
//   - Generating state: skeleton with copy.
//   - Failed state: retry CTA + optional error_message.
//   - Completed state: sections render with title + prose + key_points
//     + cited_topics chips.
//   - Side-nav: one link per section, scroll-spy initial active id is
//     the first section.
//   - Cap pill: singular vs plural copy + "{used}/{cap}" interpolation.
//   - Empty cited_topics: chip row not rendered.
//   - Edit-on-click: section's Edit button opens textarea pre-filled.
//   - Save: fires onPatchSection with the changed fields.
//   - Cancel: closes editor without firing onPatchSection.
//
// Setup mirrors CanvasErrorBoundary.test.tsx — raw createRoot + act,
// no testing-library.

import React, { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DocumentView } from "./DocumentView";
import type {
  DocumentSectionPatchBody,
  DocumentView as DocumentViewData,
} from "../inspira/api";

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

let container: HTMLDivElement;
let root: Root;
let consoleWarnSpy: ReturnType<typeof vi.spyOn>;

// Stub IntersectionObserver — DocumentView's useScrollSpy reads it.
class StubIO {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
  takeRecords(): IntersectionObserverEntry[] {
    return [];
  }
  root = null;
  rootMargin = "";
  thresholds = [0];
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  consoleWarnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
  // @ts-expect-error stub global IO
  globalThis.IntersectionObserver = StubIO;
});

afterEach(() => {
  act(() => {
    root.unmount();
  });
  container.remove();
  consoleWarnSpy.mockRestore();
});

// ---------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------

function makeCompletedDoc(): DocumentViewData {
  return {
    document_id: "doc-1",
    project_id: "proj-1",
    doc_type: "business_plan",
    status: "completed",
    content: {
      doc_type: "business_plan",
      sections: [
        {
          section_id: "executive_summary",
          title: "Executive summary",
          prose_markdown: "This is the **summary**.",
          key_points: ["Point A", "Point B"],
          cited_topics: ["Budget", "Audience"],
        },
        {
          section_id: "mission",
          title: "Mission",
          prose_markdown: "Build a thing.",
          key_points: [],
          cited_topics: [],
        },
      ],
    },
    error_message: null,
    model_id: "gpt-5.5",
    plan_tier: "pro",
    output_tokens_estimate: 100,
    generated_at: "2026-04-29T12:00:00Z",
    completed_at: "2026-04-29T12:01:00Z",
  };
}

function makeFailedDoc(): DocumentViewData {
  return {
    ...makeCompletedDoc(),
    status: "failed",
    content: null,
    error_message: "adapter_failed",
    completed_at: null,
  };
}

const noopGenerate = (): Promise<void> => Promise.resolve();

// ---------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------

describe("DocumentView render branches", () => {
  it("empty state: shows the doc-type-aware Generate CTA", () => {
    act(() => {
      root.render(
        <DocumentView
          projectId="proj-1"
          docType="business_plan"
          document={null}
          pending={false}
          stale={false}
          capUsed={0}
          capLimit={1}
          onGenerate={noopGenerate}
        />,
      );
    });
    const buttons = Array.from(
      container.querySelectorAll<HTMLButtonElement>("button"),
    );
    const generateBtn = buttons.find((b) =>
      (b.textContent ?? "").toLowerCase().includes("generate"),
    );
    expect(generateBtn).toBeTruthy();
    expect(container.textContent ?? "").toContain("business plan");
  });

  it("pending state: shows generating copy", () => {
    act(() => {
      root.render(
        <DocumentView
          projectId="proj-1"
          docType="prd"
          document={null}
          pending={true}
          stale={false}
          capUsed={0}
          capLimit={1}
          onGenerate={noopGenerate}
        />,
      );
    });
    expect(container.querySelector(".document-view__generating")).toBeTruthy();
  });

  it("failed state: shows retry CTA + error_message", () => {
    act(() => {
      root.render(
        <DocumentView
          projectId="proj-1"
          docType="business_plan"
          document={makeFailedDoc()}
          pending={false}
          stale={false}
          capUsed={0}
          capLimit={1}
          onGenerate={noopGenerate}
        />,
      );
    });
    expect(container.querySelector(".document-view__failed")).toBeTruthy();
    expect(container.textContent ?? "").toContain("adapter_failed");
  });

  it("completed: renders sections with title + prose + key_points + cited_chips", () => {
    act(() => {
      root.render(
        <DocumentView
          projectId="proj-1"
          docType="business_plan"
          document={makeCompletedDoc()}
          pending={false}
          stale={false}
          capUsed={0}
          capLimit={1}
          onGenerate={noopGenerate}
        />,
      );
    });
    const sections = container.querySelectorAll(".document-view__section");
    expect(sections.length).toBe(2);
    // First section title + prose strong tag from renderMarkdown.
    expect(container.textContent ?? "").toContain("Executive summary");
    expect(container.querySelector(".document-view__prose strong")).toBeTruthy();
    // Key points list rendered for first section only (second is empty).
    const keyPointsLists = container.querySelectorAll(
      ".document-view__key-points",
    );
    expect(keyPointsLists.length).toBe(1);
    // Cited chips also only on first section.
    const chipRows = container.querySelectorAll(".document-view__cited-chips");
    expect(chipRows.length).toBe(1);
    const chips = container.querySelectorAll(".document-view__cited-chip");
    expect(chips.length).toBe(2); // Budget + Audience
  });

  it("side-nav renders one link per section", () => {
    act(() => {
      root.render(
        <DocumentView
          projectId="proj-1"
          docType="business_plan"
          document={makeCompletedDoc()}
          pending={false}
          stale={false}
          capUsed={0}
          capLimit={1}
          onGenerate={noopGenerate}
        />,
      );
    });
    const links = container.querySelectorAll(".document-view__sidenav-link");
    expect(links.length).toBe(2);
    expect(links[0]?.getAttribute("href")).toBe("#executive_summary");
    expect(links[1]?.getAttribute("href")).toBe("#mission");
  });

  it("cap pill renders correct copy for Pro (singular)", () => {
    act(() => {
      root.render(
        <DocumentView
          projectId="proj-1"
          docType="business_plan"
          document={null}
          pending={false}
          stale={false}
          capUsed={0}
          capLimit={1}
          onGenerate={noopGenerate}
        />,
      );
    });
    const pill = container.querySelector(".document-view__cap-pill");
    expect(pill?.textContent ?? "").toMatch(/0\/1/);
    expect((pill?.textContent ?? "").toLowerCase()).not.toContain("unlimited");
  });

  it("cap pill renders correct copy for Frontier (plural, never 'unlimited')", () => {
    act(() => {
      root.render(
        <DocumentView
          projectId="proj-1"
          docType="business_plan"
          document={null}
          pending={false}
          stale={false}
          capUsed={12}
          capLimit={100}
          onGenerate={noopGenerate}
        />,
      );
    });
    const pill = container.querySelector(".document-view__cap-pill");
    expect(pill?.textContent ?? "").toMatch(/12\/100/);
    expect((pill?.textContent ?? "").toLowerCase()).not.toContain("unlimited");
  });

  it("stale banner: visible when stale=true on a completed doc", () => {
    act(() => {
      root.render(
        <DocumentView
          projectId="proj-1"
          docType="business_plan"
          document={makeCompletedDoc()}
          pending={false}
          stale={true}
          capUsed={0}
          capLimit={1}
          onGenerate={noopGenerate}
        />,
      );
    });
    expect(container.querySelector(".document-view__stale-banner")).toBeTruthy();
  });

  it("empty cited_topics: chip row not rendered for second section", () => {
    act(() => {
      root.render(
        <DocumentView
          projectId="proj-1"
          docType="business_plan"
          document={makeCompletedDoc()}
          pending={false}
          stale={false}
          capUsed={0}
          capLimit={1}
          onGenerate={noopGenerate}
        />,
      );
    });
    // Only 1 cited_chips row, on the first section that has citations.
    const chipRows = container.querySelectorAll(".document-view__cited-chips");
    expect(chipRows.length).toBe(1);
  });
});

describe("DocumentView edit-on-click", () => {
  it("Edit button opens inline textarea pre-filled with prose_markdown", () => {
    const onPatch = vi
      .fn<(sectionId: string, body: DocumentSectionPatchBody) => Promise<void>>()
      .mockResolvedValue();
    act(() => {
      root.render(
        <DocumentView
          projectId="proj-1"
          docType="business_plan"
          document={makeCompletedDoc()}
          pending={false}
          stale={false}
          capUsed={0}
          capLimit={1}
          onGenerate={noopGenerate}
          onPatchSection={onPatch}
        />,
      );
    });
    const buttons = Array.from(
      container.querySelectorAll<HTMLButtonElement>(
        ".document-view__section-actions button",
      ),
    );
    expect(buttons.length).toBe(2); // 2 sections, 2 Edit buttons.
    act(() => {
      buttons[0]?.click();
    });
    const ta = container.querySelector<HTMLTextAreaElement>(
      ".document-view__edit-textarea",
    );
    expect(ta).toBeTruthy();
    expect(ta?.value).toBe("This is the **summary**.");
    const titleInput = container.querySelector<HTMLInputElement>(
      ".document-view__edit-title",
    );
    expect(titleInput?.value).toBe("Executive summary");
  });

  it("Save fires onPatchSection with only the changed prose_markdown", async () => {
    const onPatch = vi
      .fn<(sectionId: string, body: DocumentSectionPatchBody) => Promise<void>>()
      .mockResolvedValue();
    act(() => {
      root.render(
        <DocumentView
          projectId="proj-1"
          docType="business_plan"
          document={makeCompletedDoc()}
          pending={false}
          stale={false}
          capUsed={0}
          capLimit={1}
          onGenerate={noopGenerate}
          onPatchSection={onPatch}
        />,
      );
    });
    const editBtn = container.querySelectorAll<HTMLButtonElement>(
      ".document-view__section-actions button",
    )[0];
    act(() => {
      editBtn?.click();
    });
    const ta = container.querySelector<HTMLTextAreaElement>(
      ".document-view__edit-textarea",
    )!;
    // Simulate user editing — set value + dispatch input event so React
    // controlled component picks it up.
    act(() => {
      const setter = Object.getOwnPropertyDescriptor(
        window.HTMLTextAreaElement.prototype,
        "value",
      )?.set;
      setter?.call(ta, "Updated prose.");
      ta.dispatchEvent(new Event("input", { bubbles: true }));
    });
    const saveBtn = Array.from(
      container.querySelectorAll<HTMLButtonElement>(
        ".document-view__edit-actions button",
      ),
    ).find((b) => (b.textContent ?? "").toLowerCase().includes("save"));
    await act(async () => {
      saveBtn?.click();
    });
    expect(onPatch).toHaveBeenCalledTimes(1);
    expect(onPatch).toHaveBeenCalledWith(
      "executive_summary",
      expect.objectContaining({ prose_markdown: "Updated prose." }),
    );
    // Title not changed → not included in body (BE Pydantic merges
    // partial updates; we send only the diff).
    const callArg = onPatch.mock.calls[0]?.[1] ?? {};
    expect(callArg).not.toHaveProperty("title");
  });

  it("empty state: doc-type picker offers all 7 doc types", () => {
    act(() => {
      root.render(
        <DocumentView
          projectId="proj-1"
          docType="business_plan"
          document={null}
          pending={false}
          stale={false}
          capUsed={0}
          capLimit={100}
          onGenerate={noopGenerate}
        />,
      );
    });
    const select = container.querySelector<HTMLSelectElement>(
      "#document-view-doctype-select",
    );
    expect(select).toBeTruthy();
    const optionValues = Array.from(select?.options ?? []).map(
      (o) => o.value,
    );
    expect(optionValues).toEqual([
      "business_plan",
      "prd",
      "story_outline",
      "event_plan",
      "marketing_plan",
      "research_proposal",
      "course_outline",
    ]);
    // Initial selection mirrors the prop docType.
    expect(select?.value).toBe("business_plan");
  });

  it("changing the picker updates the empty-state copy + Generate CTA", () => {
    act(() => {
      root.render(
        <DocumentView
          projectId="proj-1"
          docType="business_plan"
          document={null}
          pending={false}
          stale={false}
          capUsed={0}
          capLimit={100}
          onGenerate={noopGenerate}
        />,
      );
    });
    // Before: empty state + CTA reference business_plan copy.
    expect(container.textContent ?? "").toContain("business plan");
    const select = container.querySelector<HTMLSelectElement>(
      "#document-view-doctype-select",
    )!;
    act(() => {
      const setter = Object.getOwnPropertyDescriptor(
        window.HTMLSelectElement.prototype,
        "value",
      )?.set;
      setter?.call(select, "course_outline");
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });
    // After: copy switches to course_outline.
    expect((container.textContent ?? "").toLowerCase()).toContain(
      "course outline",
    );
    expect(select.value).toBe("course_outline");
  });

  it("unmapped domain (docType=null): empty state shows fallback copy + picker", () => {
    act(() => {
      root.render(
        <DocumentView
          projectId="proj-1"
          docType={null}
          document={null}
          pending={false}
          stale={false}
          capUsed={0}
          capLimit={100}
          onGenerate={noopGenerate}
        />,
      );
    });
    // Topbar reads the generic "Document" fallback (NOT "Business plan"
    // even though that's the picker's default selection).
    const topbar = container.querySelector(".document-view__doctype-label");
    expect((topbar?.textContent ?? "").trim().toLowerCase()).toBe(
      "document",
    );
    // Empty title surfaces the unmapped-domain warning, not "Draft your
    // business plan".
    const title = container.querySelector(".document-view__empty-title");
    expect((title?.textContent ?? "").toLowerCase()).toContain(
      "no document type",
    );
    // Picker is still rendered + offers all 7 types.
    const select = container.querySelector<HTMLSelectElement>(
      "#document-view-doctype-select",
    );
    expect(select).toBeTruthy();
    const optionValues = Array.from(select?.options ?? []).map(
      (o) => o.value,
    );
    expect(optionValues).toEqual([
      "business_plan",
      "prd",
      "story_outline",
      "event_plan",
      "marketing_plan",
      "research_proposal",
      "course_outline",
    ]);
  });

  it("unmapped domain: picking a type swaps copy to the picked type", () => {
    act(() => {
      root.render(
        <DocumentView
          projectId="proj-1"
          docType={null}
          document={null}
          pending={false}
          stale={false}
          capUsed={0}
          capLimit={100}
          onGenerate={noopGenerate}
        />,
      );
    });
    const select = container.querySelector<HTMLSelectElement>(
      "#document-view-doctype-select",
    )!;
    act(() => {
      const setter = Object.getOwnPropertyDescriptor(
        window.HTMLSelectElement.prototype,
        "value",
      )?.set;
      setter?.call(select, "story_outline");
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });
    // After picking, topbar + empty copy update to the chosen type.
    const topbar = container.querySelector(".document-view__doctype-label");
    expect((topbar?.textContent ?? "").toLowerCase()).toContain(
      "story outline",
    );
    const title = container.querySelector(".document-view__empty-title");
    expect((title?.textContent ?? "").toLowerCase()).toContain(
      "story outline",
    );
  });

  it("Generate forwards the selected docType override to onGenerate", async () => {
    const onGen = vi
      .fn<(docTypeOverride?: string) => Promise<void>>()
      .mockResolvedValue();
    act(() => {
      root.render(
        <DocumentView
          projectId="proj-1"
          docType="business_plan"
          document={null}
          pending={false}
          stale={false}
          capUsed={0}
          capLimit={100}
          onGenerate={onGen}
        />,
      );
    });
    // Switch the picker to course_outline.
    const select = container.querySelector<HTMLSelectElement>(
      "#document-view-doctype-select",
    )!;
    act(() => {
      const setter = Object.getOwnPropertyDescriptor(
        window.HTMLSelectElement.prototype,
        "value",
      )?.set;
      setter?.call(select, "course_outline");
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });
    // Click the primary Generate CTA in the empty state.
    const generateBtn = Array.from(
      container.querySelectorAll<HTMLButtonElement>(
        ".document-view__empty button",
      ),
    ).find((b) =>
      (b.textContent ?? "").toLowerCase().includes("generate"),
    );
    expect(generateBtn).toBeTruthy();
    await act(async () => {
      generateBtn?.click();
    });
    expect(onGen).toHaveBeenCalledTimes(1);
    expect(onGen).toHaveBeenCalledWith("course_outline");
  });

  it("Cancel closes editor without firing onPatchSection", () => {
    const onPatch = vi
      .fn<(sectionId: string, body: DocumentSectionPatchBody) => Promise<void>>()
      .mockResolvedValue();
    act(() => {
      root.render(
        <DocumentView
          projectId="proj-1"
          docType="business_plan"
          document={makeCompletedDoc()}
          pending={false}
          stale={false}
          capUsed={0}
          capLimit={1}
          onGenerate={noopGenerate}
          onPatchSection={onPatch}
        />,
      );
    });
    const editBtn = container.querySelectorAll<HTMLButtonElement>(
      ".document-view__section-actions button",
    )[0];
    act(() => {
      editBtn?.click();
    });
    expect(container.querySelector(".document-view__edit-textarea")).toBeTruthy();
    const cancelBtn = Array.from(
      container.querySelectorAll<HTMLButtonElement>(
        ".document-view__edit-actions button",
      ),
    ).find((b) => (b.textContent ?? "").toLowerCase().includes("cancel"));
    act(() => {
      cancelBtn?.click();
    });
    expect(container.querySelector(".document-view__edit-textarea")).toBeFalsy();
    expect(onPatch).not.toHaveBeenCalled();
  });
});
