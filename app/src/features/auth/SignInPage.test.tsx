import React, { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
  type MockInstance,
} from "vitest";

const navigateMock = vi.fn();
vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<typeof import("react-router-dom")>(
    "react-router-dom",
  );
  return {
    ...actual,
    useNavigate: () => navigateMock,
  };
});

vi.mock("../inspira/api", async () => {
  const actual = await vi.importActual<object>("../inspira/api");
  return {
    ...actual,
    api: {
      me: vi.fn(),
      login: vi.fn(),
      signup: vi.fn(),
    },
  };
});

import { api } from "../inspira/api";
import { SignInPage } from "./SignInPage";

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
  navigateMock.mockReset();
  (api.me as unknown as MockInstance).mockReset();
  (api.login as unknown as MockInstance).mockReset();
  (api.signup as unknown as MockInstance).mockReset();
});

afterEach(() => {
  act(() => {
    root.unmount();
  });
  container.remove();
});

function render(initialPath = "/") {
  return act(async () => {
    root.render(
      <MemoryRouter initialEntries={[initialPath]}>
        <SignInPage />
      </MemoryRouter>,
    );
  });
}

// React controlled-component dance: use the prototype setter so
// React's synthetic-event tracker picks up the value change. A naive
// `el.value = "..."` + dispatch("input") doesn't fire React's onChange.
function setInputValue(input: HTMLInputElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(
    window.HTMLInputElement.prototype,
    "value",
  )!.set!;
  setter.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}

describe("SignInPage", () => {
  it("renders the sign-in tab as active by default", async () => {
    await render("/");
    const tabs = container.querySelectorAll<HTMLButtonElement>(
      ".signin-tab",
    );
    expect(tabs.length).toBe(2);
    expect(tabs[0].getAttribute("aria-selected")).toBe("true");
    expect(tabs[1].getAttribute("aria-selected")).toBe("false");
  });

  it("?signup=1 presets the Sign-up tab (marketing-page deep-link compat)", async () => {
    await render("/?signup=1");
    const tabs = container.querySelectorAll<HTMLButtonElement>(
      ".signin-tab",
    );
    expect(tabs[0].getAttribute("aria-selected")).toBe("false");
    expect(tabs[1].getAttribute("aria-selected")).toBe("true");
    // Confirm-password + display-name fields appear in signup mode.
    const confirm = Array.from(
      container.querySelectorAll<HTMLLabelElement>(".signin-field"),
    ).find((el) => el.textContent?.includes("Confirm password"));
    expect(confirm).toBeDefined();
  });

  it("?signin=1 presets the Sign-in tab", async () => {
    await render("/?signin=1");
    const tabs = container.querySelectorAll<HTMLButtonElement>(
      ".signin-tab",
    );
    expect(tabs[0].getAttribute("aria-selected")).toBe("true");
  });

  it("on successful login with default_workspace_id navigates to /workspaces", async () => {
    (api.login as unknown as MockInstance).mockResolvedValue({
      user_id: "u-1",
      email: "p@x.com",
      display_name: "P",
      is_system: false,
      default_workspace_id: "ws-1",
    });
    (api.me as unknown as MockInstance).mockResolvedValue({
      user_id: "u-1",
      email: "p@x.com",
      display_name: "P",
      is_system: false,
      default_workspace_id: "ws-1",
    });
    await render("/");

    const emailInput = container.querySelector<HTMLInputElement>(
      'input[type="email"]',
    )!;
    const passwordInput = container.querySelector<HTMLInputElement>(
      'input[type="password"]',
    )!;
    await act(async () => {
      setInputValue(emailInput, "p@x.com");
      setInputValue(passwordInput, "longenough123");
    });

    const form = container.querySelector("form")!;
    await act(async () => {
      form.dispatchEvent(
        new Event("submit", { bubbles: true, cancelable: true }),
      );
    });
    // Resolve the chained promises (login -> me -> navigate).
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(api.login).toHaveBeenCalledWith({
      email: "p@x.com",
      password: "longenough123",
    });
    expect(navigateMock).toHaveBeenCalledWith("/workspaces", { replace: true });
  });

  it("on signup with no default_workspace_id navigates to /onboarding", async () => {
    (api.signup as unknown as MockInstance).mockResolvedValue({
      user_id: "u-2",
      email: "n@x.com",
      display_name: "",
      is_system: false,
      default_workspace_id: null,
    });
    (api.me as unknown as MockInstance).mockResolvedValue({
      user_id: "u-2",
      email: "n@x.com",
      display_name: "",
      is_system: false,
      default_workspace_id: null,
    });
    await render("/?signup=1");

    const emailInput = container.querySelector<HTMLInputElement>(
      'input[type="email"]',
    )!;
    const passwordInputs =
      container.querySelectorAll<HTMLInputElement>('input[type="password"]');
    const checkbox = container.querySelector<HTMLInputElement>(
      'input[type="checkbox"]',
    )!;

    await act(async () => {
      setInputValue(emailInput, "n@x.com");
      setInputValue(passwordInputs[0], "longenough123");
      setInputValue(passwordInputs[1], "longenough123");
      const setter = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype,
        "checked",
      )!.set!;
      setter.call(checkbox, true);
      checkbox.dispatchEvent(new Event("click", { bubbles: true }));
    });

    const form = container.querySelector("form")!;
    await act(async () => {
      form.dispatchEvent(
        new Event("submit", { bubbles: true, cancelable: true }),
      );
    });
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(api.signup).toHaveBeenCalled();
    expect(navigateMock).toHaveBeenCalledWith("/onboarding", { replace: true });
  });

  it("surfaces invalid_credentials on 401 login error", async () => {
    const err = new Error(
      "POST /api/auth/login failed: 401 Unauthorized — invalid",
    );
    (api.login as unknown as MockInstance).mockRejectedValue(err);
    await render("/");

    const emailInput = container.querySelector<HTMLInputElement>(
      'input[type="email"]',
    )!;
    const passwordInput = container.querySelector<HTMLInputElement>(
      'input[type="password"]',
    )!;
    await act(async () => {
      setInputValue(emailInput, "wrong@x.com");
      setInputValue(passwordInput, "longenough123");
    });
    const form = container.querySelector("form")!;
    await act(async () => {
      form.dispatchEvent(
        new Event("submit", { bubbles: true, cancelable: true }),
      );
    });
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    const error = container.querySelector(".signin-error");
    expect(error?.textContent).toContain("Email or password");
    expect(navigateMock).not.toHaveBeenCalled();
  });
});
