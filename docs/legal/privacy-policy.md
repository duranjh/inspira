> ⚠ DRAFT — not reviewed by counsel. Do not publish without legal review.

# Inspira Privacy Policy

**Effective date:** 2026-04-24
**Last updated:** 2026-04-20

This Privacy Policy explains how Inspira ("Inspira", "we", "us", "our") collects, uses, shares, and protects personal information about you when you use the Service at `tryinspira.com` and `app.tryinspira.com`. It applies to all users worldwide. If you have questions, contact **privacy@tryinspira.com**.

This policy should be read alongside our [Terms of Service](./terms-of-service.md), [Cookie Policy](./cookie-policy.md), [Acceptable Use Policy](./acceptable-use.md), and [DMCA Policy](./dmca-policy.md).

---

## 1. Data controller

The "data controller" for the purposes of the European Union General Data Protection Regulation ("GDPR"), the United Kingdom Data Protection Act ("UK GDPR"), and similar laws is:

**Inspira** (operating entity TBD)

Contact: **privacy@tryinspira.com**

---

## 2. Overview

In plain English:

- You create an account with an email, display name, and password.
- Inside Inspira, you build projects, topics, decisions, and attachments — this is **your content**, and you own it.
- We transmit your prompts and relevant project data to third-party AI providers (OpenAI and Anthropic) to generate AI output at your request.
- We keep minimal telemetry and error data to operate the Service.
- **We do not sell your data. We do not use your content to train our own models.**

The rest of this policy describes each of those points in detail.

---

## 3. Information we collect

### 3.1 Information you provide

| Category | Examples | Purpose |
| --- | --- | --- |
| **Account data** | Email address, display name, password hash (we never store plain-text passwords) | Create and secure your Account; sign-in; communication |
| **Project data (User Content)** | Project names, topics, decisions, Q&A turns, relationships, notes, canvas positions | Provide the core Service |
| **Attachments and sources** | Uploaded files (text, images, PDFs), pasted text, URLs you add as sources | Enrich AI prompts at your direction |
| **Support communications** | Content of emails and messages you send us | Respond to your question; improve support |
| **Billing data** (when paid tiers launch) | Name, billing email, payment instrument token (held by our payment processor, not us), billing address | Process payments; tax compliance |

### 3.2 Information collected automatically

| Category | Examples | Purpose |
| --- | --- | --- |
| **Session cookie** | `inspira_session` (httpOnly, SameSite=Lax, 30-day max-age) | Keep you signed in |
| **Future cookies** | CSRF token (planned), analytics (only if introduced, with notice) | Security; product improvement |
| **IP address** | Origin IP of each request | Security, abuse prevention, rate limiting, rough geolocation for compliance |
| **User agent** | Browser and device identifiers | Debugging, compatibility |
| **Usage telemetry** | Page views, feature interactions, latency, error codes | Understand and improve the Service |
| **Error and diagnostic data** | Stack traces, request IDs, partial request payloads sent via our error monitor (Sentry) | Troubleshoot and fix bugs |
| **Audit logs** | Account sign-in events, security-relevant admin actions | Account security, incident investigation |

We do not collect precise GPS location, contacts, or biometric data.

### 3.3 Information from third parties

- If we introduce single sign-on (e.g., Google, GitHub), we will receive the profile information you authorize those providers to share (email, display name, profile picture).
- Our payment processor (when paid plans launch) will share limited billing metadata.

---

## 4. How we use your information

We process personal data for the following purposes:

1. **Provide the Service** — create and secure your Account, store your projects, transmit prompts to AI providers, return AI output, render the user interface.
2. **Communicate with you** — transactional email (password resets, security alerts, account notices, receipts), product updates, responses to your support requests.
3. **Secure the Service** — detect abuse, enforce rate limits, investigate suspicious activity, prevent credential stuffing and spam.
4. **Improve the Service** — aggregate usage analytics, A/B testing (if we introduce it), crash diagnostics, performance optimization.
5. **Comply with law** — respond to valid legal requests, tax obligations, export controls.
6. **Enforce our agreements** — investigate and act on violations of the Terms of Service and Acceptable Use Policy.

**We do not use your User Content to train our own machine-learning models.** Our third-party AI providers process your prompts under contracts that prohibit them from training their models on your content — see Section 6.

---

## 5. Legal bases (EU / UK / EEA / similar jurisdictions)

Where GDPR or a substantially similar law applies, we process personal data under the following bases:

| Processing activity | Legal basis |
| --- | --- |
| Account creation, authentication, delivery of the Service | Performance of a contract (Art. 6(1)(b) GDPR) |
| Transactional communications tied to your Account | Performance of a contract |
| Security, fraud prevention, abuse investigation | Legitimate interest (Art. 6(1)(f) GDPR) — our interest in running a secure service |
| Error diagnostics and minimal usage analytics | Legitimate interest |
| Product-marketing email (if any) | Consent (Art. 6(1)(a) GDPR), with unsubscribe on every message |
| Optional cookies beyond strictly necessary ones (if introduced) | Consent |
| Tax, accounting, and compliance with law | Legal obligation (Art. 6(1)(c) GDPR) |

You can withdraw consent at any time. Withdrawal does not affect processing already performed on that basis.

---

## 6. Third-party processors (sub-processors)

We use the following providers to help us deliver the Service. Each is bound by a written data-processing agreement. We review this list periodically; changes will be announced on this page.

| Processor | Role | Data handled | Location |
| --- | --- | --- | --- |
| **OpenAI** (OpenAI, L.L.C.) | Large language model provider | Prompts and relevant project context you submit when you invoke an AI feature; AI output | USA |
| **Anthropic** (Anthropic PBC) | Large language model provider | Same as above, when you use a Claude-backed feature | USA |
| **Sentry** (Functional Software, Inc.) | Error monitoring | Stack traces, request identifiers, scrubbed error payloads, IP address | USA |
| **Database host** *(provider TBD — e.g., managed Postgres)* | Primary data store | All Account data and User Content | USA *(to be confirmed)* |
| **Application host** *(provider TBD)* | Runs the backend and frontend | All traffic to and from the Service | USA *(to be confirmed)* |
| **Email provider** *(provider TBD — e.g., Postmark, Resend, AWS SES)* | Transactional email delivery | Email address, display name, email body | USA *(to be confirmed)* |
| **Payment processor** (when paid plans launch — e.g., Stripe) | Payments | Billing metadata, payment-instrument token | USA |

**Contracts with AI providers:** OpenAI and Anthropic have both published commitments that API traffic is not used to train their models by default. We configure our integrations to run under those defaults. If either provider materially changes its practices, we will update this policy and give you notice.

---

## 7. Cookies and local storage

See the [Cookie Policy](./cookie-policy.md) for details. In summary:

- `inspira_session` — httpOnly, SameSite=Lax, 30-day max-age; used to keep you signed in.
- Future CSRF token cookie (when enabled).
- No third-party advertising or tracking cookies.
- No cross-site analytics cookies today. If we introduce analytics, we will update the Cookie Policy and, where required, ask for consent.

Your browser also stores some information locally (e.g., UI preferences). Local storage is not transmitted to us.

---

## 8. Sharing your information

We share personal data only in these circumstances:

1. **With our sub-processors** (Section 6), under written data-processing agreements, to deliver the Service.
2. **With AI providers** when you invoke an AI feature, as described in Section 6.
3. **With your consent** — for example, if we introduce integrations you can opt into.
4. **With legal authorities** when required by law, valid legal process, or to protect the rights, property, or safety of our users, the Service, or the public.
5. **With an acquirer** if Inspira is involved in a merger, acquisition, reorganization, bankruptcy, or sale of assets. We will notify you before your personal data becomes subject to a different privacy policy.

**We do not sell your personal data. We do not share your personal data for cross-context behavioral advertising, as those terms are defined under the California Consumer Privacy Act as amended by the CPRA.**

---

## 9. Data retention

| Data | Retention |
| --- | --- |
| Account data (email, display name, password hash) | For as long as your Account exists, then deleted after the soft-delete grace period (see below) |
| User Content (projects, topics, decisions, attachments, Q&A turns) | Kept indefinitely while your Account is active. Subject to soft-delete when you delete projects or your Account. |
| Soft-delete grace period | 30 days after an Account or project is marked for deletion, during which you can restore it. After 30 days, permanent erasure begins. |
| Audit logs (sign-in events, security-relevant admin actions) | 1 year, then deleted or aggregated beyond identifiability |
| Error / diagnostic data (Sentry) | Retained per Sentry's default retention (currently 90 days for errors; confirm) |
| Email delivery logs | 30 to 90 days, depending on provider, then deleted |
| Billing records (when paid plans launch) | As required by tax and accounting law (typically 7 years in the US) |
| Backups | Rolling backups for disaster recovery, overwritten on a schedule (target: 30 days). Erasure propagates to backups on the next overwrite cycle. |

If applicable law requires us to retain certain data longer (for example, for tax or litigation-hold purposes), we will keep only the minimum necessary data for that purpose and continue to protect it.

---

## 10. Your rights

Subject to applicable law, you have the following rights regarding your personal data:

- **Access** — ask for a copy of the personal data we hold about you.
- **Rectification** — ask us to correct inaccurate or incomplete data.
- **Erasure ("right to be forgotten")** — ask us to delete your personal data.
- **Portability** — receive your data in a structured, machine-readable format (JSON).
- **Restriction** — ask us to limit how we process your data.
- **Objection** — object to processing based on legitimate interest.
- **Withdraw consent** — where we rely on consent, you can withdraw it at any time.
- **Automated decisions** — we do not make decisions about you based solely on automated processing that produces legal or similarly significant effects.
- **Complain to a regulator** — for EU/UK users, the right to lodge a complaint with your national data-protection authority.

### How to exercise your rights

Email **privacy@tryinspira.com** from the address on your Account, or from an address you can prove you control, and describe your request. We respond within 30 days (extendable by up to 60 additional days for complex requests, with notice). For security, we may ask you to verify your identity before we act.

Our internal procedure for responding to data-subject requests is documented in the [GDPR Data-Subject-Request Procedure](./gdpr-data-subject-procedure.md) *(operational doc, not public-facing)*.

---

## 11. Children

The Service is not directed to children under 13. We do not knowingly collect personal data from children under 13. If you believe a child under 13 has provided us with personal data, contact **privacy@tryinspira.com** and we will delete it.

Users between 13 and 18 (or the age of majority in their jurisdiction) should use the Service only with the involvement of a parent or legal guardian.

---

## 12. International data transfers

We are based in the United States and our primary sub-processors are located in the United States. If you are outside the United States, your personal data will be transferred to, stored in, and processed in the United States and other countries where our sub-processors operate.

For transfers of personal data out of the European Economic Area, United Kingdom, or Switzerland, we rely on appropriate safeguards, including the European Commission's Standard Contractual Clauses and the UK International Data Transfer Addendum, supplemented by technical and organizational measures where needed. You can request a copy of the safeguards by emailing **privacy@tryinspira.com**.

---

## 13. California privacy notice (CCPA / CPRA)

If you are a California resident, the California Consumer Privacy Act, as amended by the California Privacy Rights Act (together, "CCPA"), gives you additional rights.

In the past 12 months we have collected the categories of personal information listed in Section 3 of this policy. We have used each category for the purposes described in Section 4. We have disclosed personal information to the sub-processors listed in Section 6 for the business purposes described there.

**We do not "sell" personal information and we do not "share" it for cross-context behavioral advertising** as those terms are defined under CCPA.

California residents have the right to:

- know the categories and specific pieces of personal information we collect;
- request deletion of personal information we have collected;
- request correction of inaccurate personal information;
- limit use and disclosure of sensitive personal information (we do not use sensitive personal information beyond what is strictly necessary to deliver the Service, so this right is effectively already honored);
- opt out of sale or sharing (not applicable — we do neither);
- be free from retaliation for exercising these rights.

To exercise these rights, email **privacy@tryinspira.com**. You may also designate an authorized agent; we will verify their authority before acting.

---

## 14. GDPR and EEA / UK specifics

In addition to the rights in Section 10:

- **Legal bases:** see Section 5.
- **International transfers:** see Section 12.
- **Right to complain to a supervisory authority:** you may complain to the data-protection authority in your EU member state, or to the UK Information Commissioner's Office.
- **EU representative / UK representative:** 

---

## 15. Security

We take reasonable technical and organizational measures to protect personal data, including:

- transport encryption (TLS) for all traffic;
- at-rest encryption for database and backups (inherited from our managed database host);
- password hashing using a modern, memory-hard algorithm;
- principle-of-least-privilege access to production systems;
- regular dependency updates and security patching;
- centralized error monitoring with scrubbed payloads;
- audit logging for security-relevant events;
- incident response procedures, documented in `docs/ops/incident-response.md`.

No system is perfectly secure. If we become aware of a breach affecting your personal data, we will notify you and, where required, regulators within the timelines set by applicable law (for GDPR, within 72 hours to the supervisory authority).

---

## 16. Automated decision-making and AI

We use large language models to generate suggestions, questions, and structure inside your projects. These outputs support your thinking — they do not make decisions that produce legal or similarly significant effects about you. You are always free to ignore AI output, edit it, or delete it.

---

## 17. Do-not-track

Some browsers send a "Do Not Track" signal. There is no common industry standard on how to respond. We do not change our practices based on this signal today. If that changes, we will update this policy.

---

## 18. Changes to this Privacy Policy

We may update this Privacy Policy as the Service evolves. When we make material changes, we will notify you by email to the address associated with your Account or through a prominent notice in the Service, and we will update the "Last updated" date above. Your continued use of the Service after the change takes effect constitutes acceptance.

---

## 19. Contact

Privacy questions: **privacy@tryinspira.com**
General support: **hello@tryinspira.com**
Physical mailing address: 

---

*This document is a first draft. In particular, the sub-processor list (Section 6), international-transfer safeguards (Section 12), California disclosures (Section 13), and EEA/UK representative questions (Section 14) require counsel review before publication. Coordinate Section 10 (user rights) with the internal procedure in `docs/legal/gdpr-data-subject-procedure.md`.*
