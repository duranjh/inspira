# Legal review pending — internal TODO tracker

Not rendered on any user-visible surface. Tracks specific clauses across the
public legal documents that need professional review before we incorporate
the operating entity and formally publish the policies.

Once each item is resolved, edit the referenced file directly and delete the
matching entry here. The CI guard in `scripts/check_legal_placeholders.sh`
(added by PR 5) fails the build if any `LAWYER CHECK` / `LAWYER / OPS CHECK`
marker appears outside this file.

---

## `docs/legal/acceptable-use.md`

- **Security-disclosure alias** — confirm `security@tryinspira.com` is
  provisioned (with a monitored inbox + on-call rotation) before shipping
  the policy publicly. The inline "alias to be configured" caveat was
  removed in PR 5; the address now reads as live.

## `docs/legal/dmca-policy.md`

- **Designated agent mailing address** — add the physical address of the
  designated agent once the entity is formally incorporated. A P.O. box
  alone is not sufficient for USCO registration. The agent must be
  registered with the U.S. Copyright Office via the DMCA Designated Agent
  Directory at `dmca.copyright.gov` before this policy is published.

## `docs/legal/privacy-policy.md`

- **Operating entity disclosure** — fill in legal entity name, registered
  address, and any EU/UK representative once the entity is formally
  incorporated. Also confirm whether the single-founder model requires the
  appointment of a Data Protection Officer under Article 37 — unlikely
  given scale, but worth documenting the reasoning.
- **Sub-processor table completeness** — confirm the sub-processor table
  is complete once hosting/email providers are selected. Add the provider's
  data-processing addendum links. Consider whether Sentry should be
  reclassified as a security-log processor for retention purposes. Ensure
  international transfer mechanisms (Standard Contractual Clauses, UK
  IDTA) are in place for each processor.
- **Sentry error-retention pin** — pin to Sentry's current, contracted
  retention rather than the default-90-days language.
- **Minor / child protection** — consider whether to set a higher minimum
  age (for example, 16 in some EU member states under GDPR Art. 8
  without parental consent). Consider COPPA exposure and whether any
  marketing or features could be attractive to minors.
- **International transfer mechanisms** — confirm that Standard
  Contractual Clauses are in place with each sub-processor, that the UK
  addendum is signed where UK users are involved, and that adequacy
  decisions (e.g., EU-US Data Privacy Framework) are tracked. Consider
  Swiss-US, Japan, South Korea, and other regional rules once the user
  base expands.
- **CCPA thresholds** — confirm whether Inspira meets the CCPA
  thresholds at current scale. Even if not strictly required, the doc
  treats California residents as if it does. Review the "sensitive
  personal information" list and the Service's handling of login
  credentials (treated as sensitive under CCPA).
- **EU / UK representative appointment** — at current scale and target
  market, is an Article 27 EU representative required? If yes, appoint
  one and list their contact details. Same question for the UK.
- **Physical mailing address** — add once the entity is incorporated;
  required under CCPA.

## `docs/legal/terms-of-service.md`

- **Governing-law forum** — confirm Delaware is the correct forum for a
  Delaware-formed entity, or recommend an alternative (e.g., the
  founder's home state) and update this clause. Re-evaluate once the
  Company is formally incorporated.
- **Arbitration / class-action waiver enforceability** — arbitration
  clauses and class-action waivers are subject to rapidly evolving
  state-law limits (e.g., California, New York). Confirm enforceability
  of this clause in target markets, the appropriate arbitral forum,
  fee-shifting rules under the AAA Consumer Rules, mass-arbitration
  protections, and whether a 30-day opt-out is sufficient.
