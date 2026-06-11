# Security Headers

**Audience:** anyone touching `app/nginx.conf` or `services/planning_studio_service/api.py`.
**Scope:** what every security-related response header does, the rationale for the specific directive values Inspira ships, and the trade-offs we made to get there.
**Last updated:** 2026-04-20

Two layers emit security headers: nginx (for the HTML shell and static bundles) and the FastAPI middleware (for JSON API responses). They carry overlapping but non-identical sets because not every header makes sense on a JSON payload. Keep both files in sync with this doc when changing anything.

---

## 1. Content-Security-Policy (nginx only)

Emitted on the HTML shell. The API responses are JSON, so CSP has no rendering surface to protect there — we leave it off on the FastAPI side deliberately.

```
default-src 'self';
script-src 'self';
style-src 'self' 'unsafe-inline' https://fonts.googleapis.com;
font-src 'self' https://fonts.gstatic.com data:;
img-src 'self' data: blob:;
connect-src 'self' https://api.tryinspira.com http://127.0.0.1:4174 http://localhost:4174;
frame-ancestors 'none';
base-uri 'self';
form-action 'self';
object-src 'none';
upgrade-insecure-requests
```

Directive-by-directive rationale:

- **`default-src 'self'`** — default backstop. Anything without an explicit rule falls back to same-origin.
- **`script-src 'self'`** — no `'unsafe-inline'`, no `'unsafe-eval'`. Vite emits hashed bundle files under `/assets/`, never inline `<script>` tags. This is the single most important directive for defeating stored XSS; loosen it only with strong justification and a nonce/hash plan.
- **`style-src 'self' 'unsafe-inline' https://fonts.googleapis.com`** — **known compromise.** Vite inlines a small amount of critical CSS directly into the HTML shell, which forces `'unsafe-inline'`. This is the risk we accept for a signed-in app where the attack surface for CSS-based data exfiltration is narrow. To tighten this later: switch to `vite-plugin-csp` (or equivalent) that hashes inlined style blocks and emits `'sha256-…'` entries instead.
- **`font-src 'self' https://fonts.gstatic.com data:`** — Source Serif 4 is loaded from Google Fonts. `data:` covers inlined icon fonts and any fallback glyphs.
- **`img-src 'self' data: blob:`** — `data:` for inlined SVG icons; `blob:` for any client-side object URLs (file uploads rendered for preview).
- **`connect-src 'self' https://api.tryinspira.com http://127.0.0.1:4174 http://localhost:4174`** — dev + prod API origins. Add new third-party services (Sentry browser SDK, analytics if ever added) here explicitly.
- **`frame-ancestors 'none'`** — Inspira may never be embedded in an iframe. Full clickjacking defence.
- **`base-uri 'self'`** — prevents an injected `<base>` tag from re-rooting relative URLs to an attacker-controlled origin.
- **`form-action 'self'`** — forms may only submit back to our own origin.
- **`object-src 'none'`** — blocks `<object>`, `<embed>`, `<applet>`. Legacy XSS vectors.
- **`upgrade-insecure-requests`** — auto-upgrades any mixed HTTP subresource to HTTPS. Safe because our edge always terminates TLS; no plain-HTTP endpoints exist in production.

### A note on `'unsafe-inline'` in `style-src`

This is the only knowingly loose directive in the policy. The reasoning:

1. Vite's dev and prod builds both inline a small bundle of critical CSS into `index.html`.
2. Switching to hash-based or nonce-based CSS would mean either building a custom Vite plugin, or accepting a per-deploy build step that mutates `nginx.conf`.
3. The attack surface — CSS-based attribute-selector data exfiltration — is narrow for a signed-in product with no unauthenticated user-generated content.

Action if the team decides to tighten: track as a hardening task, plan a Vite plugin that emits a hash manifest, and template that manifest into the CSP at build time.

---

## 2. Strict-Transport-Security (both layers)

```
Strict-Transport-Security: max-age=31536000; includeSubDomains; preload
```

- **`max-age=31536000`** — one year. Once a browser sees this header, it refuses to speak plain HTTP to Inspira for that long.
- **`includeSubDomains`** — applies to `*.tryinspira.com`. If we ever stand up a staging subdomain on plain HTTP, requests will fail. That's the intended behaviour.
- **`preload`** — signals intent to join the HSTS preload list. **Commitment:** submitting to the preload list is effectively permanent. Once in, we cannot easily opt out — browsers ship with the list baked in, and removal takes many release cycles to propagate. Only submit after we're certain:
  - every current and future subdomain will always be HTTPS,
  - the cert chain is automated and reliable (Fly/Railway auto-renewal), and
  - we've run with `max-age=31536000` in the wild for at least a few weeks without issue.

**Gating:** nginx emits HSTS only when `$scheme = https`, so plain-HTTP local testing doesn't pin the browser. FastAPI middleware emits HSTS only when `ENVIRONMENT=production`, so uvicorn-against-localhost development doesn't either.

---

## 3. X-Content-Type-Options (both layers)

```
X-Content-Type-Options: nosniff
```

Forces the browser to respect our `Content-Type`. Blocks MIME sniffing — a class of attack where an attacker uploads a file that browsers interpret as HTML/script despite our intended type. Unconditional; no trade-off.

---

## 4. Referrer-Policy (both layers)

```
Referrer-Policy: strict-origin-when-cross-origin
```

- Same-origin requests: full URL in `Referer`.
- Cross-origin requests: only the origin (`https://app.tryinspira.com`), no path.
- Downgrade (HTTPS → HTTP) requests: no `Referer` at all.

Balances analytics/debug visibility with not leaking project titles or topic IDs to third parties a user might navigate to.

---

## 5. Permissions-Policy (both layers)

```
Permissions-Policy: accelerometer=(), camera=(), geolocation=(), microphone=(), payment=(), usb=()
```

Deny-everything baseline. Inspira uses none of these features today, so any request for them is a tell for either a third-party script behaving badly or a compromise.

**Forward path:** loosen individual entries when product needs them. Voice input is the most likely addition — change to `microphone=(self)` when that feature ships. Do not globally loosen.

---

## 6. X-Frame-Options (both layers)

```
X-Frame-Options: DENY
```

Legacy clickjacking defence. Redundant with CSP `frame-ancestors 'none'` for modern browsers but still honoured by older Safari and IE mode. Zero cost to keep; no reason to remove.

---

## 7. server_tokens off (nginx only)

Hides `Server: nginx/1.X.Y` from every response. Version disclosure is reconnaissance — one less detail an attacker has about our stack.

---

## 8. Caching (nginx only)

Not a security header per se, but paired with CSP in the same block because a wrong cache strategy can pin a compromised bundle in user browsers.

- `/assets/*` (Vite hashed bundles): `Cache-Control: public, max-age=31536000, immutable`. Safe because the filename changes when the content changes.
- `/index.html`: `Cache-Control: no-cache`. Forces revalidation on every navigation so users fetch the new shell (and new bundle hashes) as soon as a deploy lands. Without this, a compromised deploy could sit in stale HTML caches for days.

---

## 9. CORS vs security headers — load order

In FastAPI the middleware stack runs in the order middlewares are added. Order matters:

1. **CORS first.** Preflight (`OPTIONS`) responses are generated inside CORS and short-circuit before anything else runs. If the security middleware ran first, preflights would never pass through it — fine, because preflights don't need security headers, but they would also not see CORS-sensitive header additions.
2. **Security headers second.** Runs on every non-preflight response, including rate-limit 429s emitted by slowapi further down the stack.
3. **slowapi (rate limiting) third.** Emits its own 429 responses, which still flow through the security middleware on the way out.

Verification: `services/tests/test_api_fastapi.py` exercises every route; re-run after any middleware re-ordering.

---

## 10. Ops references

- `app/nginx.conf` — SPA headers + CSP + cache rules.
- `services/planning_studio_service/api.py` — `_SecurityHeadersMiddleware`.
- `docs/ops/hardening-checklist.md` — pre-launch verification steps.
