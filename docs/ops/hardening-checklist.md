# Pre-Launch Hardening Checklist

**Audience:** whoever is shipping the first public deploy of Inspira.
**Scope:** security + ops verification steps to run before DNS points at the new environment. Sibling docs: `docs/ops/security-headers.md`, `docs/ops/runbook.md`, `docs/ops/incident-response.md`.
**Last updated:** 2026-04-20

Walk through this list in order. Don't skip items — an audit failure at item 12 is cheap; a leaked stack trace on day one of public traffic is not.

---

## 1. Environment variables

- [ ] `INSPIRA_SESSION_SECRET` — generated fresh per environment, 48+ bytes of entropy. `python -c 'import secrets; print(secrets.token_urlsafe(48))'`. **Not** committed to the repo; set via the platform's secret store (Fly, Railway, Doppler, etc.).
- [ ] `INSPIRA_COOKIE_SECURE=true` — session cookies only transmitted over HTTPS.
- [ ] `INSPIRA_ALLOWED_ORIGINS=https://app.tryinspira.com` (plus `https://www.tryinspira.com` if the marketing site calls the API). Comma-separated, no trailing slashes.
- [ ] `OPENAI_API_KEY` — live key, scoped to a project with spend cap enabled. Never checked in.
- [ ] `SENTRY_DSN` — production DSN for the production Sentry project.
- [ ] `ENVIRONMENT=production` — this flag trips `_assert_production_safe()` in `api.py`, which refuses to boot with any of the above missing. If startup fails, read the exception message; it names the missing var.
- [ ] `INSPIRA_RATE_LIMIT` — default `120/minute` is fine for launch. Tune upward only after observing real traffic.
- [ ] `INSPIRA_USER_DAILY_TOKEN_BUDGET` — default `200000` keeps a single rogue session from bleeding the OpenAI bill dry. Leave alone unless you have a concrete reason.

---

## 2. TLS + certificates

- [ ] HTTPS cert issued for the apex + API subdomains. Fly and Railway auto-issue; verify in the platform dashboard.
- [ ] Full chain serves cleanly — run `openssl s_client -servername app.tryinspira.com -connect app.tryinspira.com:443 -showcerts` and confirm no warnings.
- [ ] Cert renewal is automated. Note the renewal date in the runbook.
- [ ] `http://…` redirects to `https://…` with a 301 (platform-level, usually default).

---

## 3. Security headers verified in the browser

- [ ] Load the production site in Chrome DevTools → Security panel. Confirm "Connection is secure".
- [ ] DevTools → Network tab → click `/` request → Headers tab. Verify presence of `Content-Security-Policy`, `Strict-Transport-Security`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: strict-origin-when-cross-origin`, `Permissions-Policy: …`, `X-Frame-Options: DENY`.
- [ ] No CSP violations in DevTools → Console. If violations appear, either fix the source (preferred) or widen the policy (documented trade-off only).
- [ ] Same checks on an API request (e.g. `/api/health`). The API carries a subset — `X-Content-Type-Options`, `Referrer-Policy`, `X-Frame-Options`, `Permissions-Policy`, `Strict-Transport-Security`. CSP is intentionally absent on JSON.

---

## 4. HSTS

- [ ] `curl -I https://app.tryinspira.com | grep -i strict-transport` returns `max-age=31536000; includeSubDomains; preload`.
- [ ] Same for the API origin.
- [ ] Delay submitting to the HSTS preload list until the site has been live and stable for at least two weeks. Preload is effectively permanent — see `docs/ops/security-headers.md` §2 for the commitment.

---

## 5. Session cookies

- [ ] Sign in. Inspect the session cookie (`Application` tab in DevTools) — `Secure: true`, `HttpOnly: true`, `SameSite: Lax`.
- [ ] Cookie name matches the value set in `auth.py`.
- [ ] Cookie value is signed — tampering with a single character causes the backend to drop the session and fall back to the system user.

---

## 6. Rate limits

- [ ] `for i in $(seq 1 200); do curl -s -o /dev/null -w "%{http_code}\n" https://api.tryinspira.com/api/health; done | sort | uniq -c` — expect a batch of 200s then a batch of 429s as the per-IP limit trips.
- [ ] A 429 response still carries all security headers (verify with `curl -I` on a tripped request).
- [ ] Client receives a usable error JSON body (`{"error": "rate_limited", …}`), not a generic nginx error page.

---

## 7. Error monitoring

- [ ] Trigger a forced 500 against a staging API (e.g. temporarily raise in a route, deploy, hit it once, revert). Confirm the error appears in the Sentry production project within a minute.
- [ ] Stack traces in Sentry contain the full trace but the **client** receives only `{"error": "…", "request_id": "…"}` with no leaked internals. Verify via the network tab.
- [ ] Sentry environment tag is `production`, not `development`.

---

## 8. Per-user token budget

- [ ] Temporarily set `INSPIRA_USER_DAILY_TOKEN_BUDGET=100` against a staging user. Run a few kickoff requests until a 429 with `daily_token_budget_exhausted` comes back. Verify `Retry-After` header is set.
- [ ] Reset budget back to default.

---

## 9. Database backups

- [ ] Backup runs successfully against the production Postgres on the documented schedule.
- [ ] Restore tested end-to-end to a scratch staging DB — confirm tables exist and row counts match. Untested backups are not backups.
- [ ] Retention policy documented in the runbook.
- [ ] Point-in-time recovery window is longer than the time it takes to notice a mistake (24h minimum).

---

## 10. nginx hardening

- [ ] `curl -I https://app.tryinspira.com` does **not** include a `Server: nginx/1.X.Y` header (confirms `server_tokens off`).
- [ ] Static assets under `/assets/` return `Cache-Control: public, max-age=31536000, immutable`.
- [ ] `/index.html` returns `Cache-Control: no-cache`.
- [ ] Unknown path (e.g. `/nonexistent-route`) falls through to the SPA, not to an nginx default page.

---

## 11. Legal + content

- [ ] Terms of Service, Privacy Policy, DMCA Policy, Cookie Policy, Acceptable Use Policy all published under `/legal/*` and linked from the footer of the marketing site.
- [ ] Footer links resolve (no 404s).
- [ ] Privacy Policy's data-collection claims match what the backend actually collects (audit against `store.py` schema).
- [ ] GDPR data-subject request procedure documented and findable. See `docs/legal/gdpr-data-subject-procedure.md`.

---

## 12. Error paths

- [ ] Force a 500 on the SPA (e.g. inject a component error). Confirm the warm-editorial error boundary renders, not a raw React stack.
- [ ] Force a backend 500. Confirm the client receives the generic error shape with a `request_id`; no Python traceback leaks to the network response body.
- [ ] A 404 API response is JSON (`{"detail": "…"}`) — not an HTML error page.

---

## 13. 404 page

- [ ] Hit `https://app.tryinspira.com/definitely-not-a-route`. The SPA renders the warm-editorial NotFoundPage (not the nginx default, not a raw Vite 404).
- [ ] Same path on the API returns a JSON 404, not HTML.

---

## 14. Discoverability + hygiene

- [ ] Domain submitted to Google Safe Browsing (via Search Console) — ensures a first-party cert compromise, should it ever happen, surfaces quickly.
- [ ] `/.well-known/security.txt` published with a real contact. Optional but good hygiene; copy the template from RFC 9116.
- [ ] `robots.txt` allows crawling of marketing pages, disallows the app subdomain (`app.tryinspira.com`).
- [ ] No accidental `X-Powered-By` or similar stack-leaking headers (spot-check with `curl -I`).

---

## 15. Final smoke

- [ ] Fresh-browser sign-up end-to-end: account created, cookie set, project creation works, kickoff returns topics, a topic_turn succeeds, logout clears the cookie.
- [ ] Same flow in a private window with a second test account. Confirm topic lists are isolated (cross-user check).
- [ ] Sentry shows zero errors during the smoke run.

---

## 16. Post-launch (first 24 hours)

- [ ] Watch Sentry every couple of hours for unexpected errors.
- [ ] Watch OpenAI usage dashboard for anomalous spikes.
- [ ] Confirm auto cert renewal fires correctly on its scheduled day.

When every box is ticked, we're in decent shape for public traffic. When something fails, stop, fix it, and re-verify the whole section — don't check the box with a "we'll get to it later" intention. The purpose of this list is to catch the failures cheap.
