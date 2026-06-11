// Source utilities — helpers for turning URLs and pasted blocks of text
// into AttachedSource objects that ride alongside user turns.
//
// Today we support three AttachedSource kinds:
//   - file:*       — populated in TopicDetail / ProjectCanvas file pickers
//   - url:link     — fetched page text, produced by fetchUrlAsSource below
//   - text:paste   — large pasted blocks, produced by textAsSource below
//
// fetchUrlAsSource posts the URL to the backend proxy at
// `/api/v2/fetch-url`, which runs the fetch server-side with SSRF
// guards, size caps, and content-type allowlists (see
// services/planning_studio_service/fetchers/url.py). Only when that
// endpoint is not yet deployed (404) do we fall back to the legacy
// in-browser fetch path — which CORS will block for most sites, but
// the metadata-only attachment is still useful.
//
// No emojis, no new deps — the HTML parsing is done with the native
// DOMParser.

import { toast } from "../../components/ToastProvider";
import { api, type AttachedSource } from "./api";

// Same 8000-char cap files use. Keeps the planner payload bounded and
// roughly matches the file:* excerpt length so prompts stay consistent
// regardless of which source the user attached from.
const MAX_EXCERPT_CHARS = 8000;
const URL_DISPLAY_NAME_CAP = 40;

// Loose URL matcher — matches http(s) schemes only. We don't want to
// treat random tokens like "file.txt" as URLs, and we don't support
// mailto/ftp/etc. since we can't meaningfully fetch those.
const URL_PATTERN = /\bhttps?:\/\/[^\s<>"')]+/gi;

/**
 * Extract any URLs embedded in a block of text. Trims trailing sentence
 * punctuation that's almost never part of the URL itself (., ,, ;, ), !, ?).
 */
export function detectUrlInInput(text: string): string[] {
  const matches = text.match(URL_PATTERN);
  if (!matches) return [];
  const cleaned: string[] = [];
  for (const raw of matches) {
    const trimmed = raw.replace(/[.,;!?)\]]+$/g, "");
    if (trimmed.length > 0) cleaned.push(trimmed);
  }
  return cleaned;
}

/**
 * Fetch a URL and return an AttachedSource carrying its visible text.
 *
 * Routes through the backend proxy at `/api/v2/fetch-url` so the fetch
 * isn't subject to browser CORS. Server-side safety guards live in
 * services/planning_studio_service/fetchers/url.py.
 *
 * Fallback policy:
 *   - 404 from the proxy — likely running against an older backend that
 *     hasn't deployed the endpoint yet. Fall back to the in-browser
 *     fetch path so pre-deploy builds keep working.
 *   - Any other error — surface via a toast so the user knows the
 *     attachment didn't land; return a metadata-only source so the URL
 *     still reaches the planner as context.
 *
 * The returned excerpt is prefixed with the URL itself so the LLM has
 * grounding for what it's reading — otherwise an excerpt without context
 * looks indistinguishable from a pasted snippet.
 */
export async function fetchUrlAsSource(
  url: string,
): Promise<AttachedSource> {
  const displayName = shortenUrlForDisplay(url);

  try {
    const source = await api.fetchUrl(url);
    return source;
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err ?? "");
    // postJson encodes the status as `"POST /path failed: 404 Not Found — ..."`.
    // Detect the 404 branch and fall through to the legacy in-browser
    // path so pre-deploy builds keep working.
    if (/\b404\b/.test(message)) {
      return fetchUrlInBrowser(url, displayName);
    }
    // Any other backend-reported error — user-facing toast + metadata-only
    // source. Surfacing it (rather than silently degrading) is important
    // because the user explicitly asked to attach this URL.
    const friendlyReason = extractFriendlyReason(message);
    toast.error(`Couldn't attach ${displayName}: ${friendlyReason}`);
    console.warn("[Inspira] url proxy fetch failed", url, err);
    return metadataOnlySource(url, displayName, friendlyReason);
  }
}

/**
 * Legacy in-browser fetch path. Only used when the backend proxy is
 * absent (404). CORS blocks most real-world sites from this path; the
 * metadata-only fallback at the bottom keeps the URL itself useful
 * even when we can't inline the body.
 */
async function fetchUrlInBrowser(
  url: string,
  displayName: string,
): Promise<AttachedSource> {
  try {
    const res = await fetch(url, {
      method: "GET",
      // Intentionally default to CORS-mode: if the server doesn't send
      // Access-Control-Allow-Origin we want to fall through to the
      // metadata-only branch below rather than silently opaque-response.
      credentials: "omit",
      redirect: "follow",
    });
    if (!res.ok) {
      return metadataOnlySource(url, displayName, `HTTP ${res.status}`);
    }
    const contentType = res.headers.get("content-type") ?? "";
    const responseText = await res.text();

    let body: string;
    if (contentType.includes("text/html") || /<html[\s>]/i.test(responseText)) {
      body = extractVisibleText(responseText);
    } else {
      // JSON / plain text / etc. — use as-is, just normalize whitespace.
      body = normalizeWhitespace(responseText);
    }

    const truncated = truncateWithMarker(body, MAX_EXCERPT_CHARS);
    return {
      display_name: displayName,
      kind: "url:link",
      excerpt: `URL: ${url}\n\n${truncated}`,
    };
  } catch (err) {
    // Most commonly a TypeError for CORS / network failure. Log and fall
    // back; we don't surface the error to the planner because the URL
    // itself is still useful context.
    console.warn("[Inspira] in-browser url fetch failed", url, err);
    return metadataOnlySource(url, displayName, "CORS");
  }
}

/**
 * Pull a readable reason out of the raw error message produced by
 * postJson. Typical shape is `"POST /path failed: 400 Bad Request — {\"error\":\"invalid_url\"}"`.
 * We try to surface the short error code (e.g. `invalid_url`) when
 * present; otherwise fall back to the status word.
 */
function extractFriendlyReason(message: string): string {
  // Look for a JSON body with an `error` field.
  const errorMatch = message.match(/"error"\s*:\s*"([^"]+)"/);
  if (errorMatch && errorMatch[1]) {
    return errorMatch[1].replace(/_/g, " ");
  }
  // Fall back to the HTTP status phrase between `:` and `—`.
  const statusMatch = message.match(/failed:\s*(\d{3}[^—]*)/);
  if (statusMatch && statusMatch[1]) {
    return statusMatch[1].trim();
  }
  return "fetch failed";
}

/**
 * Wrap a block of pasted text as an AttachedSource. Use this when the
 * pasted content is big enough that cramming it into the composer textarea
 * would be awkward (the "offer as attachment" path in both composers).
 *
 * `displayHint` is an optional override for the display name; otherwise
 * we fall back to "Pasted text (N chars)".
 */
export function textAsSource(
  text: string,
  displayHint?: string,
): AttachedSource {
  const normalized = text ?? "";
  const display = displayHint ?? `Pasted text (${normalized.length} chars)`;
  return {
    display_name: display,
    kind: "text:paste",
    excerpt: normalized.slice(0, MAX_EXCERPT_CHARS),
  };
}

// -- helpers --------------------------------------------------------------

function metadataOnlySource(
  url: string,
  displayName: string,
  reason: string,
): AttachedSource {
  return {
    display_name: displayName,
    kind: "url:link",
    excerpt:
      `URL: ${url}\n\n` +
      `[fetched content not available (${reason}); the URL was attached for reference]`,
  };
}

/**
 * Strip <script>/<style> nodes, read the body text via DOMParser, and
 * normalize whitespace. DOMParser understands malformed HTML gracefully —
 * no need to pre-sanitize input.
 */
function extractVisibleText(html: string): string {
  try {
    const doc = new DOMParser().parseFromString(html, "text/html");
    // Remove non-visible chrome before reading textContent.
    doc
      .querySelectorAll("script, style, noscript, template, svg")
      .forEach((el) => el.remove());
    const body = doc.body ?? doc.documentElement;
    const raw = body ? body.textContent ?? "" : "";
    return normalizeWhitespace(raw);
  } catch {
    // Very unusual — fall back to a regex strip.
    const stripped = html
      .replace(/<script[\s\S]*?<\/script>/gi, "")
      .replace(/<style[\s\S]*?<\/style>/gi, "")
      .replace(/<[^>]+>/g, " ");
    return normalizeWhitespace(stripped);
  }
}

function normalizeWhitespace(s: string): string {
  return s.replace(/\s+/g, " ").trim();
}

function truncateWithMarker(s: string, cap: number): string {
  if (s.length <= cap) return s;
  return s.slice(0, cap) + "\n\n[...truncated]";
}

/**
 * Render a URL as a compact chip label — host + a slice of the path,
 * capped so long analytics-heavy URLs don't blow out the composer.
 */
function shortenUrlForDisplay(url: string): string {
  if (url.length <= URL_DISPLAY_NAME_CAP) return url;
  try {
    const parsed = new URL(url);
    const hostAndPath = parsed.host + parsed.pathname;
    if (hostAndPath.length <= URL_DISPLAY_NAME_CAP) return hostAndPath;
    return hostAndPath.slice(0, URL_DISPLAY_NAME_CAP - 1) + "…";
  } catch {
    return url.slice(0, URL_DISPLAY_NAME_CAP - 1) + "…";
  }
}
