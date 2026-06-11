import React, { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ChatSidebar } from "./ChatSidebar";
import type { ArtifactChatMessage } from "../api";

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

const NO_OP_SEND = async () => {};

describe("ChatSidebar", () => {
  it("renders messages with role-specific classes", () => {
    const messages: ArtifactChatMessage[] = [
      { role: "assistant", body: "Drafted the scaffold.", ts: "2026-05-03T10:00:00Z" },
      { role: "user", body: "Add a debounce.", ts: "2026-05-03T10:01:00Z" },
    ];
    act(() => {
      root.render(
        <ChatSidebar
          messages={messages}
          status="idle"
          thinkingLabel={null}
          onSend={NO_OP_SEND}
        />,
      );
    });
    expect(container.querySelectorAll(".av-chat__msg--ai").length).toBe(1);
    expect(container.querySelectorAll(".av-chat__msg--user").length).toBe(1);
  });

  it("shows the typing indicator when status is thinking", () => {
    act(() => {
      root.render(
        <ChatSidebar
          messages={[]}
          status="thinking"
          thinkingLabel="Sketching the file layout…"
          onSend={NO_OP_SEND}
        />,
      );
    });
    expect(container.querySelector(".av-chat__typing")).not.toBeNull();
    // Heartbeat label surfaces alongside the dots.
    const label = container.querySelector(".av-chat__thinking-label");
    expect(label?.textContent).toBe("Sketching the file layout…");
  });

  it("disables Send when input is empty (idle state)", () => {
    act(() => {
      root.render(
        <ChatSidebar
          messages={[]}
          status="idle"
          thinkingLabel={null}
          onSend={async () => {}}
        />,
      );
    });
    const sendBtn = container.querySelector<HTMLButtonElement>(
      ".av-chat__send",
    );
    expect(sendBtn?.disabled).toBe(true);
  });

  it("invokes onSend when controlled value drives the React onChange", () => {
    const onSend = vi.fn(async () => {});
    act(() => {
      root.render(
        <ChatSidebar
          messages={[]}
          status="idle"
          thinkingLabel={null}
          onSend={onSend}
        />,
      );
    });
    const input = container.querySelector<HTMLTextAreaElement>(
      ".av-chat__input",
    )!;

    // React controlled-component dance: use the prototype setter so
    // React's synthetic-event tracker picks up the value change.
    const setter = Object.getOwnPropertyDescriptor(
      window.HTMLTextAreaElement.prototype,
      "value",
    )!.set!;
    act(() => {
      setter.call(input, "Add a debounce");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });

    const sendBtn = container.querySelector<HTMLButtonElement>(
      ".av-chat__send",
    )!;
    expect(sendBtn.disabled).toBe(false);
    act(() => {
      sendBtn.click();
    });
    expect(onSend).toHaveBeenCalledTimes(1);
    expect(onSend).toHaveBeenCalledWith("Add a debounce");
  });

  it("renders inline backtick spans as <code> in assistant messages", () => {
    const messages: ArtifactChatMessage[] = [
      {
        role: "assistant",
        body: "I added a `100ms debounce` on the listener.",
        ts: "2026-05-03T10:00:00Z",
      },
    ];
    act(() => {
      root.render(
        <ChatSidebar
          messages={messages}
          status="idle"
          thinkingLabel={null}
          onSend={NO_OP_SEND}
        />,
      );
    });
    const code = container.querySelector(".av-chat__inline-code");
    expect(code).not.toBeNull();
    expect(code?.textContent).toBe("100ms debounce");
  });

  it("disables the input + send button while thinking", () => {
    act(() => {
      root.render(
        <ChatSidebar
          messages={[]}
          status="thinking"
          thinkingLabel={null}
          onSend={NO_OP_SEND}
        />,
      );
    });
    const input = container.querySelector<HTMLTextAreaElement>(
      ".av-chat__input",
    );
    const sendBtn = container.querySelector<HTMLButtonElement>(
      ".av-chat__send",
    );
    expect(input?.disabled).toBe(true);
    expect(sendBtn?.disabled).toBe(true);
  });
});
