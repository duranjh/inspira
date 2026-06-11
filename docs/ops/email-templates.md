# Email templates

Reference doc for Inspira's transactional email templates. Covers purpose,
trigger, required context variables, optional variables, and a sample
rendered body for each. Product owns the copy.

- **Code lives at:** `services/planning_studio_service/mail/`
- **Templates live at:** `services/planning_studio_service/mail/templates/<name>.html` and `.txt`
- **Wiring status:** not yet wired into `auth.py` or `api.py`. The sender
  abstraction is in place; flipping a real provider on is a one-line
  change in `get_email_sender()` plus adding the concrete sender class.

## Tone rules (binding)

- Warm editorial. Sentences meant to be read out loud.
- No emoji. No exclamation points. Em-dashes are fine.
- "You" addresses the reader. "We" speaks for Inspira (a small team).
- Never "workflow", "productivity", "AI-powered", "your AI assistant".
- American English.
- Subject lines end with a period.

## From header

All templates use the same sender identity by default:

- `from_name`: **Inspira**
- `from_email`: **hello@example.com** (placeholder — set `INSPIRA_EMAIL_FROM`)

When a provider is wired up, this domain needs SPF + DKIM + DMARC
configured. Override per-template by editing the `Template` entry in
`mail/templates/__init__.py` if a campaign needs a different `From`.

## Rendering

`render(template_id, context) -> (subject, html_body, text_body)` lives
in `planning_studio_service.mail.templates`. It uses plain
`str.format` — no Jinja — and **validates that every `{name}`
placeholder** in the subject, HTML body, and text body has a matching
key in the `context` dict. Missing keys raise `ValueError` so the bug
shows up in tests, not in a half-rendered email that reaches a user.

**Conditional sections.** `str.format` has no `{% if %}`, so templates
that need optional copy (account deletion with/without a data export;
budget warning with/without a Pro upgrade CTA) expose a pre-composed
block placeholder — `{export_block}`, `{upgrade_block}` — whose value
is either the full rendered snippet or the empty string. **The caller
composes that snippet in Python.** Keeping the conditional in code
means we can unit-test it, and the template files stay copy-only.

---

## 1. `welcome`

- **Purpose:** Thank a new user for signing up, remind them what Inspira
  is, and invite them to start their first map.
- **Trigger (not wired yet):** `auth.py::signup_route` after
  `store.create_user` succeeds. The send is fire-and-forget — a failed
  send must not block the signup response.
- **Owner of copy:** Product.
- **Subject:** `Welcome to Inspira.`
- **Required context:**
  | key | type | example |
  |---|---|---|
  | `display_name` | str | `"Sam"` |
  | `app_url` | str | `"https://tryinspira.com"` |

### Sample rendered text body

```
Welcome, Sam.

Thank you for making an account. Inspira believes every idea deserves
a canvas. You bring an idea, and the canvas opens around it —
connected topics as cards you can move, rename, or cut.

Click any card and a planner begins a conversation about that piece of
the idea, asking the kind of questions you would want a patient
collaborator to ask.

Whenever you are ready, start your first map:
https://tryinspira.com

We are a small team. If something gets in the way of your thinking,
reply to this note and we will read it.

Warmly,
The Inspira team
```

---

## 2. `password_reset`

- **Purpose:** Give the user a time-limited link to set a new password.
- **Trigger (not wired yet):** a future `POST /api/auth/password-reset`
  route in `auth.py` that generates a signed token with 1-hour TTL,
  stores its hash alongside the user row, and hands the plaintext
  token to the sender inside `reset_link`. Never log the plaintext
  token or store it unhashed.
- **Owner of copy:** Product.
- **Subject:** `Reset your Inspira password.`
- **Required context:**
  | key | type | example |
  |---|---|---|
  | `display_name` | str | `"Sam"` |
  | `reset_link` | str | `"https://tryinspira.com/auth/reset?token=..."` |
  | `expires_in_human` | str | `"one hour"` |

### Sample rendered text body

```
Reset your password, Sam.

You asked to reset your Inspira password. Open the link below to choose
a new one. It works for one hour and then expires.

https://tryinspira.com/auth/reset?token=abc123

Did not request this? You can ignore this email. Your password will
not change.

—
The Inspira team
```

---

## 3. `account_deleted`

- **Purpose:** Confirm the deletion landed and every row tied to the
  account is gone. If we emitted a data export first, link to it.
- **Trigger (not wired yet):** a future
  `POST /api/auth/account/delete` route that cascades deletes across
  `projects`, `topics`, `decisions`, `qna_turns`, `relationships`, and
  `user_usage` for that `user_id`. Send AFTER the DELETE commits —
  otherwise a rollback leaves the user with a goodbye email and an
  intact account.
- **Owner of copy:** Product.
- **Subject:** `Your Inspira account is deleted.`
- **Required context:**
  | key | type | example |
  |---|---|---|
  | `display_name` | str | `"Sam"` |
  | `export_block` | str | see below — either `""` or a composed snippet |

### Composing `export_block`

Because `str.format` can't branch, the caller composes this string:

```python
if export_link:
    export_block = (
        f"A copy of everything you wrote is available to download for "
        f"the next 30 days here: {export_link}"
    )
else:
    export_block = ""
```

### Sample rendered text body (with export)

```
Your account is deleted, Sam.

We have closed your Inspira account. Every project, topic, decision, and
Q&A turn tied to it has been removed from our systems. Nothing remains
behind a login screen.

A copy of everything you wrote is available to download for the next 30 days here: https://tryinspira.com/export/xyz

Thank you for spending some of your thinking with us. If you want to
come back someday, you can sign up again with the same email.

Warmly,
The Inspira team
```

### Sample rendered text body (no export)

```
Your account is deleted, Sam.

We have closed your Inspira account. Every project, topic, decision, and
Q&A turn tied to it has been removed from our systems. Nothing remains
behind a login screen.

Thank you for spending some of your thinking with us. If you want to
come back someday, you can sign up again with the same email.

Warmly,
The Inspira team
```

---

## 4. `budget_warning`

- **Purpose:** Gentle heads-up that the user is near today's per-user
  token cap. Not punitive. Explains when the budget resets, and (once
  Pro exists) offers the lift.
- **Trigger (not wired yet):** `api.py::_record_llm_usage` — after
  writing usage, compare `(tokens_in + tokens_out)` against
  `_load_user_daily_token_budget()`. When the user first crosses 80%
  for the day, enqueue one send. Use a once-per-day guard in the
  `user_usage` row (e.g. `budget_warned_at`) so a user doesn't get a
  stream of these as they keep working.
- **Owner of copy:** Product.
- **Subject:** `You've used most of today's planner budget.`
- **Required context:**
  | key | type | example |
  |---|---|---|
  | `display_name` | str | `"Sam"` |
  | `percent_used` | str | `"85"` — no % sign; the template adds it |
  | `resets_at_human` | str | `"midnight UTC"` |
  | `upgrade_block` | str | either `""` or a composed snippet |

### Composing `upgrade_block`

```python
if upgrade_link:
    upgrade_block = (
        f"When Pro lands, the cap lifts. You can read what it includes "
        f"here: {upgrade_link}"
    )
else:
    upgrade_block = "When Pro lands, the cap lifts. You will be among the first to hear."
```

### Sample rendered text body

```
A gentle note, Sam.

You have used about 85% of today's planner budget. The
planner will keep answering until you reach the cap, and then pause
until the budget resets at midnight UTC.

This is a soft signal, not a penalty. If you are mid-thought, keep
going — you will know when you hit the limit.

When Pro lands, the cap lifts. You will be among the first to hear.

Warmly,
The Inspira team
```

---

## Activating real sending

Today all sends are routed to `NoopEmailSender`, which logs the
rendered email and returns. To cut over to a real provider:

1. **Pick a provider.** Current reserved strings in
   `get_email_sender()`: `resend`, `postmark`, `loops`. All three are
   reasonable for a small team — Resend has the friendliest API, Postmark
   has the best deliverability story for transactional mail, Loops adds
   marketing-sequence features we do not yet need. Recommendation unless
   there is a reason otherwise: **Resend** for speed to ship, **Postmark**
   if deliverability is the primary concern.
2. **Verify the sending domain.** The provider will ask for SPF, DKIM,
   and DMARC records on `tryinspira.com`. Set these up in DNS before
   flipping the flag; unverified domains hit the spam folder
   immediately and the reputation is hard to recover.
3. **Mint an API key** in the provider dashboard. Scope it to "send
   only" if the provider supports it.
4. **Store the key as a secret.** `RESEND_API_KEY` /
   `POSTMARK_SERVER_TOKEN` / `LOOPS_API_KEY`, injected via the deploy
   platform's secret store — never a committed `.env`.
5. **Implement the concrete sender class** in
   `services/planning_studio_service/mail/sender.py`. The class needs
   one method: `send(self, *, to_email, template_id, context) -> None`.
   Call `mail.templates.render(template_id, context)` to get
   `(subject, html, text)`, then hit the provider API. Wrap transport
   errors in `RuntimeError` so the caller can decide whether to retry.
6. **Wire it into `get_email_sender()`** — replace the
   `NotImplementedError` branch with `return FooSender()`.
7. **Call it from the three trigger sites:**
   `auth.py::signup_route` (welcome), a new password-reset route
   (password_reset), the upcoming account-delete route
   (account_deleted), and `api.py::_record_llm_usage` (budget_warning).
   Wrap each call in try/except and log on failure — the user action
   must never fail because of a mail hiccup.
8. **Add a smoke test** that sends a live email to a team inbox, so
   the provider wiring is exercised in CI rather than discovered by
   the first real user.

Until step 1 happens, keep `EMAIL_PROVIDER` unset in production —
`NoopEmailSender` is the safe default and the logs give a full audit of
what would have been sent.
