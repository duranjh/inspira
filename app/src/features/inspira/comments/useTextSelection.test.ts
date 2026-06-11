// Pure-function tests for ``computeFloatingPosition``. Selection
// mechanics themselves go through DOM events that aren't well-suited
// to vitest+jsdom — a manual smoke covers that path.

import { describe, expect, it } from "vitest";

import { computeFloatingPosition } from "./useTextSelection";

describe("computeFloatingPosition", () => {
  const viewport = { width: 1024, height: 768 };

  it("renders above when there's room", () => {
    const pos = computeFloatingPosition(
      { top: 200, left: 400, width: 80, height: 16 },
      viewport,
      96,
    );
    expect(pos.placement).toBe("above");
    expect(pos.top).toBeLessThan(200);
  });

  it("flips below when selection is near the top of the viewport", () => {
    const pos = computeFloatingPosition(
      { top: 10, left: 400, width: 80, height: 16 },
      viewport,
      96,
    );
    expect(pos.placement).toBe("below");
    expect(pos.top).toBeGreaterThan(10);
  });

  it("clamps left to the viewport's left edge", () => {
    const pos = computeFloatingPosition(
      { top: 100, left: -50, width: 20, height: 16 },
      viewport,
      96,
    );
    expect(pos.left).toBeGreaterThanOrEqual(8);
  });

  it("clamps right to the viewport's right edge", () => {
    const pos = computeFloatingPosition(
      { top: 100, left: 1100, width: 50, height: 16 },
      viewport,
      96,
    );
    expect(pos.left).toBeLessThanOrEqual(viewport.width - 96 - 8);
  });
});
