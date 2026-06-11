// CascadeBanner — three banner-state branches.

import React, { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { CascadeBanner } from "./CascadeBanner";
import { CommentsProvider, useComments } from "./CommentsContext";

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
});

const fakeApi = {
  preview: async () => ({}) as never,
  commit: async () => ({}) as never,
  status: async () => ({}) as never,
};

describe("CascadeBanner", () => {
  it("renders nothing when preview is null", () => {
    act(() => {
      root.render(
        <CommentsProvider projectId="p1" api={fakeApi as never}>
          <CascadeBanner />
        </CommentsProvider>,
      );
    });
    expect(container.querySelector(".cc-banner")).toBeNull();
  });

  it("renders nothing when banner_state is 'none'", async () => {
    function NoneBanner(): React.JSX.Element {
      const { previewCascade } = useComments();
      React.useEffect(() => {
        // Force a "none" preview via the API stub.
        void previewCascade(
          { kind: "decision", id: "dec-1" },
          "x",
          "local",
        );
      }, [previewCascade]);
      return <CascadeBanner />;
    }
    const noneApi = {
      ...fakeApi,
      preview: async () => ({
        affected_scope: { decision_ids: [], topic_ids: [], count: 0, banner_state: "none" as const },
        estimated_cost_usd: 0,
        estimated_seconds: 0,
      }),
    };
    await act(async () => {
      root.render(
        <CommentsProvider projectId="p1" api={noneApi as never}>
          <NoneBanner />
        </CommentsProvider>,
      );
    });
    // Wait one microtask for preview promise to resolve
    await act(async () => {
      await Promise.resolve();
    });
    expect(container.querySelector(".cc-banner")).toBeNull();
  });

  it("renders gold variant for narrow", async () => {
    function NarrowBanner(): React.JSX.Element {
      const { previewCascade } = useComments();
      React.useEffect(() => {
        void previewCascade(
          { kind: "decision", id: "dec-1" },
          "x",
          "cascade",
        );
      }, [previewCascade]);
      return <CascadeBanner />;
    }
    const narrowApi = {
      ...fakeApi,
      preview: async () => ({
        affected_scope: {
          decision_ids: ["d2", "d3"],
          topic_ids: ["t1"],
          count: 2,
          banner_state: "narrow" as const,
        },
        estimated_cost_usd: 0.003,
        estimated_seconds: 6,
      }),
    };
    await act(async () => {
      root.render(
        <CommentsProvider projectId="p1" api={narrowApi as never}>
          <NarrowBanner />
        </CommentsProvider>,
      );
    });
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    const banner = container.querySelector(".cc-banner");
    expect(banner).not.toBeNull();
    expect(banner?.classList.contains("cc-banner--gold")).toBe(true);
    expect(banner?.textContent).toMatch(/2 decisions affected/);
  });

  it("renders rust variant for wide", async () => {
    function WideBanner(): React.JSX.Element {
      const { previewCascade } = useComments();
      React.useEffect(() => {
        void previewCascade(
          { kind: "decision", id: "dec-1" },
          "x",
          "cascade",
        );
      }, [previewCascade]);
      return <CascadeBanner />;
    }
    const wideApi = {
      ...fakeApi,
      preview: async () => ({
        affected_scope: {
          decision_ids: ["d2", "d3", "d4", "d5", "d6"],
          topic_ids: ["t1"],
          count: 5,
          banner_state: "wide" as const,
        },
        estimated_cost_usd: 0.008,
        estimated_seconds: 15,
      }),
    };
    await act(async () => {
      root.render(
        <CommentsProvider projectId="p1" api={wideApi as never}>
          <WideBanner />
        </CommentsProvider>,
      );
    });
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    const banner = container.querySelector(".cc-banner");
    expect(banner).not.toBeNull();
    expect(banner?.classList.contains("cc-banner--rust")).toBe(true);
    expect(banner?.textContent).toMatch(/Wide cascade — 5 decisions/);
  });
});
