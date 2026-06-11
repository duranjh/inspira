// Unit tests for the v4 httpClient (W2 C4).
//
// Covers the watch points:
//
//  #2 Auth + bootstrap endpoints skip X-Workspace-Id injection.
//     `shouldSkipWorkspaceHeader` is exported via __testing__ so
//     we can assert the skip-list directly without mocking fetch.
//
//  Other coverage (skip-list edge cases, query-string handling)
//  rounds out the small surface area.

import { describe, expect, it } from "vitest";

import { __testing__ } from "./httpClient";

const { shouldSkipWorkspaceHeader } = __testing__;

describe("shouldSkipWorkspaceHeader", () => {
  it("skips all auth paths under /api/auth/", () => {
    for (const p of [
      "/api/auth/signup",
      "/api/auth/login",
      "/api/auth/logout",
      "/api/auth/me",
      "/api/auth/forgot-password",
      "/api/auth/reset-password",
      "/api/auth/verify-email",
      "/api/auth/google/callback",
    ]) {
      expect(shouldSkipWorkspaceHeader(p)).toBe(true);
    }
  });

  it("skips health endpoints", () => {
    expect(shouldSkipWorkspaceHeader("/api/health")).toBe(true);
    expect(shouldSkipWorkspaceHeader("/api/health/deep")).toBe(true);
  });

  it("skips the list-mine + create-workspace endpoint exactly", () => {
    // Exact match: the bare /api/v2/workspaces path. Per-workspace
    // sub-paths DO carry the header (they're scoped by path-param
    // server-side, but the header is harmless and useful for audit).
    expect(shouldSkipWorkspaceHeader("/api/v2/workspaces")).toBe(true);
    expect(
      shouldSkipWorkspaceHeader("/api/v2/workspaces/ws-123"),
    ).toBe(false);
    expect(
      shouldSkipWorkspaceHeader("/api/v2/workspaces/ws-123/members"),
    ).toBe(false);
  });

  it("respects exact-match boundaries (no false-prefix matches)", () => {
    // /api/auth (no trailing slash) is NOT a prefix of any auth
    // endpoint, but as a literal path it would still skip. Per
    // the implementation, /api/auth/* matches via the trailing-
    // slash prefix, while plain /api/auth would only match if
    // we added it to the exact list. Verify current behaviour:
    expect(shouldSkipWorkspaceHeader("/api/auth")).toBe(false);
    expect(shouldSkipWorkspaceHeader("/api/auth/")).toBe(true);
  });

  it("strips query string before matching", () => {
    expect(
      shouldSkipWorkspaceHeader("/api/auth/me?refresh=1"),
    ).toBe(true);
    expect(
      shouldSkipWorkspaceHeader(
        "/api/v2/workspaces?include=archived",
      ),
    ).toBe(true);
    expect(
      shouldSkipWorkspaceHeader("/api/v2/connectors?source=ui"),
    ).toBe(false);
  });

  it("does NOT skip workspace-scoped endpoints", () => {
    for (const p of [
      "/api/v2/connectors",
      "/api/v2/connectors/github/sync",
      "/api/v2/connectors/github",
      "/api/v2/projects",
      "/api/v2/projects/proj-123",
      "/api/v2/feedback/extract-themes",
    ]) {
      expect(shouldSkipWorkspaceHeader(p)).toBe(false);
    }
  });
});
