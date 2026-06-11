// ConnectorTile state-derivation tests (W2 C6).
//
// Pure-function tests for `deriveTileState` — covers the four
// states the design specifies + the `connecting` override.
// Rendering tests are minimal (DOM mount with react-dom/client)
// so we lock the data-state attribute on the tile root.

import React, { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  ConnectorTile,
  TileState,
  deriveTileState,
} from "./ConnectorTile";
import type { ConnectorRuntimeState } from "./types";

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

function makeState(overrides: Partial<ConnectorRuntimeState> = {}): ConnectorRuntimeState {
  return {
    status: "not_connected",
    account: null,
    primary_repo_full_name: null,
    repo_count: 0,
    last_sync_at: null,
    last_successful_sync_at: null,
    last_error: null,
    ...overrides,
  };
}

describe("deriveTileState", () => {
  it("idle when status is not_connected and not connecting", () => {
    expect(deriveTileState(makeState({ status: "not_connected" }), false)).toBe(
      "idle" satisfies TileState,
    );
  });

  it("idle when status is not_implemented (placeholder providers)", () => {
    expect(
      deriveTileState(makeState({ status: "not_implemented" }), false),
    ).toBe("idle");
  });

  it("connecting overrides every other state", () => {
    // Even if backend says connected, a fresh user click flips us
    // into the connecting branch first. The state usually flips
    // back to connected within ~50ms before the redirect fires —
    // but the user gets immediate feedback either way.
    expect(
      deriveTileState(makeState({ status: "connected" }), true),
    ).toBe("connecting");
    expect(
      deriveTileState(makeState({ status: "error" }), true),
    ).toBe("connecting");
  });

  it("connected when status=connected and not connecting", () => {
    expect(
      deriveTileState(makeState({ status: "connected" }), false),
    ).toBe("connected");
  });

  it("error when status is needs_reauth", () => {
    expect(
      deriveTileState(makeState({ status: "needs_reauth" }), false),
    ).toBe("error");
  });

  it("error when status is error", () => {
    expect(
      deriveTileState(makeState({ status: "error" }), false),
    ).toBe("error");
  });
});

describe("ConnectorTile rendering", () => {
  it("renders idle CTA when state is not_connected", () => {
    act(() => {
      root.render(
        <ConnectorTile
          provider="github"
          displayName="GitHub"
          summary="Connect a repo."
          state={makeState({ status: "not_connected" })}
          ctaLabel="Connect with GitHub →"
          onConnect={() => {}}
          onSync={() => {}}
          onManage={() => {}}
          onRetry={() => {}}
        />,
      );
    });
    const tile = container.querySelector(".connector-tile") as HTMLElement;
    expect(tile.dataset.state).toBe("idle");
    expect(container.textContent).toContain("Connect with GitHub →");
  });

  it("renders connected meta line when state is connected", () => {
    act(() => {
      root.render(
        <ConnectorTile
          provider="github"
          displayName="GitHub"
          summary="Connect a repo."
          state={makeState({
            status: "connected",
            account: "acme-corp",
            primary_repo_full_name: "acme-platform",
            repo_count: 3,
            last_sync_at: new Date().toISOString(),
          })}
          ctaLabel="Connect with GitHub →"
          onConnect={() => {}}
          onSync={() => {}}
          onManage={() => {}}
          onRetry={() => {}}
        />,
      );
    });
    const tile = container.querySelector(".connector-tile") as HTMLElement;
    expect(tile.dataset.state).toBe("connected");
    expect(container.textContent).toContain("acme-corp");
    expect(container.textContent).toContain("3 repos");
  });

  it("renders error pill + Retry when state is error", () => {
    act(() => {
      root.render(
        <ConnectorTile
          provider="github"
          displayName="GitHub"
          summary="Connect a repo."
          state={makeState({
            status: "error",
            account: "acme-corp",
            last_successful_sync_at: new Date(
              Date.now() - 6 * 3600 * 1000,
            ).toISOString(),
          })}
          ctaLabel="Connect with GitHub →"
          onConnect={() => {}}
          onSync={() => {}}
          onManage={() => {}}
          onRetry={() => {}}
        />,
      );
    });
    const tile = container.querySelector(".connector-tile") as HTMLElement;
    expect(tile.dataset.state).toBe("error");
    expect(container.textContent).toContain("Sync failed");
    expect(container.textContent).toContain("Retry →");
  });

  it("renders Opening… spinner when connecting", () => {
    act(() => {
      root.render(
        <ConnectorTile
          provider="github"
          displayName="GitHub"
          summary="Connect a repo."
          state={makeState({ status: "not_connected" })}
          connecting
          ctaLabel="Connect with GitHub →"
          onConnect={() => {}}
          onSync={() => {}}
          onManage={() => {}}
          onRetry={() => {}}
        />,
      );
    });
    const tile = container.querySelector(".connector-tile") as HTMLElement;
    expect(tile.dataset.state).toBe("connecting");
    expect(container.textContent).toContain("Opening GitHub…");
  });
});
