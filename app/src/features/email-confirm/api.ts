// Inspira — Email confirmation API client.
//
// Thin wrapper around the two verify-email backend routes. Typed errors
// let EmailConfirmPage distinguish "token is bad / expired" (400 / 410)
// from transient failures so the UI can pick the right next-state.

const DEFAULT_BASE_URL =
  (import.meta.env.VITE_INSPIRA_API_URL as string | undefined) ??
  "http://127.0.0.1:4174";

/** Thrown when the verification token is invalid, expired, or already
 *  consumed. UI should render the "send a new link" state. */
export class EmailTokenExpiredError extends Error {
  constructor(message = "email_token_expired") {
    super(message);
    this.name = "EmailTokenExpiredError";
  }
}

/** Thrown when the resend endpoint is rate-limited. UI surfaces a quiet
 *  "give it a minute" line; the inline throttle is per-user so honest
 *  retries wait briefly rather than hit it repeatedly. */
export class EmailResendThrottledError extends Error {
  constructor(public readonly retryAfterSeconds: number) {
    super("email_resend_throttled");
    this.name = "EmailResendThrottledError";
  }
}

export async function verifyEmail(token: string): Promise<void> {
  const path = `/api/auth/verify-email/${encodeURIComponent(token)}`;
  const res = await fetch(`${DEFAULT_BASE_URL}${path}`, {
    method: "POST",
    credentials: "include",
  });
  if (res.status === 400 || res.status === 410) {
    throw new EmailTokenExpiredError();
  }
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(
      `POST ${path} failed: ${res.status} ${res.statusText} — ${detail}`,
    );
  }
}

export async function resendVerification(): Promise<void> {
  const path = "/api/auth/verify-email/resend";
  const res = await fetch(`${DEFAULT_BASE_URL}${path}`, {
    method: "POST",
    credentials: "include",
  });
  if (res.status === 429) {
    const retry = Number(res.headers.get("retry-after") ?? "60");
    throw new EmailResendThrottledError(Number.isFinite(retry) ? retry : 60);
  }
  if (res.status === 401) {
    // Not signed in — the page falls back to "please sign in first".
    throw new Error("POST /api/auth/verify-email/resend failed: 401");
  }
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(
      `POST ${path} failed: ${res.status} ${res.statusText} — ${detail}`,
    );
  }
}
