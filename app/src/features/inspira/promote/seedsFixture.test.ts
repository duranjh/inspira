/**
 * Tests for seedsFixture — deterministic fallback seeds for the Promote dialog.
 *
 * Coverage:
 *   - defaultTopicSeeds returns 5 seeds
 *   - each call returns a fresh array (no shared mutation)
 *   - all seeds start with removed=false and added=false
 *   - makeBlankSeed marks added=true
 *   - all seed ids are unique within and across calls
 */

import { describe, expect, it } from "vitest";

import { defaultTopicSeeds, makeBlankSeed } from "./seedsFixture";

describe("seedsFixture", () => {
  it("defaultTopicSeeds returns 5 seeds", () => {
    expect(defaultTopicSeeds().length).toBe(5);
  });

  it("each call returns a fresh array (no shared mutation)", () => {
    const a = defaultTopicSeeds();
    const b = defaultTopicSeeds();
    expect(a).not.toBe(b);
    a[0].name = "mutated";
    expect(b[0].name).not.toBe("mutated");
  });

  it("all default seeds start unmodified", () => {
    for (const s of defaultTopicSeeds()) {
      expect(s.removed).toBe(false);
      expect(s.added).toBe(false);
    }
  });

  it("makeBlankSeed marks added=true and removed=false", () => {
    const s = makeBlankSeed();
    expect(s.added).toBe(true);
    expect(s.removed).toBe(false);
  });

  it("all seed ids are unique across the dialog's lifecycle", () => {
    const ids = new Set<string>();
    for (let i = 0; i < 3; i++) {
      for (const s of defaultTopicSeeds()) ids.add(s.id);
      ids.add(makeBlankSeed().id);
    }
    // 5 default × 3 + 3 blanks = 18 unique ids.
    expect(ids.size).toBe(18);
  });
});
