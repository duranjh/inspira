// @vitest-environment happy-dom
//
// Tests for the W2 κ exports surface. Three angles:
//
// 1. ExportModalsHost — listens for the right window events and reads
//    detail.projectId.
// 2. api.exportProject* helpers — hit the right URL with the right body.
// 3. parseExportError — translates known backend error codes into the
//    friendly inline-error messages.
//
// Full visual rendering of the modal (Dialog + cards + selects) is
// verified in the live browser smoke pass per the W0 ritual. These
// tests target the load-bearing logic.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";

import { api } from "../api";
import { __resolveReadyForTesting } from "../../workspaces/WorkspaceContext";
import {
  EXPORT_TO_GITHUB_EVENT,
  EXPORT_TO_LINEAR_EVENT,
  ExportModalsHost,
} from "./index";

// Tell React this is an act-aware environment so the createRoot +
// act(...) flow under happy-dom doesn't warn on every render.
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

// Resolve workspaceReady() so api.ts helpers (which now await
// hydration before stamping X-Workspace-Id on cross-origin POSTs)
// don't hang waiting for a WorkspaceProvider that isn't mounted.
__resolveReadyForTesting(null);


// =====================================================================
// API client methods
// =====================================================================

describe("exports api client", () => {
  let fetchSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(
        new Response(JSON.stringify({ ok: true }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      );
  });

  afterEach(() => {
    fetchSpy.mockRestore();
  });

  it("getConnectorDestination hits the linear destination route", async () => {
    await api.getConnectorDestination("linear");
    const [url, init] = fetchSpy.mock.calls[0]!;
    expect(String(url)).toContain("/api/v2/connectors/linear/destination");
    expect(init?.credentials).toBe("include");
  });

  it("getConnectorDestination hits the github destination route", async () => {
    await api.getConnectorDestination("github");
    const [url] = fetchSpy.mock.calls[0]!;
    expect(String(url)).toContain("/api/v2/connectors/github/destination");
  });

  it("exportProjectToLinear posts options to the project route", async () => {
    fetchSpy.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          ok: true,
          provider: "linear",
          issue_url: "https://linear.app/x",
          identifier: "ACM-1",
          issue_id: "issue-1",
          sub_issue_count: 3,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    const result = await api.exportProjectToLinear("proj-1", {
      include_canvas_link: true,
      include_source_feedback: false,
      apply_priority_label: true,
      priority_label: "P0",
    });
    const [url, init] = fetchSpy.mock.calls[0]!;
    expect(String(url)).toContain("/api/v2/projects/proj-1/export/linear");
    expect(init?.method).toBe("POST");
    const sentBody = JSON.parse(String(init?.body));
    expect(sentBody.priority_label).toBe("P0");
    expect(sentBody.include_source_feedback).toBe(false);
    expect(result.identifier).toBe("ACM-1");
  });

  it("exportProjectToGitHub posts options to the github route", async () => {
    fetchSpy.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          ok: true,
          provider: "github",
          issue_url: "https://github.com/x/y/issues/1",
          issue_number: 1,
          issue_id: 99,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    const result = await api.exportProjectToGitHub("proj-1", {
      include_canvas_link: true,
      include_source_feedback: true,
      apply_priority_label: false,
      priority_label: "P2",
    });
    const [url] = fetchSpy.mock.calls[0]!;
    expect(String(url)).toContain("/api/v2/projects/proj-1/export/github");
    expect(result.issue_number).toBe(1);
  });

  it("encodes special chars in project_id", async () => {
    await api.exportProjectToLinear("proj/special?id=1", {
      include_canvas_link: true,
      include_source_feedback: true,
      apply_priority_label: true,
      priority_label: "P1",
    });
    const [url] = fetchSpy.mock.calls[0]!;
    expect(String(url)).toContain("/projects/proj%2Fspecial%3Fid%3D1/");
  });
});


// =====================================================================
// ExportModalsHost — window event wiring
// =====================================================================

describe("ExportModalsHost", () => {
  let container: HTMLDivElement;
  let root: Root;
  let fetchSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    // Stub fetch so the modal's open-on-event triggers don't hammer
    // a real network. Resolve with a destination-not-configured shape
    // so the modal renders quickly and stably.
    fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(
        new Response(
          JSON.stringify({
            configured: false,
            display: null,
            metadata: {},
            hint: "configure first",
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      );
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    container.remove();
    fetchSpy.mockRestore();
  });

  it("opens linear modal on inspira:export-to-linear", async () => {
    await act(async () => {
      root.render(<ExportModalsHost />);
    });
    expect(document.querySelector('[role="dialog"]')).toBeNull();

    await act(async () => {
      window.dispatchEvent(
        new CustomEvent(EXPORT_TO_LINEAR_EVENT, {
          detail: { projectId: "proj-1" },
        }),
      );
    });
    const dialog = document.querySelector('[role="dialog"]');
    expect(dialog).not.toBeNull();
    expect(dialog?.textContent).toContain("Send to Linear");
  });

  it("opens github modal on inspira:export-to-github", async () => {
    await act(async () => {
      root.render(<ExportModalsHost />);
    });
    await act(async () => {
      window.dispatchEvent(
        new CustomEvent(EXPORT_TO_GITHUB_EVENT, {
          detail: { projectId: "proj-2" },
        }),
      );
    });
    const dialog = document.querySelector('[role="dialog"]');
    expect(dialog?.textContent).toContain("Push to GitHub");
  });

  it("ignores dispatches without a projectId", async () => {
    await act(async () => {
      root.render(<ExportModalsHost />);
    });
    await act(async () => {
      window.dispatchEvent(
        new CustomEvent(EXPORT_TO_LINEAR_EVENT, { detail: {} }),
      );
    });
    expect(document.querySelector('[role="dialog"]')).toBeNull();
  });
});
