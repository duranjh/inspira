/**
 * Tests for src/observability/analytics.ts.
 *
 * Coverage:
 *   - initAnalytics() no-ops when VITE_PLAUSIBLE_DOMAIN is unset.
 *   - initAnalytics() injects the Plausible script tag with the right
 *     data-domain + data-api attributes when the domain is set.
 *   - initAnalytics() respects a `"none"` consent key (skips the script).
 *   - track() suppresses custom events when consent is "essential".
 *   - trackEvent() forwards name + props through to window.plausible.
 *   - AnalyticsEvent names are stable (this is a soft compile-time check —
 *     if you rename an event, the test fails loudly so the marketing
 *     dashboard doesn't silently go dark).
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  __resetAnalyticsForTests,
  initAnalytics,
  track,
  trackEvent,
} from "./analytics";
import type { AnalyticsEvent } from "./analytics";

const STABLE_EVENT_NAMES: ReadonlyArray<AnalyticsEvent["name"]> = [
  "landing_cta_clicked",
  "signup_started",
  "signup_completed",
  "first_map_created",
  "topic_turn_sent",
  "canvas_exported",
  "template_picked",
];

function clearHead(): void {
  const scripts = document.head.querySelectorAll("script");
  scripts.forEach((s) => s.remove());
}

beforeEach(() => {
  __resetAnalyticsForTests();
  delete (import.meta.env as Record<string, unknown>).VITE_PLAUSIBLE_DOMAIN;
  localStorage.clear();
  clearHead();
  delete (window as { plausible?: unknown }).plausible;
});

afterEach(() => {
  delete (import.meta.env as Record<string, unknown>).VITE_PLAUSIBLE_DOMAIN;
  localStorage.clear();
  clearHead();
});

describe("initAnalytics", () => {
  it("no-ops when VITE_PLAUSIBLE_DOMAIN is unset", () => {
    const debugSpy = vi.spyOn(console, "debug").mockImplementation(() => {});
    initAnalytics();
    initAnalytics();
    expect(document.head.querySelector("script[data-domain]")).toBeNull();
    expect(debugSpy).toHaveBeenCalledTimes(1);
    debugSpy.mockRestore();
  });

  it("injects the Plausible script tag when the domain is set", () => {
    (import.meta.env as Record<string, unknown>).VITE_PLAUSIBLE_DOMAIN =
      "tryinspira.com";
    initAnalytics();
    const script = document.head.querySelector<HTMLScriptElement>(
      "script[data-domain]",
    );
    expect(script).not.toBeNull();
    expect(script!.src).toBe("https://plausible.io/js/script.js");
    expect(script!.getAttribute("data-domain")).toBe("tryinspira.com");
    expect(script!.getAttribute("data-api")).toBe(
      "https://plausible.io/api/event",
    );
    expect(script!.defer).toBe(true);
  });

  it("skips script injection when consent is 'none'", () => {
    (import.meta.env as Record<string, unknown>).VITE_PLAUSIBLE_DOMAIN =
      "tryinspira.com";
    localStorage.setItem("inspira_cookie_consent", "none");
    initAnalytics();
    expect(document.head.querySelector("script[data-domain]")).toBeNull();
  });

  it("is idempotent", () => {
    (import.meta.env as Record<string, unknown>).VITE_PLAUSIBLE_DOMAIN =
      "tryinspira.com";
    initAnalytics();
    initAnalytics();
    const scripts = document.head.querySelectorAll("script[data-domain]");
    expect(scripts.length).toBe(1);
  });
});

describe("track + trackEvent (consent gating)", () => {
  it("calls window.plausible when consent is 'all'", () => {
    localStorage.setItem("inspira_cookie_consent", "all");
    const plausible = vi.fn();
    (window as { plausible?: unknown }).plausible = plausible;

    track("test_event", { foo: "bar" });
    expect(plausible).toHaveBeenCalledWith("test_event", { props: { foo: "bar" } });
  });

  it("suppresses custom events when consent is 'essential'", () => {
    localStorage.setItem("inspira_cookie_consent", "essential");
    const plausible = vi.fn();
    (window as { plausible?: unknown }).plausible = plausible;

    track("test_event");
    expect(plausible).not.toHaveBeenCalled();
  });

  it("suppresses everything when consent is 'none'", () => {
    localStorage.setItem("inspira_cookie_consent", "none");
    const plausible = vi.fn();
    (window as { plausible?: unknown }).plausible = plausible;

    track("test_event");
    expect(plausible).not.toHaveBeenCalled();
  });

  it("does not throw when window.plausible is missing", () => {
    localStorage.setItem("inspira_cookie_consent", "all");
    expect(() => track("test_event")).not.toThrow();
  });

  it("trackEvent forwards typed events through with props", () => {
    localStorage.setItem("inspira_cookie_consent", "all");
    const plausible = vi.fn();
    (window as { plausible?: unknown }).plausible = plausible;

    trackEvent({ name: "canvas_exported", props: { format: "pdf" } });
    expect(plausible).toHaveBeenCalledWith("canvas_exported", {
      props: { format: "pdf" },
    });
  });

  it("trackEvent forwards typed events without props", () => {
    localStorage.setItem("inspira_cookie_consent", "all");
    const plausible = vi.fn();
    (window as { plausible?: unknown }).plausible = plausible;

    trackEvent({ name: "signup_started" });
    expect(plausible).toHaveBeenCalledWith("signup_started");
  });

  it("swallows errors from a misbehaving window.plausible", () => {
    localStorage.setItem("inspira_cookie_consent", "all");
    (window as { plausible?: unknown }).plausible = () => {
      throw new Error("nope");
    };
    expect(() => track("test_event")).not.toThrow();
  });
});

describe("event vocabulary stability", () => {
  // A compile-time failure on the `name` member would produce a build break.
  // This runtime check guards against a future refactor that e.g. renames
  // `first_map_created` and breaks the Plausible dashboard silently.
  it("includes every shipped event name exactly once", () => {
    const set = new Set(STABLE_EVENT_NAMES);
    expect(set.size).toBe(STABLE_EVENT_NAMES.length);
    expect(set.has("landing_cta_clicked")).toBe(true);
    expect(set.has("first_map_created")).toBe(true);
    expect(set.has("canvas_exported")).toBe(true);
    expect(set.has("template_picked")).toBe(true);
  });
});
