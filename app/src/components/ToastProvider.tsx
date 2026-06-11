/**
 * Inspira — ToastProvider + useToast + module-level `toast` singleton.
 *
 * Toasts are passive notifications for async-operation outcomes. They appear
 * in a stack at the bottom-right of the viewport, auto-dismiss after a short
 * delay, and are dismissible by click. The visual language is warm-editorial:
 * a small cream paper card with a left accent border, a quiet shadow, and a
 * subtle fade/slide entrance. No bright browser-alert colors.
 *
 * Public API:
 *   <ToastProvider> ... </ToastProvider>
 *       Renders children plus a fixed container for the toast stack.
 *
 *   useToast(): ToastApi
 *       Hook returning { toast, success, error, warning, info }.
 *
 *   toast (module-level singleton): ToastApi
 *       Same surface, usable from non-hook contexts (e.g. api.ts top-level
 *       callbacks). Backed by a pub-sub: calls dispatch into a shared
 *       emitter; the mounted provider subscribes and renders them.
 *
 *   type ToastOptions, ToastVariant — for explicit typing in callers.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactElement,
  type ReactNode,
} from "react";
import "./Toast.css";

import { t as i18nT } from "../i18n";

/* ---------------------------------------------------------------------------
 * Types
 * ------------------------------------------------------------------------- */

export type ToastVariant = "info" | "success" | "warning" | "error";

export interface ToastOptions {
  /** Main line. If title is omitted, message is rendered alone. */
  message: string;
  /** Optional short title above the message. */
  title?: string;
  /** Visual variant. Defaults to "info". */
  variant?: ToastVariant;
  /** Auto-dismiss delay in ms. Default 4000. Pass 0 to disable auto-dismiss. */
  durationMs?: number;
  /** Optional inline action — typically "Undo". When clicked, calls
   *  ``onAction`` then dismisses the toast. Skipped if either field is
   *  missing. */
  actionLabel?: string;
  onAction?: () => void;
}

export interface ToastApi {
  /** Generic toast call — pass options or a plain string (treated as info message). */
  toast: (opts: ToastOptions | string) => string;
  info: (message: string, opts?: Omit<ToastOptions, "message" | "variant">) => string;
  success: (message: string, opts?: Omit<ToastOptions, "message" | "variant">) => string;
  warning: (message: string, opts?: Omit<ToastOptions, "message" | "variant">) => string;
  error: (message: string, opts?: Omit<ToastOptions, "message" | "variant">) => string;
  /** Dismiss a specific toast early by the id returned from the helpers. */
  dismiss: (id: string) => void;
}

interface ToastRecord {
  id: string;
  message: string;
  title?: string;
  variant: ToastVariant;
  durationMs: number;
  actionLabel?: string;
  onAction?: () => void;
  leaving?: boolean;
}

/* ---------------------------------------------------------------------------
 * Singleton event emitter — enables the module-level `toast` object to work
 * from non-React contexts. The provider subscribes on mount; calls made
 * before a provider exists are buffered and delivered once one subscribes.
 * ------------------------------------------------------------------------- */

type Dispatch =
  | { kind: "show"; opts: ToastOptions; id: string }
  | { kind: "dismiss"; id: string };

type Listener = (d: Dispatch) => void;

class ToastEmitter {
  private listener: Listener | null = null;
  private buffer: Dispatch[] = [];

  subscribe(listener: Listener): () => void {
    this.listener = listener;
    // Flush any buffered dispatches that arrived before a provider mounted.
    if (this.buffer.length) {
      const flush = this.buffer;
      this.buffer = [];
      for (const d of flush) listener(d);
    }
    return () => {
      if (this.listener === listener) this.listener = null;
    };
  }

  dispatch(d: Dispatch): void {
    if (this.listener) this.listener(d);
    else this.buffer.push(d);
  }
}

const emitter = new ToastEmitter();

let toastIdSeq = 0;
const nextId = (): string => {
  toastIdSeq += 1;
  return `t${toastIdSeq}_${Date.now().toString(36)}`;
};

/* ---------------------------------------------------------------------------
 * Helpers shared between the hook API and the module-level singleton.
 * ------------------------------------------------------------------------- */

const normalize = (opts: ToastOptions | string): ToastOptions =>
  typeof opts === "string" ? { message: opts } : opts;

const makeApi = (
  show: (opts: ToastOptions) => string,
  dismiss: (id: string) => void,
): ToastApi => ({
  toast: (opts) => show(normalize(opts)),
  info: (message, opts) => show({ ...(opts ?? {}), message, variant: "info" }),
  success: (message, opts) =>
    show({ ...(opts ?? {}), message, variant: "success" }),
  warning: (message, opts) =>
    show({ ...(opts ?? {}), message, variant: "warning" }),
  error: (message, opts) =>
    show({ ...(opts ?? {}), message, variant: "error" }),
  dismiss,
});

/**
 * Module-level singleton. Works anywhere (including non-React contexts like
 * api.ts top-level), because it dispatches into the shared emitter which
 * the mounted ToastProvider subscribes to.
 */
export const toast: ToastApi = makeApi(
  (opts) => {
    const id = nextId();
    emitter.dispatch({ kind: "show", opts, id });
    return id;
  },
  (id) => emitter.dispatch({ kind: "dismiss", id }),
);

/* ---------------------------------------------------------------------------
 * React context + hook
 * ------------------------------------------------------------------------- */

const ToastContext = createContext<ToastApi | null>(null);

export function useToast(): ToastApi {
  const ctx = useContext(ToastContext);
  if (!ctx) {
    // Fallback to the module-level singleton so hook callers never crash
    // when something is rendered outside the provider (e.g. storybook).
    return toast;
  }
  return ctx;
}

/* ---------------------------------------------------------------------------
 * Provider
 * ------------------------------------------------------------------------- */

const DEFAULT_DURATION = 4000;
const LEAVE_MS = 180;

const variantGlyph = (v: ToastVariant): string => {
  switch (v) {
    case "success":
      return "\u2713"; // check
    case "error":
      return "!";
    case "warning":
      return "!";
    case "info":
    default:
      return "i";
  }
};

const variantRole = (v: ToastVariant): "status" | "alert" =>
  v === "error" || v === "warning" ? "alert" : "status";

const variantAriaLive = (v: ToastVariant): "polite" | "assertive" =>
  v === "error" || v === "warning" ? "assertive" : "polite";

export interface ToastProviderProps {
  children: ReactNode;
  /** Default auto-dismiss in ms. Overridden per-toast via options. */
  defaultDurationMs?: number;
}

export function ToastProvider({
  children,
  defaultDurationMs = DEFAULT_DURATION,
}: ToastProviderProps): ReactElement {
  const [toasts, setToasts] = useState<ToastRecord[]>([]);
  // Track pending timers so we can cancel on unmount or manual dismiss.
  const timersRef = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map());

  const clearTimer = useCallback((id: string) => {
    const t = timersRef.current.get(id);
    if (t !== undefined) {
      clearTimeout(t);
      timersRef.current.delete(id);
    }
  }, []);

  const remove = useCallback(
    (id: string) => {
      clearTimer(id);
      setToasts((xs) => xs.filter((t) => t.id !== id));
    },
    [clearTimer],
  );

  const beginLeave = useCallback(
    (id: string) => {
      clearTimer(id);
      setToasts((xs) =>
        xs.map((t) => (t.id === id ? { ...t, leaving: true } : t)),
      );
      const leaveTimer = setTimeout(() => remove(id), LEAVE_MS);
      timersRef.current.set(id, leaveTimer);
    },
    [clearTimer, remove],
  );

  const show = useCallback(
    (opts: ToastOptions, forcedId?: string): string => {
      const id = forcedId ?? nextId();
      const variant: ToastVariant = opts.variant ?? "info";
      const durationMs =
        opts.durationMs !== undefined ? opts.durationMs : defaultDurationMs;
      const record: ToastRecord = {
        id,
        message: opts.message,
        title: opts.title,
        variant,
        durationMs,
        actionLabel: opts.actionLabel,
        onAction: opts.onAction,
      };
      setToasts((xs) => [...xs, record]);
      if (durationMs > 0) {
        const timer = setTimeout(() => beginLeave(id), durationMs);
        timersRef.current.set(id, timer);
      }
      return id;
    },
    [beginLeave, defaultDurationMs],
  );

  // Subscribe to the module-level emitter so `toast.error(...)` outside of
  // React lands in this provider's state.
  useEffect(() => {
    const unsubscribe = emitter.subscribe((d) => {
      if (d.kind === "show") show(d.opts, d.id);
      else beginLeave(d.id);
    });
    return unsubscribe;
  }, [show, beginLeave]);

  // Cleanup timers on unmount.
  useEffect(() => {
    const timers = timersRef.current;
    return () => {
      timers.forEach((t) => clearTimeout(t));
      timers.clear();
    };
  }, []);

  const api = useMemo<ToastApi>(
    () =>
      makeApi(
        (opts) => show(opts),
        (id) => beginLeave(id),
      ),
    [show, beginLeave],
  );

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div
        className="inspira-toast-stack"
        aria-live="polite"
        aria-relevant="additions"
      >
        {toasts.map((t) => (
          <div
            key={t.id}
            className={
              "inspira-toast inspira-toast--" +
              t.variant +
              (t.leaving ? " inspira-toast--leaving" : "")
            }
            role={variantRole(t.variant)}
            aria-live={variantAriaLive(t.variant)}
            onClick={() => beginLeave(t.id)}
          >
            <span className="inspira-toast__glyph" aria-hidden="true">
              {variantGlyph(t.variant)}
            </span>
            <div className="inspira-toast__body">
              {t.title ? (
                <p className="inspira-toast__title">{t.title}</p>
              ) : null}
              <div className="inspira-toast__message">{t.message}</div>
            </div>
            {t.actionLabel && t.onAction ? (
              <button
                type="button"
                className="inspira-toast__action"
                onClick={(e) => {
                  e.stopPropagation();
                  // Snapshot the handler so a stale toast record can't
                  // double-fire after dismiss.
                  const fn = t.onAction;
                  beginLeave(t.id);
                  if (fn) fn();
                }}
              >
                {t.actionLabel}
              </button>
            ) : null}
            <button
              type="button"
              className="inspira-toast__close"
              aria-label={i18nT("toast.dismiss_aria")}
              onClick={(e) => {
                e.stopPropagation();
                beginLeave(t.id);
              }}
            >
              {"\u00D7"}
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}
