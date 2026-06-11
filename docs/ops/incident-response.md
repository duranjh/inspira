# Incident Response Playbook

**Audience:** the operator on duty.
**Goal:** restore the Service, communicate with affected users, and learn from every incident.
**Last updated:** 2026-04-20

This playbook is written for today's operational shape — a single operator, a managed Postgres database, a FastAPI backend running under uvicorn, a static-hosted frontend, and third-party APIs for OpenAI, Anthropic, and Sentry. Revise as the topology changes.

Companion document: `docs/ops/runbook.md` (day-to-day operations).

---

## 1. Severity levels

| Severity | Definition | Response |
| --- | --- | --- |
| **SEV-1** | Complete outage or data loss. Users cannot sign in, the product is unreachable, or data is being corrupted. Examples: backend entirely down, database unreachable, confirmed data-corruption bug in a running migration, confirmed security breach. | Drop everything. Announce on status page within 15 minutes. Update every 30 minutes until resolved. Post-mortem required within 5 business days. |
| **SEV-2** | Major degradation affecting all or most users. Core features are unusable but the product is partially reachable. Examples: kickoffs stuck, topic_turn failing for everyone, AI provider outage without fallback, frontend loading blank, attachment uploads broken. | Announce on status page within 30 minutes. Update every 60 minutes. Post-mortem required within 5 business days. |
| **SEV-3** | Partial degradation or an issue affecting a subset of users or a non-critical feature. Examples: export endpoint failing, a specific provider returning errors with a fallback available, intermittent 5xx at <5% of requests, canvas layout glitch. | Announce on status page within 2 hours if user-visible. Fix within the next business day or two. Post-mortem at the author's discretion. |
| **SEV-4** | Minor bug or annoyance. No user-visible outage. Examples: cosmetic issue, log-spam, a scheduled job running slow. | Track as a normal ticket. No status-page announcement. No post-mortem. |

When in doubt, err on the side of the higher severity.

---

## 2. On-call

Solo operator for now. The on-call pager is:

- Primary contact: the operator's mobile phone + an on-call email alias *(to be configured)*
- Paging integration: *[TO BE CONFIGURED — PagerDuty / Better Stack / Opsgenie link placeholder]*
- Secondary contact: n/a until a second operator exists.

Configure Sentry and host-monitoring alerts to page this contact for SEV-1 and SEV-2 equivalent conditions.

---

## 3. Triage checklist

When an alert fires or a report comes in, work through this list in order:

1. **Acknowledge the alert** within 5 minutes. Silence duplicate alerts until the incident is resolved.
2. **Open an incident channel** — create a dated note at `docs/ops/incidents/YYYY-MM-DD-short-title.md` with sections for timeline, impact, and actions taken. Update it in real time.
3. **Assess severity** using Section 1. If you are not sure, call it one level worse than your instinct.
4. **Confirm the symptoms** — reproduce the failing request, or check health endpoints (`/healthz`, `/readyz`). See `docs/ops/runbook.md` for the exact URLs.
5. **Check blast radius** using the matrix in Section 7. Is it all users? A single region? A single tenant? One endpoint?
6. **Check for recent changes** — the incident is almost always caused by the last deploy or a recent config change. Inspect the deploy log and Git commits within the last 24 hours.
7. **Decide: mitigate, roll back, or investigate in place.** Choose the smallest action that stops the bleeding.
8. **Communicate** — status-page update (Section 5), customer email (Section 6) if impact warrants, internal note in the incident log.
9. **Fix.** Keep the incident log updated as you go.
10. **Verify recovery.** Run smoke tests against the affected flows (sign-in, create project, kickoff, topic_turn, attachment upload).
11. **Close the incident.** Post the "resolved" update and note the time to recovery.
12. **Schedule the post-mortem** (Section 8).

---

## 4. Rollback procedures

### 4.1 Backend rollback

1. Identify the last known-good image or commit from the deploy history.
2. Redeploy it. With a managed host, this is usually a single-click rollback from the deploy dashboard. With a container platform, `docker pull` the prior tag and redeploy.
3. Confirm `/healthz` returns 200 and `/readyz` reports the expected database and dependency status.
4. Run the smoke-test flow in `docs/ops/runbook.md`.
5. If rollback is blocked (for example, because of a forward-only database migration), apply a forward fix instead — see Section 4.3.

### 4.2 Frontend rollback

1. Static-hosted frontend rolls back by redeploying the previous build artifact. On most static hosts (Vercel, Netlify, Cloudflare Pages) this is a single-click promotion of a prior deployment.
2. Confirm the Service loads, the login flow works, and a kickoff can complete end-to-end.
3. Clear the CDN cache only if the rollback does not appear to take effect, to avoid serving stale assets.

### 4.3 Database migration rollback

Database migrations are **forward-only** by policy. Instead of "reverse" migrations, use one of the following:

- **If the migration is additive** (new table, new column, new index): a backend rollback is usually enough because the old code will ignore the new schema object. Leave the schema change in place.
- **If the migration is destructive** (dropped column, renamed column, changed type): create a new forward migration that restores the old shape or adds the missing columns back. Deploy the restore migration first, then the backend rollback.
- **Data corruption:** stop writes to the affected tables by putting the backend into maintenance mode, then restore from the most recent clean backup for those rows specifically. Full database restore is a SEV-1 last resort.

Document every migration action in the incident log.

---

## 5. Status-page update procedure

*[TO BE CONFIGURED — status page not yet live. Placeholder procedure below; update once `status.tryinspira.com` is operational.]*

For each incident:

1. **Investigating** — post within the time window in Section 1. Example: "We are investigating reports of failed sign-ins and have degraded sign-in availability."
2. **Identified** — once you know the cause. Example: "We have identified the issue as a configuration error on the database host. We are applying a fix."
3. **Monitoring** — the fix is in place and recovery is in progress. Example: "A fix has been deployed. Sign-ins are succeeding again and we are monitoring."
4. **Resolved** — full recovery confirmed. Include an estimate of duration and affected users.

Use neutral, factual language. Never speculate publicly about root cause until the post-mortem is published.

---

## 6. Customer communication templates

Tailor these before sending. Keep them brief, honest, and free of jargon.

### 6.1 Outage (SEV-1)

> Subject: Inspira is currently unavailable — we are working on it
>
> Hi [name],
>
> You may have noticed that Inspira is unreachable right now. We are aware of the outage and are actively working to restore the Service.
>
> We will post updates on status.tryinspira.com and email you again when the Service is back. Your projects and account are safe — this is a service interruption, not a data issue.
>
> We are sorry for the disruption.
>
> — Inspira

### 6.2 Degraded service (SEV-2)

> Subject: Some Inspira features are currently degraded
>
> Hi [name],
>
> Inspira is running, but [specific feature — e.g., starting a new project, asking questions inside a topic] is currently failing or slow. We have identified the cause and are rolling out a fix.
>
> Live status: status.tryinspira.com. We will email again when we are fully back to normal.
>
> Thanks for your patience.
>
> — Inspira

### 6.3 Service restored

> Subject: Inspira is fully operational again
>
> Hi [name],
>
> The issue affecting [feature] has been resolved. From roughly [start time] to [end time] Pacific, users experienced [brief description]. Full service has been restored.
>
> If you see anything unusual, reply to this email and we will investigate.
>
> — Inspira

### 6.4 Security incident (send only after counsel review)

> Subject: Important security notice about your Inspira account
>
> Hi [name],
>
> We recently discovered [plain-language description of what happened, what was affected, what was not affected]. We have taken the following steps: [list]. We recommend you [specific user action].
>
> Full details and FAQ: [link]. If you have questions, reply to this email and we will answer personally.
>
> We are sorry this happened.
>
> — Inspira

---

## 7. Blast-radius assessment matrix

Use this matrix to estimate impact quickly and decide severity.

| Axis | Narrow | Wide | Catastrophic |
| --- | --- | --- | --- |
| **User scope** | A specific user or tenant | 10-50% of active users | All users |
| **Geography** | One region / one provider | A multi-region provider | Global |
| **Feature scope** | One endpoint / one feature | A core flow (kickoff, topic_turn, sign-in) | The whole product |
| **Data scope** | Metadata only | User content read path | User content write path, or persistent data loss |
| **Security scope** | No credential exposure | Session tokens exposed to Sentry or logs | Password hashes or attachments exposed |
| **Duration** | < 5 minutes | 5-60 minutes | > 60 minutes |

Two "catastrophic" columns or one catastrophic plus one wide = SEV-1. One wide = SEV-2. One narrow = SEV-3.

---

## 8. Post-mortem

A post-mortem is required for every SEV-1 and SEV-2 incident. Write it within five business days. Publish at `docs/ops/incidents/YYYY-MM-DD-short-title-postmortem.md`.

### Template

```
# Post-mortem: <title>

**Incident date:** YYYY-MM-DD
**Severity:** SEV-<n>
**Duration:** <start> to <end> (<duration>)
**Authors:** <name>

## Summary
One paragraph. What happened, who was affected, how it was resolved.

## Impact
- Users affected: <estimate>
- Features affected: <list>
- Data loss or corruption: yes / no. If yes, describe.
- External communication sent: yes / no, and to whom.

## Timeline (all times in UTC)
- HH:MM  Alert fires / report received.
- HH:MM  Responder acknowledges.
- HH:MM  Incident opened at SEV-<n>.
- HH:MM  Hypothesis formed.
- HH:MM  Fix applied.
- HH:MM  Recovery verified.
- HH:MM  Status-page "resolved" posted.

## Root cause
What actually caused this. Go beyond the surface — ask "why" several times. Describe the underlying assumption, configuration, or gap that let the failure happen.

## Contributing factors
- What made it worse or harder to detect than it should have been.
- What made it easier to recover than it could have been.

## What went well
- Detection channels that worked.
- Tooling that helped.
- Communication wins.

## What went poorly
- Gaps in observability.
- Gaps in runbook or playbook.
- Slow decisions.

## Lessons learned
A few candid sentences. No blame.

## Action items
Concrete, owned, dated. Each item is a ticket or commit.

| # | Action | Owner | Due | Type |
|---|--------|-------|-----|------|
| 1 | ...    | ...   | ... | code / runbook / monitoring / policy |
```

---

## 9. Data-breach handling

Security incidents that touch personal data have additional obligations under GDPR and other laws. Triggers that make an incident a "personal-data breach":

- confirmed unauthorized access to the application database or backups;
- leak of session tokens, password hashes, or attachments outside the Service;
- accidental exposure of user content to the wrong user (even a single user is enough).

When you suspect a personal-data breach:

1. Declare SEV-1 regardless of user count.
2. Start a legal-hold: preserve all logs, snapshots, and database states related to the incident. Do not overwrite.
3. Contact counsel before any external communication.
4. If confirmed, prepare a GDPR Article 33 notification to the supervisory authority within 72 hours of becoming aware. Include facts, likely consequences, and mitigation.
5. If the breach is likely to result in high risk to users, send the user notification described in Section 6.4 — after counsel review.
6. Coordinate with the [GDPR Data-Subject-Request Procedure](../legal/gdpr-data-subject-procedure.md) if affected users exercise their rights.
7. Update the Privacy Policy if the incident changes our stated practices.

---

## 10. Exercising the playbook

At least once a quarter, run a tabletop exercise. Pick a plausible scenario (database host outage, AI provider regional outage, accidental deletion of a production table) and walk the playbook from alert to post-mortem. Update the playbook with what you learn.

---

*Companion docs: `docs/ops/runbook.md` for day-to-day procedures, `docs/legal/gdpr-data-subject-procedure.md` for data-breach notification steps, `docs/legal/privacy-policy.md` for public-facing commitments.*
