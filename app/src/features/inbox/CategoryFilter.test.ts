// Filter pure-function tests (W2 F6).
//
// applyFilters narrows the items list — we lock the AND-across-
// dimensions semantics + the OR-within-dimension semantics so a
// future regression doesn't accidentally turn the multi-select
// pills into single-select.

import { describe, expect, it } from "vitest";

import { applyFilters, type InboxFilters } from "./CategoryFilter";
import type { FeedbackCategory, FeedbackItem } from "./types";

function fakeItem(overrides: Partial<FeedbackItem> = {}): FeedbackItem {
  return {
    item_id: overrides.item_id ?? "fi-1",
    workspace_id: "ws-test",
    source: overrides.source ?? "csv-import",
    external_id: null,
    content_hash: "hash",
    title: overrides.title ?? "test title",
    body: overrides.body ?? "",
    author: overrides.author ?? null,
    author_email: null,
    received_at: null,
    ingested_at: "2026-05-02T12:00:00+00:00",
    type_hint: overrides.type_hint ?? "bug",
    status: "classified",
  };
}

function blankFilters(): InboxFilters {
  return {
    categories: new Set<FeedbackCategory>(),
    sources: new Set<string>(),
    search: "",
  };
}

describe("applyFilters", () => {
  const items: FeedbackItem[] = [
    fakeItem({ item_id: "fi-1", title: "Login crashes", type_hint: "bug", source: "linear" }),
    fakeItem({ item_id: "fi-2", title: "Add export to CSV", type_hint: "feature", source: "csv-import" }),
    fakeItem({ item_id: "fi-3", title: "Love the kanban", type_hint: "praise", source: "csv-import" }),
    fakeItem({ item_id: "fi-4", title: "How do I switch?", type_hint: "question", source: "linear" }),
  ];

  it("returns all items when no filters set", () => {
    expect(applyFilters(items, blankFilters()).map((i) => i.item_id)).toEqual([
      "fi-1",
      "fi-2",
      "fi-3",
      "fi-4",
    ]);
  });

  it("OR-filters within categories", () => {
    const filters: InboxFilters = {
      ...blankFilters(),
      categories: new Set<FeedbackCategory>(["bug", "praise"]),
    };
    const ids = applyFilters(items, filters).map((i) => i.item_id);
    expect(ids).toEqual(["fi-1", "fi-3"]);
  });

  it("OR-filters within sources", () => {
    const filters: InboxFilters = {
      ...blankFilters(),
      sources: new Set<string>(["linear"]),
    };
    expect(applyFilters(items, filters).map((i) => i.item_id)).toEqual([
      "fi-1",
      "fi-4",
    ]);
  });

  it("AND-filters across dimensions (category + source)", () => {
    const filters: InboxFilters = {
      ...blankFilters(),
      categories: new Set<FeedbackCategory>(["bug"]),
      sources: new Set<string>(["linear"]),
    };
    expect(applyFilters(items, filters).map((i) => i.item_id)).toEqual([
      "fi-1",
    ]);
  });

  it("search matches title", () => {
    const filters = { ...blankFilters(), search: "kanban" };
    expect(applyFilters(items, filters).map((i) => i.item_id)).toEqual([
      "fi-3",
    ]);
  });

  it("search is case-insensitive", () => {
    const filters = { ...blankFilters(), search: "LOGIN" };
    expect(applyFilters(items, filters).map((i) => i.item_id)).toEqual([
      "fi-1",
    ]);
  });

  it("search across body + author + title", () => {
    const richer = [
      fakeItem({ item_id: "fi-a", title: "x", body: "Safari issue" }),
      fakeItem({ item_id: "fi-b", title: "x", author: "Maria K." }),
    ];
    expect(
      applyFilters(richer, { ...blankFilters(), search: "safari" }).map((i) => i.item_id),
    ).toEqual(["fi-a"]);
    expect(
      applyFilters(richer, { ...blankFilters(), search: "maria" }).map((i) => i.item_id),
    ).toEqual(["fi-b"]);
  });

  it("returns empty when filters exclude everything", () => {
    const filters: InboxFilters = {
      ...blankFilters(),
      categories: new Set<FeedbackCategory>(["noise"]),
    };
    expect(applyFilters(items, filters)).toEqual([]);
  });
});
