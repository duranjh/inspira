// Pull an HTTP status code out of the error messages thrown by our fetch
// wrappers. They format as "GET /api/foo failed: 404" (see
// `features/inspira/api.ts`), so a regex against that suffix recovers the
// numeric code. Returns null for network errors, CORS blocks, or anything
// else that doesn't carry a status.
//
// Centralized here after PR 7 flagged 11 verbatim copies across the
// codebase (AuthPanel, account sections, billing api, feedback widget,
// etc.). Importing a single implementation means a server-response format
// change only has to land in one place.

export function parseStatus(err: unknown): number | null {
  if (!(err instanceof Error)) return null;
  const m = err.message.match(/failed:\s*(\d{3})\b/);
  return m ? Number(m[1]) : null;
}
