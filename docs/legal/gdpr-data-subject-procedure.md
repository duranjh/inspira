> ⚠ DRAFT — not reviewed by counsel. Do not publish without legal review.

# GDPR Data-Subject-Request Procedure

**Audience:** operators running an Inspira instance.
**Status:** template operational procedure — adapt it to your own deployment before relying on it. It is not a public-facing document.
**Effective date:** 2026-04-24
**Last updated:** 2026-04-20

This document describes how Inspira handles data-subject access, rectification, erasure, portability, restriction, and objection requests under the EU General Data Protection Regulation ("GDPR"), the UK Data Protection Act, the California Consumer Privacy Act as amended ("CCPA"), and similar laws. The public-facing description of user rights lives in the [Privacy Policy](./privacy-policy.md); this document describes the operational workflow the operator follows to honor those rights.

Whenever this procedure is updated, review the user-facing Privacy Policy (Section 10) to ensure the two documents remain consistent.

---

## 1. Scope

This procedure applies to any request ("DSAR") from a user, former user, or their authorized agent asking us to:

- confirm whether we hold personal data about them ("access request");
- provide a copy of that personal data ("access / portability request");
- correct inaccurate or incomplete data ("rectification request");
- delete data we hold about them ("erasure request" / "right to be forgotten");
- restrict processing of their data ("restriction request");
- object to processing ("objection request"); or
- withdraw a consent they previously gave.

The procedure also covers requests from regulators or their appointed representatives under GDPR, CCPA, PIPEDA, LGPD, and similar laws.

---

## 2. Intake channels

DSARs can reach us through:

1. **A dedicated privacy inbox** (primary) — monitored by the operator.
2. **hello@tryinspira.com** — general inbox; any request received here must be forwarded to `privacy@` within one business day.
3. **In-product "Delete my account" flow** — treat the account-deletion action as an implicit erasure request scoped to the user's own data.
4. **Legal process** — subpoenas, court orders, law-enforcement requests. These follow a separate escalation (see Section 11).

Every DSAR, regardless of channel, is logged in the internal DSAR register (a dated entry with the requester's identifier, the request type, the decision, and the response date).

---

## 3. Response window

| Regime | Response window | Extension allowed |
| --- | --- | --- |
| GDPR / UK GDPR | 30 calendar days | Up to 60 additional days for complex or multiple requests, with notice to the requester within the first 30 days |
| CCPA | 45 calendar days for substantive response; 10 days for acknowledgment | Up to 45 additional days with notice |
| LGPD (Brazil) | 15 calendar days | Possible by mutual agreement |
| PIPEDA (Canada) | 30 calendar days | Up to 30 additional days with written justification |

Default to the **30-day GDPR window** for all requests unless a shorter local law applies. Send an acknowledgment within 3 business days regardless.

---

## 4. Identity verification

We do not action a DSAR until we are confident the requester is the data subject (or an authorized agent).

### 4.1 Self-service (preferred)

If the request comes from the email address on the Account and can be completed through the user's own login (for example, editing their display name or triggering account deletion inside the app), direct the user to that flow instead of handling the request manually.

### 4.2 Email verification

If the request comes from the email address on the Account and the request is a routine access, rectification, portability, restriction, objection, or erasure request, we accept the email verification as sufficient for most cases.

### 4.3 Additional verification

Ask for additional verification in any of these cases:

- the requester claims to be acting on behalf of the data subject (request authorization evidence; for California, authorized agents must provide a signed permission unless the agent is a registered power of attorney);
- the request relates to sensitive operations (e.g., export of a large project with third-party personal data inside);
- the request is made from an email address that does not match the Account's email;
- there are signs of fraud (inconsistent information, recent account takeover attempts).

Acceptable additional verification includes replying from the Account's registered email after we send a one-time challenge, providing the Account's identifier (UUID) visible only to the signed-in user, or demonstrating knowledge of recent non-public Account activity we can cross-check against logs.

**Do not ask for more identity information than is strictly necessary.** Do not request government identification unless the sensitivity of the request and the risk of impersonation genuinely require it.

Record the verification method in the DSAR register.

---

## 5. Fees and refusal

Requests are free of charge. We may charge a reasonable fee or refuse the request only if it is manifestly unfounded or excessive, in particular because of its repetitive character. Any refusal is documented in writing with the reason and the user's right to complain to a supervisory authority.

---

## 6. Request templates and procedures by type

### 6.1 Access request — "Show me what you have"

Workflow:

1. Verify identity (Section 4).
2. Run the internal export script (see Section 8) against the user's identifier to gather all personal data we hold.
3. Deliver the data to the user via a secure, authenticated link to a JSON file, or as an email attachment if the export is under 10 MB.
4. Accompany the data with the metadata that GDPR Article 15 requires: purposes, categories, recipients, retention, source, rights, existence of automated decision-making, international transfers.

**Response template:**

> Hi [name],
>
> Thanks for your request. We have verified your identity and prepared an export of the personal data we hold about you.
>
> Attached (or linked) you will find a JSON file containing your Account profile, all your projects, topics, relationships, decisions, Q&A turns, attachments metadata, audit-log entries for your Account, and diagnostic events captured by our error monitor.
>
> You can find the following information about how we process this data in our [Privacy Policy](https://tryinspira.com/privacy). The data is provided as of [YYYY-MM-DD].
>
> If anything is inaccurate, please reply and we will correct it. If you want us to delete it, let us know and we will proceed under our erasure procedure.
>
> Best,
> Inspira Privacy

### 6.2 Rectification request — "Fix this"

Workflow:

1. Verify identity.
2. Ask the requester to specify exactly which field is wrong and what the correct value is, unless already stated.
3. Apply the change in the appropriate table (usually `users` for profile fields; `v2_projects` or a nested table for project content).
4. If the incorrect data was shared with sub-processors (for example, Sentry error events), propagate the correction where possible.
5. Confirm completion in writing.

**Response template:**

> Hi [name],
>
> We have updated [field] from "[old value]" to "[new value]" on your Account. Please let us know if anything else looks off.

### 6.3 Erasure request — "Delete everything"

Workflow:

1. Verify identity.
2. Confirm whether the request scopes the entire Account or only specific data (e.g., "please remove this one topic").
3. Warn the requester that erasure is irreversible once the soft-delete grace period expires. Offer a last-chance export if they have not already requested one.
4. Trigger the permanent-delete procedure in Section 9.
5. Wait for the 30-day soft-delete grace period to elapse, unless the requester explicitly asks for immediate hard-delete, in which case skip the grace period.
6. Confirm completion in writing after the purge job runs.
7. Record the date and scope in the DSAR register. Keep only the minimum record needed to demonstrate compliance; avoid keeping personal data in the DSAR log itself.

**Response template (initial acknowledgment):**

> Hi [name],
>
> Thanks for your request. We have verified your identity and will proceed with deleting your Inspira Account and all associated personal data.
>
> [If soft-delete grace period applies] Your data enters a 30-day soft-delete grace period, after which we permanently erase it. If you change your mind, reply to this email within the next 30 days.
>
> [If immediate hard-delete requested] You asked us to proceed immediately without the grace period. The permanent-delete job will run within 1 business day and we will confirm when it completes.
>
> If you would like an export of your data before it is deleted, let us know and we will prepare one.

**Response template (after completion):**

> Hi [name],
>
> We have permanently deleted your Inspira Account and all personal data we held about you, including projects, topics, attachments, audit-log entries, and diagnostic events. The deletion propagates to our backups over the next 30 days as older backups are overwritten.
>
> This email is the last communication you will receive from us regarding this Account.

### 6.4 Portability request — "Give me my data in a useful format"

Workflow:

1. Verify identity.
2. Run the export script described in Section 8 to produce a JSON file.
3. Deliver the file to the user.
4. If the user asks us to transmit the data directly to another controller, confirm that the transfer is technically feasible and that the receiving party is prepared to accept it. If it is not feasible, say so in writing.

**Response template:** use the access-request template (Section 6.1) and note that the file is in structured JSON suitable for portability.

### 6.5 Restriction request — "Pause processing"

Workflow:

1. Verify identity.
2. Confirm the scope of the restriction (usually the user wants us to stop processing pending the resolution of a rectification or objection).
3. Mark the Account `is_restricted = true` and exclude it from non-essential processing (analytics, improvement workflows). Preserve the data.
4. Document the basis and duration.
5. Notify the user if and when we lift the restriction.

### 6.6 Objection — "Stop doing X"

Workflow:

1. Verify identity.
2. Determine whether the processing is based on legitimate interest (subject to objection) or on contract / legal obligation (not subject to objection).
3. If legitimate interest, balance the user's rights against our grounds. If the user's rights prevail — which they usually do — stop the processing and confirm.
4. Document the assessment and outcome.

### 6.7 Withdrawal of consent

Workflow:

1. Verify identity.
2. Stop the consent-based processing (usually email marketing or optional analytics).
3. Confirm in writing. Note that withdrawal does not affect the lawfulness of processing performed before withdrawal.

---

## 7. Handling requests that touch other users

Some exports may contain personal data about third parties (for example, if a user pastes another person's email into a project note). Before disclosing a third party's data, redact it or obtain that third party's consent. Document the redactions.

---

## 8. Exporting user data (technical procedure)

The export is a JSON document containing the fields listed below. Extend the script whenever a new table is added to the schema.

```
{
  "format_version": "1.0",
  "exported_at": "<ISO 8601 timestamp>",
  "user": {
    "user_id": "<UUID>",
    "email": "<email>",
    "display_name": "<string>",
    "created_at": "<ISO 8601>",
    "last_login_at": "<ISO 8601>"
  },
  "projects": [
    {
      "project_id": "<UUID>",
      "title": "<string>",
      "created_at": "<ISO 8601>",
      "updated_at": "<ISO 8601>",
      "topics": [ { "topic_id": "...", "title": "...", "body": "...", "canvas_x": ..., "canvas_y": ... } ],
      "relationships": [ { "from_topic_id": "...", "to_topic_id": "...", "kind": "..." } ],
      "qna_turns": [ { "turn_id": "...", "role": "...", "content": "...", "created_at": "..." } ],
      "decisions": [ { "decision_id": "...", "topic_id": "...", "summary": "...", "created_at": "..." } ],
      "attachments": [ { "attachment_id": "...", "filename": "...", "mime_type": "...", "size_bytes": ..., "created_at": "..." } ],
      "sources": [ { "source_id": "...", "kind": "url|paste|file", "reference": "..." } ]
    }
  ],
  "audit_events": [
    { "event_id": "...", "event_type": "login_success|password_reset|...", "ip": "...", "user_agent": "...", "created_at": "..." }
  ],
  "diagnostic_events_summary": {
    "count": <int>,
    "retention_policy": "See Sentry retention policy; events beyond the retention window are not available."
  }
}
```

Attachments themselves (the file bytes) are exported separately as a zipped archive alongside the JSON, with filenames keyed by `attachment_id`.

Run the export through the privacy tooling CLI (planned — if not yet implemented, generate the export manually by querying the database with the user's `user_id` and packaging the results). Record the command and parameters used in the DSAR register.

---

## 9. Permanently deleting user data (technical procedure)

The purge job cascades deletions across every table and integration that holds personal data about the user.

1. **Application database** — hard-delete rows in this order (to respect foreign keys):
   - `qna_turns` WHERE `project_id` IN user's projects
   - `decisions` WHERE `project_id` IN user's projects
   - `relationships` WHERE the from- or to-topic belongs to the user's projects
   - `topics` WHERE `project_id` IN user's projects
   - `attachments` WHERE `project_id` IN user's projects (also delete the underlying object-storage files)
   - `sources` WHERE `project_id` IN user's projects
   - `v2_projects` WHERE `user_id = <user>`
   - `audit_events` WHERE `user_id = <user>`
   - `users` WHERE `user_id = <user>`
2. **Object storage** — delete every attachment blob, including thumbnails and any derived files.
3. **Error monitor (Sentry)** — delete events associated with the user's identifier using Sentry's data-deletion API. Confirm the job completes.
4. **Email provider** — remove the user from any contact or suppression lists where their presence would be identifying; retain only the minimum suppression record required to honor unsubscribe requests (per CAN-SPAM and GDPR).
5. **Analytics (if any)** — delete or anonymize rows keyed to the user's identifier.
6. **Backups** — the user's data will persist in rolling backups until those backups are overwritten. Document the 30-day overwrite cadence and communicate it to the user. Do not restore any backup that pre-dates the deletion without first re-applying the deletion.
7. **DSAR register** — keep only the minimum proof-of-compliance metadata (date, request type, verification method, outcome). Redact the user's contact information after the matter is closed and the limitation period for regulatory complaints has passed.

Record the purge timestamp and the operator who ran the job.

---

## 10. Dealing with authorized agents and minors

- **Authorized agents** (especially CCPA): require a signed authorization from the data subject or proof of power of attorney. Independently verify the data subject when practical.
- **Minors (13-17):** require confirmation of parental or guardian involvement. Do not process erasure requests from a third party purporting to be a parent without evidence.

---

## 11. Law-enforcement, regulator, and litigation requests

These are not DSARs and follow a different path:

1. Forward to counsel before responding.
2. Require the request in writing on official letterhead, with a case or matter reference.
3. Push back on overbroad requests; require narrowly tailored scope.
4. Preserve data relevant to a known legal hold.
5. Keep the DSAR register separate from the legal-process register.

---

## 12. Breach overlap

If, during the processing of a DSAR, we discover evidence of a data breach (for example, the requester mentions seeing data they should not have), escalate immediately to the incident-response playbook at `docs/ops/incident-response.md`. A DSAR does not delay the 72-hour GDPR breach-notification obligation.

---

## 13. Records and audits

The DSAR register is retained for at least 3 years after the last action on a request, to demonstrate compliance under GDPR Article 5(2) ("accountability"). Review the register quarterly to verify response times and identify repeat patterns.

---

*This document is a first draft. Counsel should review the verification thresholds (Section 4), the retention of DSAR records (Section 13), the exact scope of the permanent-delete cascade (Section 9) once the schema stabilizes, and the interplay between the soft-delete grace period and the GDPR "without undue delay" requirement. Keep this file synchronized with Section 10 of the Privacy Policy.*
