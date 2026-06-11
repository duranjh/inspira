# Security policy

Inspira takes security seriously. If you've found a vulnerability, thank you — please report it privately so we can fix it before attackers exploit it.

> **Note:** the original hosted Inspira service has been discontinued. This
> policy covers the open-source codebase. Reports about the defunct hosted
> infrastructure are out of scope.

## How to report

Use **GitHub Private Vulnerability Reporting**: go to the repository's
**Security** tab and click **"Report a vulnerability."** This opens a private
advisory only the maintainer can see.

Include:
- A clear description of the issue.
- Steps to reproduce, with curl commands or a minimal test case if applicable.
- Your assessment of the impact.
- Any relevant logs, screenshots, or proof-of-concept code.

Please do not:
- Open a public GitHub issue.
- Share the vulnerability publicly until we've had a chance to fix it.
- Test against other people's deployments or data.

## What we'll do

- Acknowledge receipt within **72 hours** (best effort — this project is not
  under active development).
- Keep you informed as we investigate and patch.
- Credit you in the changelog once the fix is public, unless you prefer otherwise.
- Coordinate on a disclosure timeline — usually 30-90 days depending on severity.

## Scope

In scope:
- Authentication and session handling.
- Authorization and access control (one user reading another user's data).
- Input validation, SQL injection, XSS.
- Server-side request forgery in the URL fetcher.
- Cookie security.
- LLM prompt injection that exposes other users' data.
- Anything that leaks secrets, tokens, or internal configuration.

Out of scope (but still welcome reports):
- Issues in third-party services the app integrates with (OpenAI, Anthropic, Stripe, Sentry) — report those directly to the vendor.
- Social engineering.
- Physical attacks against infrastructure.
- Self-XSS that requires running the victim's own JavaScript.
- Missing security headers without a demonstrable exploit.

## Bounty

There is no bug bounty program. We will credit researchers in the public
changelog, and we'll work with you on any coordinated-disclosure framing you
prefer.

## Known issue classes

The following are known limitations that don't constitute a vulnerability in themselves:

- Rate limits are per-IP via slowapi; distributed abuse from many IPs can exceed intended thresholds.
- Session tokens use `SameSite=Lax`, not `Strict`. Top-level POST forms from other origins can still be submitted — mitigated by the API only accepting JSON bodies.
- The browser-side PDF / OCR extraction does not enforce a DRM or watermark — users can extract text from anything they can upload.

See `docs/deployment/security-audit.md` for the most recent pre-deploy audit.

## Hall of fame

No reports yet. Your name could go here.
