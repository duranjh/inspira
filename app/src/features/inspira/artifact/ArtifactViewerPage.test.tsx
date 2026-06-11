import React, { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
  type MockInstance,
} from "vitest";

vi.mock("../api", async () => {
  const actual = await vi.importActual<object>("../api");
  return {
    ...actual,
    api: {
      getArtifact: vi.fn(),
      generateArtifactStream: vi.fn(),
      editArtifactStream: vi.fn(),
      // ArtifactViewerPage hydrates project_state from the backend
      // on mount so the ApprovalChip + read-only gating reflect
      // server truth even when the prop-supplied initialState is
      // stale. Tests that don't care about the chip just leave the
      // rejection in place.
      getV2Project: vi.fn().mockRejectedValue(new Error("not stubbed")),
      // Wave F.4 — useArtifactComments fetches on mount.
      // Default to empty so existing tests aren't affected.
      listArtifactComments: vi.fn().mockResolvedValue({ comments: [] }),
      createArtifactComment: vi.fn(),
      updateArtifactComment: vi.fn(),
      // Wave F.5 — useStaleness fetches on mount + every 60s. Tests
      // that don't care leave the rejection in place; the FE treats
      // staleness errors as non-fatal (badges simply don't appear).
      getPrOverlayStaleness: vi.fn().mockRejectedValue(
        new Error("not stubbed"),
      ),
    },
  };
});

// ArtifactViewerPage calls useNavigate() at mount; without a Router
// wrapper that throws. Tests don't exercise navigation, so a no-op
// mock is simpler than wrapping every render in MemoryRouter.
vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<object>("react-router-dom");
  return { ...actual, useNavigate: () => vi.fn() };
});

import { api } from "../api";
import { ArtifactViewerPage } from "./ArtifactViewerPage";

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
  // Default mock — getArtifact pending forever; tests that need a
  // resolution explicitly resolve their own mock.
  (api.getArtifact as unknown as MockInstance).mockReset();
  (api.generateArtifactStream as unknown as MockInstance).mockReset();
  (api.editArtifactStream as unknown as MockInstance).mockReset();
});

afterEach(() => {
  act(() => {
    root.unmount();
  });
  container.remove();
});

describe("ArtifactViewerPage", () => {
  it("renders the top bar with the project title (no legacy approval badge)", async () => {
    // Pending fetch keeps the page in loading state for this assertion.
    (api.getArtifact as unknown as MockInstance).mockReturnValue(
      new Promise(() => {}),
    );

    await act(async () => {
      root.render(
        <ArtifactViewerPage
          projectId="proj-1"
          projectTitle="Notes"
          initialState="pending_review"
          onBack={() => {}}
        />,
      );
    });

    const title = container.querySelector(".av-top__title");
    expect(title?.textContent).toBe("Notes");
    // The "✓ Approved · {age}" / "Drafting…" badge was removed in
    // the founder reframe (2026-05-04) — ApprovalChip is the
    // canonical state surface for the artifact's review lifecycle.
    expect(container.querySelector(".av-badge")).toBeNull();
  });

  it("calls onBack when the Back button is clicked", async () => {
    (api.getArtifact as unknown as MockInstance).mockReturnValue(
      new Promise(() => {}),
    );
    const onBack = vi.fn();

    await act(async () => {
      root.render(
        <ArtifactViewerPage
          projectId="proj-1"
          projectTitle="Notes"
          initialState="pending_review"
          onBack={onBack}
        />,
      );
    });

    const back = container.querySelector<HTMLButtonElement>(".av-top__back");
    expect(back).not.toBeNull();
    act(() => {
      back!.click();
    });
    expect(onBack).toHaveBeenCalledTimes(1);
  });

  it("surfaces a non-404 error from getArtifact in the empty state", async () => {
    (api.getArtifact as unknown as MockInstance).mockRejectedValue(
      new Error("network failed"),
    );

    await act(async () => {
      root.render(
        <ArtifactViewerPage
          projectId="proj-1"
          projectTitle="Notes"
          initialState="pending_review"
          onBack={() => {}}
        />,
      );
    });
    // Let the rejection chain settle.
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    const errLine = container.querySelector(".av-empty__line--error");
    expect(errLine?.textContent).toContain("network failed");
  });

  it("auto-fires generateArtifactStream when getArtifact returns 404 (any project_state)", async () => {
    // Founder reframe 2026-05-04: artifact (code) IS what gets
    // approved — not the canvas. The viewer is openable + generatable
    // regardless of project_state. This test pins that contract by
    // mounting with initialState="in_review" and asserting auto-fire.
    const err = new Error("artifact_not_generated 404");
    (api.getArtifact as unknown as MockInstance).mockRejectedValue(err);
    (api.generateArtifactStream as unknown as MockInstance).mockReturnValue(
      new Promise(() => {}),
    );

    await act(async () => {
      root.render(
        <ArtifactViewerPage
          projectId="proj-1"
          projectTitle="Notes"
          initialState="in_review"
          onBack={() => {}}
        />,
      );
    });
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(
      (api.generateArtifactStream as unknown as MockInstance).mock.calls.length,
    ).toBeGreaterThanOrEqual(1);
  });

  it("dispatches inspira:export-to-linear with projectId when Send to Linear is clicked", async () => {
    (api.getArtifact as unknown as MockInstance).mockReturnValue(
      new Promise(() => {}),
    );
    const listener = vi.fn();
    window.addEventListener("inspira:export-to-linear", listener as EventListener);

    await act(async () => {
      root.render(
        <ArtifactViewerPage
          projectId="proj-42"
          projectTitle="Notes"
          initialState="pending_review"
          onBack={() => {}}
        />,
      );
    });

    const linear = container.querySelector<HTMLButtonElement>(
      ".av-top__btn--primary",
    );
    expect(linear).not.toBeNull();
    act(() => {
      linear!.click();
    });

    expect(listener).toHaveBeenCalledTimes(1);
    const event = listener.mock.calls[0][0] as CustomEvent<{ projectId: string }>;
    expect(event.detail.projectId).toBe("proj-42");

    window.removeEventListener(
      "inspira:export-to-linear",
      listener as EventListener,
    );
  });

  it("does NOT dispatch inspira:export-to-github when Push to GitHub is clicked (PR push, not Issue modal)", async () => {
    // Founder fix 2026-05-04: previously the artifact viewer's
    // Push button ALSO opened the Issue-export modal as a side
    // effect. That confused users (modal hides the success toast,
    // its own Push button fires another PR endpoint). The PR push
    // and the Issue modal are now strictly separate surfaces.
    (api.getArtifact as unknown as MockInstance).mockReturnValue(
      new Promise(() => {}),
    );
    const listener = vi.fn();
    window.addEventListener("inspira:export-to-github", listener as EventListener);

    await act(async () => {
      root.render(
        <ArtifactViewerPage
          projectId="proj-42"
          projectTitle="Notes"
          initialState="pending_review"
          onBack={() => {}}
        />,
      );
    });

    const github = container.querySelector<HTMLButtonElement>(
      ".av-top__btn--ghost",
    );
    expect(github).not.toBeNull();
    act(() => {
      github!.click();
    });

    expect(listener).not.toHaveBeenCalled();

    window.removeEventListener(
      "inspira:export-to-github",
      listener as EventListener,
    );
  });
});
