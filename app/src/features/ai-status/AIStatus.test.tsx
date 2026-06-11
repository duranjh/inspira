// AI status chip + Orchestrator panel tests.
//
// Covers chip variants, panel open/close, ARIA wiring, focus
// restoration, conflict banner, configure expander (with
// localStorage persistence), costPreview default, Re-run
// state-gating + DEV demo cycle, and the cold-start tooltip path.

import React, { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";

// Stub WorkspaceContext so AIStatus can mount without a real
// <WorkspaceProvider> wrapper. All tests run in fixture mode
// (initialState provided OR no-active-workspace), so the
// useOrchestratorState hook receives null wsId and never polls.
vi.mock("../workspaces/WorkspaceContext", () => ({
  __esModule: true,
  useWorkspaceContext: () => ({
    activeWorkspace: null,
    workspaces: [],
    loading: false,
    error: null,
    setActiveWorkspace: () => {},
    refresh: () => Promise.resolve(),
  }),
  getActiveWorkspaceId: () => null,
  workspaceReady: () => Promise.resolve(),
}));

import { AIStatus } from "./AIStatus";
import {
  makeConflictState,
  makeFailedState,
  makeIdleState,
  makeRunningState,
} from "./mockOrchestratorState";

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
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-05-03T12:00:00Z"));
  // Reset localStorage so configure-open persistence tests start fresh.
  try {
    window.localStorage.clear();
  } catch {
    /* ignore */
  }
});

afterEach(() => {
  act(() => {
    root.unmount();
  });
  container.remove();
  vi.useRealTimers();
  vi.unstubAllEnvs();
  try {
    window.localStorage.clear();
  } catch {
    /* ignore */
  }
});

function mount(props: Parameters<typeof AIStatus>[0] = {}) {
  act(() => {
    root.render(<AIStatus {...props} />);
  });
}

function chipEl(): HTMLButtonElement {
  return container.querySelector(".os-chip") as HTMLButtonElement;
}

function panelEl(): HTMLElement | null {
  return container.querySelector(".os-panel");
}

function rerunEl(): HTMLButtonElement {
  return container.querySelector(".os-rerun") as HTMLButtonElement;
}

describe("AIStatusChip — variants", () => {
  it("renders the Idle chip by default", () => {
    mount();
    const chip = chipEl();
    expect(chip.className).toContain("os-chip--idle");
    expect(chip.querySelector(".os-chip__dot--sage")).not.toBeNull();
    expect(chip.textContent).toContain("Idle");
    expect(chip.textContent).toContain("last run");
  });

  it("renders the Running chip when state is running", () => {
    mount({ initialState: makeRunningState() });
    const chip = chipEl();
    expect(chip.className).toContain("os-chip--running");
    expect(chip.querySelector(".os-chip__dot--gold")).not.toBeNull();
    expect(chip.textContent).toContain("4 sub-agents");
  });

  it("renders the Failed chip with click-to-retry copy", () => {
    mount({ initialState: makeFailedState() });
    const chip = chipEl();
    expect(chip.className).toContain("os-chip--failed");
    expect(chip.querySelector(".os-chip__dot--rust")).not.toBeNull();
    expect(chip.textContent).toContain("click to retry");
  });

  it("renders the Conflict chip with warning glyph (no dot)", () => {
    mount({ initialState: makeConflictState() });
    const chip = chipEl();
    expect(chip.className).toContain("os-chip--conflict");
    expect(chip.querySelector(".os-chip__dot")).toBeNull();
    expect(chip.querySelector(".os-chip__warn")).not.toBeNull();
    expect(chip.textContent).toContain("Resolving conflict");
  });
});

describe("Panel open/close", () => {
  it("toggles panel + flips ARIA on chip click", () => {
    mount();
    const chip = chipEl();
    expect(panelEl()).toBeNull();
    expect(chip.getAttribute("aria-expanded")).toBe("false");
    expect(chip.getAttribute("aria-haspopup")).toBe("true");
    expect(chip.getAttribute("aria-controls")).toBeTruthy();

    act(() => {
      chip.click();
    });
    expect(panelEl()).not.toBeNull();
    expect(chip.getAttribute("aria-expanded")).toBe("true");
    expect(panelEl()!.id).toBe(chip.getAttribute("aria-controls"));
  });

  it("Escape closes the open panel", () => {
    mount();
    act(() => {
      chipEl().click();
    });
    expect(panelEl()).not.toBeNull();
    act(() => {
      document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    });
    expect(panelEl()).toBeNull();
  });

  it("click outside closes the open panel", () => {
    mount();
    act(() => {
      chipEl().click();
    });
    expect(panelEl()).not.toBeNull();
    act(() => {
      document.dispatchEvent(new MouseEvent("mousedown"));
    });
    expect(panelEl()).toBeNull();
  });

  it("restores focus to the chip when the panel closes", () => {
    mount();
    const chip = chipEl();
    act(() => {
      chip.click();
    });
    expect(panelEl()).not.toBeNull();
    act(() => {
      document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    });
    expect(panelEl()).toBeNull();
    expect(document.activeElement).toBe(chip);
  });
});

describe("Panel content — running state", () => {
  it("renders 4 sub-agent cards with the conflict-pending agent", () => {
    mount({ initialState: makeRunningState() });
    act(() => {
      chipEl().click();
    });
    const cards = container.querySelectorAll(".os-agent");
    expect(cards.length).toBe(4);
    const conflictCard = container.querySelector(
      ".os-agent__status--conflict",
    );
    expect(conflictCard).not.toBeNull();
    expect(conflictCard!.textContent).toContain("Conflict pending");
  });

  it("renders the conflict banner when conflict is set", () => {
    mount({ initialState: makeRunningState() });
    act(() => {
      chipEl().click();
    });
    const banner = container.querySelector(".os-conflict-row");
    expect(banner).not.toBeNull();
    expect(banner!.textContent).toContain(
      "Fix login flow vs Test across browsers",
    );
  });

  it("hides the conflict banner when conflict is null", () => {
    mount({ initialState: makeRunningState({ conflict: null }) });
    act(() => {
      chipEl().click();
    });
    expect(container.querySelector(".os-conflict-row")).toBeNull();
  });
});

describe("Panel content — idle state", () => {
  it("renders the idle CTA and no sub-agent cards", () => {
    mount();
    act(() => {
      chipEl().click();
    });
    expect(container.querySelector(".os-idle__cta")).not.toBeNull();
    expect(container.querySelectorAll(".os-agent").length).toBe(0);
  });
});

describe("Configure expander", () => {
  it("toggles the configure body open and closed", () => {
    mount();
    act(() => {
      chipEl().click();
    });
    expect(container.querySelector(".os-config__body")).toBeNull();

    const trigger = container.querySelector(
      ".os-config__trigger",
    ) as HTMLButtonElement;
    act(() => {
      trigger.click();
    });
    expect(container.querySelector(".os-config__body")).not.toBeNull();

    act(() => {
      trigger.click();
    });
    expect(container.querySelector(".os-config__body")).toBeNull();
  });

  it("defaults costPreview ON", () => {
    mount();
    act(() => {
      chipEl().click();
    });
    act(() => {
      (
        container.querySelector(".os-config__trigger") as HTMLButtonElement
      ).click();
    });
    const sw = container.querySelector(".os-config__switch");
    expect(sw!.className).toContain("os-config__switch--on");
  });

  it("toggles costPreview OFF on click", () => {
    mount();
    act(() => {
      chipEl().click();
    });
    act(() => {
      (
        container.querySelector(".os-config__trigger") as HTMLButtonElement
      ).click();
    });
    const toggleBtn = container.querySelector(
      ".os-config__toggle",
    ) as HTMLButtonElement;
    act(() => {
      toggleBtn.click();
    });
    const sw = container.querySelector(".os-config__switch");
    expect(sw!.className).toContain("os-config__switch--off");
  });
});

describe("Re-run button gating", () => {
  it("is disabled when state is running", () => {
    mount({ initialState: makeRunningState() });
    expect(rerunEl().disabled).toBe(true);
  });

  it("is enabled when state is idle", () => {
    mount({ initialState: makeIdleState() });
    expect(rerunEl().disabled).toBe(false);
  });

  it("is enabled when state is failed", () => {
    mount({ initialState: makeFailedState() });
    expect(rerunEl().disabled).toBe(false);
  });

  it("is enabled when state is conflict", () => {
    mount({ initialState: makeConflictState() });
    expect(rerunEl().disabled).toBe(false);
  });
});

describe("Re-run click behavior", () => {
  it("cycles idle → running in DEV", () => {
    // vitest runs DEV-mode by default; no stub needed
    mount({ initialState: makeIdleState() });
    expect(chipEl().className).toContain("os-chip--idle");
    act(() => {
      rerunEl().click();
    });
    expect(chipEl().className).toContain("os-chip--running");
  });

  it("is a no-op in PROD", () => {
    vi.stubEnv("DEV", "");
    mount({ initialState: makeIdleState() });
    expect(chipEl().className).toContain("os-chip--idle");
    act(() => {
      rerunEl().click();
    });
    expect(chipEl().className).toContain("os-chip--idle");
  });
});

describe("Configure expander — localStorage persistence", () => {
  it("starts closed when localStorage is empty", () => {
    mount();
    act(() => {
      chipEl().click();
    });
    expect(container.querySelector(".os-config__body")).toBeNull();
  });

  it("opens hydrated from localStorage on remount", () => {
    window.localStorage.setItem(
      "inspira.ai-status.configure-open",
      "true",
    );
    mount();
    act(() => {
      chipEl().click();
    });
    expect(container.querySelector(".os-config__body")).not.toBeNull();
  });

  it("writes localStorage on toggle", () => {
    mount();
    act(() => {
      chipEl().click();
    });
    expect(
      window.localStorage.getItem("inspira.ai-status.configure-open"),
    ).toBeNull();
    const trigger = container.querySelector(
      ".os-config__trigger",
    ) as HTMLButtonElement;
    act(() => {
      trigger.click();
    });
    expect(
      window.localStorage.getItem("inspira.ai-status.configure-open"),
    ).toBe("true");
    act(() => {
      trigger.click();
    });
    expect(
      window.localStorage.getItem("inspira.ai-status.configure-open"),
    ).toBe("false");
  });
});
