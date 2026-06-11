// H3 regression — cascadeApi must route 401 + project-not-found 404
// through the global dispatchers the rest of the app uses, so the
// SessionExpiredModal + project-not-found toast still fire when a
// cascade call hits these states.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ProjectNotFoundError } from "../api";
import { cascadeApi } from "./cascadeApi";

const realFetch = globalThis.fetch;

function _mockFetch(status: number, body: string): void {
  globalThis.fetch = vi.fn().mockResolvedValue({
    ok: false,
    status,
    statusText: status === 401 ? "Unauthorized" : "Not Found",
    text: async () => body,
    json: async () => ({}),
    headers: { get: () => null },
  } as unknown as Response);
}

beforeEach(() => {
  // Allow CustomEvent inside jsdom.
  globalThis.IS_REACT_ACT_ENVIRONMENT = true as never;
});

afterEach(() => {
  globalThis.fetch = realFetch;
  vi.restoreAllMocks();
});

describe("cascadeApi.preview — 401 dispatch", () => {
  it("dispatches inspira:unauthorized on 401", async () => {
    _mockFetch(401, "");
    const handler = vi.fn();
    window.addEventListener("inspira:unauthorized", handler);
    try {
      await expect(
        cascadeApi.preview("proj-1", {
          commented_decisions: [{ decision_id: "d1", comment_text: "x" }],
          scope_mode: "local",
        }),
      ).rejects.toThrow();
    } finally {
      window.removeEventListener("inspira:unauthorized", handler);
    }
    expect(handler).toHaveBeenCalledTimes(1);
  });

  it("throws ProjectNotFoundError + dispatches inspira:project-not-found on 404", async () => {
    _mockFetch(404, JSON.stringify({ error: "project_not_found" }));
    const handler = vi.fn();
    window.addEventListener("inspira:project-not-found", handler);
    let caught: unknown = null;
    try {
      await cascadeApi.preview("proj-abc", {
        commented_decisions: [{ decision_id: "d1", comment_text: "x" }],
        scope_mode: "local",
      });
    } catch (e) {
      caught = e;
    } finally {
      window.removeEventListener("inspira:project-not-found", handler);
    }
    expect(caught).toBeInstanceOf(ProjectNotFoundError);
    expect(handler).toHaveBeenCalledTimes(1);
  });

  it("does NOT dispatch inspira:unauthorized on a 500", async () => {
    _mockFetch(500, "boom");
    const handler = vi.fn();
    window.addEventListener("inspira:unauthorized", handler);
    try {
      await expect(
        cascadeApi.preview("proj-1", {
          commented_decisions: [{ decision_id: "d1", comment_text: "x" }],
          scope_mode: "local",
        }),
      ).rejects.toThrow();
    } finally {
      window.removeEventListener("inspira:unauthorized", handler);
    }
    expect(handler).not.toHaveBeenCalled();
  });
});
