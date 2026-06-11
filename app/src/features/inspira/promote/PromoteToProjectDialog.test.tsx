/**
 * Tests for the Promote-to-Project flow — Controller + Dialog.
 *
 * Coverage:
 *   - Controller opens dialog when inspira:promote-to-project fires
 *   - Dialog displays cluster chip text from feedback item / cluster
 *   - Title input toggles the Modified pill on edit
 *   - Topic seed remove → counter decrements + restore reverses it
 *   - Add another topic appends a row marked --added
 *   - Drag a removed seed → no-op (no state mutation)
 *   - Decisions preview chevron flips on toggle
 *   - Send-back panel expands; submit button is disabled with tooltip
 *   - Promote success → calls api.promoteToProject + onPromoted callback
 *   - Promote success → dispatches inspira:feedback-item-promoted
 *   - Promote failure → spawning hidden, error rendered, dialog stays open
 *   - Cancel button closes dialog without API call
 *   - Spam-clicking dispatcher is idempotent (Controller ignores re-fires)
 */

import React, { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { FeedbackItem } from "../../inbox/types";
import { api } from "../api";
import {
  PromoteToProjectController,
} from "./PromoteToProjectController";
import { PromoteToProjectDialog } from "./PromoteToProjectDialog";

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
  vi.restoreAllMocks();
});

function makeFeedbackItem(over: Partial<FeedbackItem> = {}): FeedbackItem {
  return {
    item_id: "fi-1",
    workspace_id: "w-1",
    source: "intercom",
    external_id: null,
    content_hash: "h",
    title: "Mobile login broken on iOS Safari",
    body: "Can't log in from a fresh Safari session on iOS 17.",
    author: null,
    author_email: null,
    received_at: "2026-05-03T10:00:00Z",
    ingested_at: "2026-05-03T10:00:00Z",
    type_hint: "bug",
    status: "classified",
    cluster_id: "c-1",
    ...over,
  };
}

function setNativeValue(el: HTMLInputElement | HTMLTextAreaElement, value: string) {
  const proto =
    el instanceof HTMLTextAreaElement
      ? HTMLTextAreaElement.prototype
      : HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(proto, "value")!.set!;
  setter.call(el, value);
  el.dispatchEvent(new Event("input", { bubbles: true }));
}

function firePromoteEvent(item: FeedbackItem, cluster?: unknown) {
  window.dispatchEvent(
    new CustomEvent("inspira:promote-to-project", {
      detail: { feedbackItem: item, cluster },
    }),
  );
}

describe("PromoteToProjectController", () => {
  it("opens dialog when inspira:promote-to-project fires", () => {
    act(() => {
      root.render(
        <MemoryRouter>
          <PromoteToProjectController />
        </MemoryRouter>,
      );
    });
    expect(document.querySelector("[role='dialog']")).toBeNull();
    act(() => {
      firePromoteEvent(makeFeedbackItem());
    });
    expect(document.querySelector("[role='dialog']")).not.toBeNull();
  });

  it("ignores additional dispatches while dialog is open (idempotent)", () => {
    act(() => {
      root.render(
        <MemoryRouter>
          <PromoteToProjectController />
        </MemoryRouter>,
      );
    });
    act(() => {
      firePromoteEvent(makeFeedbackItem({ item_id: "fi-1" }));
    });
    // Capture the title input — it reflects the FIRST item's title.
    const firstTitle = (
      document.querySelector(".pm-title-input") as HTMLInputElement | null
    )?.value;
    expect(firstTitle).toContain("Mobile login");
    act(() => {
      firePromoteEvent(makeFeedbackItem({ item_id: "fi-2", title: "Different bug" }));
    });
    // The dialog stayed open with the first item's data — second dispatch ignored.
    const stillFirst = (
      document.querySelector(".pm-title-input") as HTMLInputElement | null
    )?.value;
    expect(stillFirst).toContain("Mobile login");
  });

  it("Promote success navigates to /app with router state", async () => {
    let lastLocation: { pathname?: string; state?: unknown } | null = null;
    function LocationProbe() {
      const loc = useLocation();
      lastLocation = { pathname: loc.pathname, state: loc.state };
      return null;
    }
    const spy = vi
      .spyOn(api, "promoteToProject")
      .mockResolvedValue({
        project: {
          // V2Project shape — only project_id is read by the controller.
          project_id: "p_99",
          owner_user_id: "u",
          title: "Mobile login",
          status: "ready",
          created_at: "",
          updated_at: "",
          icon: null,
          color: null,
          archived_at: null,
          deleted_at: null,
        } as never,
      });

    act(() => {
      root.render(
        <MemoryRouter initialEntries={["/inbox"]}>
          <Routes>
            <Route path="/inbox" element={<div data-testid="inbox-route" />} />
            <Route
              path="/app"
              element={<div data-testid="app-route" />}
            />
            <Route
              path="*"
              element={<div data-testid="default-route" />}
            />
          </Routes>
          <PromoteToProjectController />
          <LocationProbe />
        </MemoryRouter>,
      );
    });
    act(() => {
      firePromoteEvent(makeFeedbackItem());
    });
    const promoteBtn = document.querySelector<HTMLButtonElement>(
      ".pm-footer__promote",
    );
    expect(promoteBtn).not.toBeNull();
    await act(async () => {
      promoteBtn!.click();
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(spy).toHaveBeenCalledWith(
      expect.objectContaining({
        cluster_id: "c-1",
        project_title: "Mobile login broken on iOS Safari",
        feedback_item_id: "fi-1",
      }),
    );
    expect(lastLocation?.pathname).toBe("/app");
    expect(lastLocation?.state).toEqual({
      openProject: "p_99",
      pendingReview: true,
    });
  });
});

describe("PromoteToProjectDialog", () => {
  function renderDialog(opts: {
    onClose?: () => void;
    onPromoted?: (id: string) => void;
    cluster?: unknown;
  } = {}) {
    const onClose = opts.onClose ?? vi.fn();
    const onPromoted = opts.onPromoted ?? vi.fn();
    act(() => {
      root.render(
        <MemoryRouter>
          <PromoteToProjectDialog
            open={true}
            feedbackItem={makeFeedbackItem()}
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            cluster={(opts.cluster as any) ?? null}
            onClose={onClose}
            onPromoted={onPromoted}
          />
        </MemoryRouter>,
      );
    });
    return { onClose, onPromoted };
  }

  it("renders cluster chip text from item title when no cluster summary present", () => {
    renderDialog();
    const chip = document.querySelector(".pm-dialog__chip-text");
    expect(chip?.textContent).toContain("Mobile login broken on iOS Safari");
  });

  it("renders cluster chip text from cluster summary when provided", () => {
    renderDialog({
      cluster: {
        cluster_id: "c-1",
        theme: "Mobile login broken on iOS Safari",
        item_count: 12,
        severity: 5,
      },
    });
    const chip = document.querySelector(".pm-dialog__chip-text");
    expect(chip?.textContent).toBe(
      "12 items · severity 5 · cluster: 'Mobile login broken on iOS Safari'",
    );
  });

  it("title edit toggles Modified pill", () => {
    renderDialog();
    expect(document.querySelector(".pm-modified")).toBeNull();
    const input = document.querySelector<HTMLInputElement>(".pm-title-input")!;
    act(() => {
      setNativeValue(input, "Different title");
    });
    expect(document.querySelector(".pm-modified")).not.toBeNull();
    // Clearing back hides the pill.
    act(() => {
      setNativeValue(input, "Mobile login broken on iOS Safari");
    });
    expect(document.querySelector(".pm-modified")).toBeNull();
  });

  it("topic seed remove decrements counter and restore reverses", () => {
    renderDialog();
    const labelText = () =>
      document
        .querySelectorAll(".pm-block__label")[1]
        ?.textContent ?? "";
    expect(labelText()).toContain("(5)");
    const firstRemove = document.querySelectorAll<HTMLButtonElement>(
      ".pm-topic__remove",
    )[0];
    act(() => {
      firstRemove.click();
    });
    expect(labelText()).toContain("(4)");
    act(() => {
      firstRemove.click();
    });
    expect(labelText()).toContain("(5)");
  });

  it("Add another topic appends a row marked --added", () => {
    renderDialog();
    const initialRows = document.querySelectorAll(".pm-topic").length;
    const addBtn = document.querySelector<HTMLButtonElement>(".pm-add-topic")!;
    act(() => {
      addBtn.click();
    });
    const rowsNow = document.querySelectorAll(".pm-topic");
    expect(rowsNow.length).toBe(initialRows + 1);
    const last = rowsNow[rowsNow.length - 1];
    expect(last.classList.contains("pm-topic--added")).toBe(true);
  });

  it("drag a removed seed → draggable={false} (no-op visual contract)", () => {
    renderDialog();
    const firstRemove = document.querySelectorAll<HTMLButtonElement>(
      ".pm-topic__remove",
    )[0];
    act(() => {
      firstRemove.click();
    });
    const firstRow = document.querySelectorAll<HTMLLIElement>(".pm-topic")[0];
    expect(firstRow.draggable).toBe(false);
    expect(firstRow.classList.contains("pm-topic--removed")).toBe(true);
  });

  it("Decisions preview toggles chevron and list", () => {
    renderDialog();
    const toggle = document.querySelector<HTMLButtonElement>(".pm-block__toggle")!;
    expect(toggle.textContent).toContain("Show decision preview");
    expect(document.querySelector(".pm-decisions__full")).toBeNull();
    act(() => {
      toggle.click();
    });
    expect(toggle.textContent).toContain("Hide decision preview");
    expect(document.querySelector(".pm-decisions__full")).not.toBeNull();
  });

  it("Send-back panel: button visible but disabled with honest tooltip", () => {
    renderDialog();
    const sendbackToggle = document.querySelector<HTMLButtonElement>(
      ".pm-footer__sendback-toggle",
    )!;
    expect(document.querySelector(".pm-sendback")).toBeNull();
    act(() => {
      sendbackToggle.click();
    });
    expect(document.querySelector(".pm-sendback")).not.toBeNull();
    const submit = document.querySelector<HTMLButtonElement>(".pm-sendback__submit")!;
    expect(submit.disabled).toBe(true);
    expect(submit.getAttribute("aria-disabled")).toBe("true");
    expect(submit.getAttribute("title")).toBe("Available in next release");
  });

  it("Promote success calls api.promoteToProject and onPromoted, fires inboxgrade event", async () => {
    const spy = vi
      .spyOn(api, "promoteToProject")
      .mockResolvedValue({
        project: {
          project_id: "p_42",
          owner_user_id: "u",
          title: "Mobile login broken on iOS Safari",
          status: "ready",
          created_at: "",
          updated_at: "",
          icon: null,
          color: null,
          archived_at: null,
          deleted_at: null,
        } as never,
      });
    const promotedEvents: Array<unknown> = [];
    const promotedListener = (e: Event) =>
      promotedEvents.push((e as CustomEvent).detail);
    window.addEventListener(
      "inspira:feedback-item-promoted",
      promotedListener,
    );

    const { onPromoted } = renderDialog();
    const promoteBtn = document.querySelector<HTMLButtonElement>(
      ".pm-footer__promote",
    )!;
    await act(async () => {
      promoteBtn.click();
      await Promise.resolve();
      await Promise.resolve();
    });

    window.removeEventListener(
      "inspira:feedback-item-promoted",
      promotedListener,
    );

    expect(spy).toHaveBeenCalledTimes(1);
    expect(onPromoted).toHaveBeenCalledWith("p_42");
    expect(promotedEvents).toEqual([
      { itemId: "fi-1", projectId: "p_42" },
    ]);
  });

  it("Promote failure shows error inline; spawning unmounts; dialog stays open", async () => {
    vi.spyOn(api, "promoteToProject").mockRejectedValue(
      new Error("backend not wired"),
    );
    const { onClose, onPromoted } = renderDialog();
    const promoteBtn = document.querySelector<HTMLButtonElement>(
      ".pm-footer__promote",
    )!;
    await act(async () => {
      promoteBtn.click();
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(document.querySelector(".pm-spawning")).toBeNull();
    const errEl = document.querySelector(".pm-dialog__error");
    expect(errEl?.textContent).toContain("backend not wired");
    expect(onClose).not.toHaveBeenCalled();
    expect(onPromoted).not.toHaveBeenCalled();
  });

  it("Cancel button calls onClose without firing the API", () => {
    const promoteSpy = vi.spyOn(api, "promoteToProject");
    const { onClose, onPromoted } = renderDialog();
    const cancel = document.querySelector<HTMLButtonElement>(
      ".pm-footer__cancel",
    )!;
    act(() => {
      cancel.click();
    });
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(onPromoted).not.toHaveBeenCalled();
    expect(promoteSpy).not.toHaveBeenCalled();
  });
});
