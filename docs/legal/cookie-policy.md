> ⚠ DRAFT — not reviewed by counsel. Do not publish without legal review.

# Inspira Cookie Policy

**Effective date:** 2026-04-24
**Last updated:** 2026-04-20

This Cookie Policy explains how Inspira ("we", "us", "our") uses cookies and similar technologies on `tryinspira.com` and `app.tryinspira.com`. Read this alongside our [Privacy Policy](./privacy-policy.md).

---

## 1. What cookies are

A cookie is a small text file a website stores on your browser. Cookies let websites remember your actions between requests — for example, keeping you signed in after you enter your password. "Similar technologies" include `localStorage` and `sessionStorage`, which store data inside your browser but are not transmitted with each request.

---

## 2. Cookies we use today

| Name | Type | Purpose | Duration | Essential? |
| --- | --- | --- | --- | --- |
| `inspira_session` | First-party, `httpOnly`, `SameSite=Lax`, `Secure` | Keeps you signed in to your Inspira account. Stores a short signed session identifier — it does not contain your password or your project data. | Up to 30 days from your last sign-in; reset when you sign out | Yes — strictly necessary for the Service |

### Local storage

We use browser `localStorage` for lightweight UI preferences — for example, whether you last had dark mode enabled. These values never leave your browser.

---

## 3. Cookies we plan to add

We will update this table and this policy before any of these cookies are enabled in production.

| Name | Type | Purpose | Duration | Essential? |
| --- | --- | --- | --- | --- |
| CSRF token *(planned)* | First-party, `SameSite=Lax`, `Secure` | Protects authenticated requests from cross-site request forgery | Session | Yes — strictly necessary |
| Analytics *(only if we introduce it)* | First-party, privacy-preserving | Aggregate usage measurements (page views, error rates). No cross-site tracking. | To be defined | No — consent-based where required |

We do not currently use third-party advertising or tracking cookies, and we have no plans to do so.

---

## 4. Your choices

Because our current cookie is strictly necessary for the Service to function, you cannot sign in to Inspira without accepting it. You can still:

- **Delete cookies** — most browsers let you delete stored cookies from Settings. If you delete `inspira_session`, you will be signed out and will need to sign in again.
- **Block cookies globally** — you can configure your browser to block all cookies, but the Service will not work in that configuration.
- **Browser-specific instructions:**
  - Chrome: `chrome://settings/cookies`
  - Firefox: Settings → Privacy & Security → Cookies and Site Data
  - Safari: Preferences → Privacy → Manage Website Data
  - Edge: Settings → Cookies and site permissions

If and when we add optional cookies (for example, analytics), we will ask for your consent first where required by law, and you will be able to change your choice later in the Service.

---

## 5. "Do Not Track"

Some browsers send a "Do Not Track" signal. There is no common industry standard on how to respond to it. Because we do not use cross-site tracking cookies, our practices already align with the spirit of that signal.

---

## 6. Changes

If we add new cookies or change the purpose of existing ones, we will update this page and, where required, ask for your consent. The "Last updated" date at the top will reflect the change.

---

## 7. Contact

Questions about cookies: **privacy@tryinspira.com**

---

*This document is a first draft. Counsel should confirm the categorization of `inspira_session` as strictly necessary under the EU ePrivacy Directive and equivalent laws, verify consent requirements in target jurisdictions, and review the language before any optional or analytics cookie is introduced.*
