/**
 * Tests for src/observability/sentry.ts.
 *
 * Coverage:
 *   - initSentry() no-ops when VITE_SENTRY_DSN is unset (dev build).
 *   - initSentry() wires Sentry.init with the expected sampling + release
 *     when the DSN is present.
 *   - sentryBeforeSend() drops the three well-known noisy errors and
 *     keeps real ones.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// We mock @sentry/react so Sentry.init doesn't try to reach the DSN.
vi.mock("@sentry/react", () => {
  const init = vi.fn();
  const browserTracingIntegration = vi.fn(() => ({ name: "BrowserTracing" }));
  const replayIntegration = vi.fn(() => ({ name: "Replay" }));
  const ErrorBoundary = ({
    children,
  }: {
    children?: unknown;
    fallback?: unknown;
  }) => children;
  return {
    init,
    browserTracingIntegration,
    replayIntegration,
    ErrorBoundary,
  };
});

import * as SentrySDK from "@sentry/react";
import {
  SENTRY_NOISE_PATTERNS,
  __resetSentryForTests,
  initSentry,
  sentryBeforeSend,
} from "./sentry";

const sentryInitSpy = SentrySDK.init as unknown as ReturnType<typeof vi.fn>;

beforeEach(() => {
  __resetSentryForTests();
  sentryInitSpy.mockReset();
  // Reset the VITE_* env overrides we set per test.
  // vitest exposes import.meta.env as a mutable object so we can reassign.
  delete (import.meta.env as Record<string, unknown>).VITE_SENTRY_DSN;
  delete (import.meta.env as Record<string, unknown>).VITE_RELEASE;
});

afterEach(() => {
  delete (import.meta.env as Record<string, unknown>).VITE_SENTRY_DSN;
  delete (import.meta.env as Record<string, unknown>).VITE_RELEASE;
});

describe("initSentry", () => {
  it("no-ops and logs a single debug line when no DSN is set", () => {
    const debugSpy = vi.spyOn(console, "debug").mockImplementation(() => {});
    initSentry();
    initSentry(); // second call should not re-warn
    expect(sentryInitSpy).not.toHaveBeenCalled();
    expect(debugSpy).toHaveBeenCalledTimes(1);
    debugSpy.mockRestore();
  });

  it("calls Sentry.init with DSN, release, and the configured sampling", () => {
    (import.meta.env as Record<string, unknown>).VITE_SENTRY_DSN =
      "https://abc@o0.ingest.sentry.io/12345";
    (import.meta.env as Record<string, unknown>).VITE_RELEASE = "abc1234";

    initSentry();

    expect(sentryInitSpy).toHaveBeenCalledTimes(1);
    const options = sentryInitSpy.mock.calls[0]![0] as Record<string, unknown>;
    expect(options.dsn).toBe("https://abc@o0.ingest.sentry.io/12345");
    expect(options.release).toBe("abc1234");
    expect(options.tracesSampleRate).toBe(0.1);
    expect(options.replaysSessionSampleRate).toBe(0.0);
    expect(options.replaysOnErrorSampleRate).toBe(1.0);
    expect(typeof options.beforeSend).toBe("function");
  });

  it("falls back to release='dev' when VITE_RELEASE is missing", () => {
    (import.meta.env as Record<string, unknown>).VITE_SENTRY_DSN =
      "https://abc@o0.ingest.sentry.io/12345";
    initSentry();
    const options = sentryInitSpy.mock.calls[0]![0] as Record<string, unknown>;
    expect(options.release).toBe("dev");
  });

  it("is idempotent — a second call does not re-init", () => {
    (import.meta.env as Record<string, unknown>).VITE_SENTRY_DSN =
      "https://abc@o0.ingest.sentry.io/12345";
    initSentry();
    initSentry();
    expect(sentryInitSpy).toHaveBeenCalledTimes(1);
  });
});

describe("sentryBeforeSend", () => {
  const makeEvent = (message: string) =>
    ({
      message,
      exception: { values: [{ value: message }] },
    }) as unknown as Parameters<typeof sentryBeforeSend>[0];

  const hint = (err?: Error) =>
    ({ originalException: err }) as unknown as Parameters<
      typeof sentryBeforeSend
    >[1];

  it("drops ResizeObserver loop errors", () => {
    const event = makeEvent("ResizeObserver loop limit exceeded");
    expect(
      sentryBeforeSend(event, hint(new Error("ResizeObserver loop limit exceeded"))),
    ).toBeNull();
  });

  it("drops ChunkLoadError", () => {
    const event = makeEvent("ChunkLoadError: Loading chunk 3 failed.");
    expect(
      sentryBeforeSend(event, hint(new Error("ChunkLoadError: Loading chunk 3 failed."))),
    ).toBeNull();
  });

  it("drops 'Failed to fetch' network errors", () => {
    const event = makeEvent("TypeError: Failed to fetch");
    expect(
      sentryBeforeSend(event, hint(new Error("TypeError: Failed to fetch"))),
    ).toBeNull();
  });

  it("keeps real errors", () => {
    const event = makeEvent("Cannot read properties of undefined (reading 'x')");
    const result = sentryBeforeSend(
      event,
      hint(new Error("Cannot read properties of undefined (reading 'x')")),
    );
    expect(result).toBe(event);
  });

  it("falls back to event.message when originalException is not an Error", () => {
    const event = makeEvent("ChunkLoadError via exception values");
    // No originalException on hint — force the fallback path.
    expect(sentryBeforeSend(event, hint())).toBeNull();
  });

  it("exports the noise pattern list for external consumers", () => {
    expect(SENTRY_NOISE_PATTERNS.length).toBe(3);
    expect(SENTRY_NOISE_PATTERNS.every((p) => p instanceof RegExp)).toBe(true);
  });
});
