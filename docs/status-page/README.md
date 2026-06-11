# Inspira status page

Self-contained single-file HTML status page, hosted at
`status.tryinspira.com`. The goal is a page a user can reach in 100 ms
from a page that cannot load — so the page must not depend on the main
backend, the main frontend, or the database.

Files in this directory:

| File | Purpose |
| --- | --- |
| `status-page.html` | The static page. Self-contained; inline CSS and JS. Warm editorial palette mirroring the app. |
| `incidents.json` | Live data the page reads client-side. Hand-edited today; automated tomorrow. |
| `incidents-example.json` | Schema example with one realistic, resolved major incident. Copy to `incidents.json` as a starting point. |

The page renders fine when opened directly (`file://`) — no web server
needed. If the `fetch("./incidents.json")` fails, the defaults in
`DEFAULT_DATA` keep the page rendering as "All systems operational"
with a small italic note in the footer that status data is unavailable.

---

## 1. Hosting — the shortest path

**Pick one.** All three keep the page on a separate host from the
backend, so an outage of the app doesn't take the status page with it.

### Option A — Netlify (~5 minutes)

1. Drag-and-drop this directory onto `https://app.netlify.com/drop`. A
   randomly-named site appears with `status-page.html` live.
2. Rename `status-page.html` to `index.html` **before** the upload so
   the root URL serves the page directly.
3. In the Netlify UI, add the domain `status.tryinspira.com`. Point the
   DNS `CNAME` at the Netlify-provided host.
4. TLS provisions automatically (Let's Encrypt).
5. To update: re-drop the directory, or connect Netlify to a Git repo
   watching `docs/status-page/`.

### Option B — Cloudflare Pages (if DNS is on Cloudflare)

1. Cloudflare Pages &rarr; Create &rarr; Direct upload. Upload the
   contents of this directory.
2. Rename `status-page.html` to `index.html` first.
3. Add `status.tryinspira.com` as a custom domain; Cloudflare handles
   the DNS record and cert automatically.
4. To update: re-upload, or plug it into a GitHub repo so Cloudflare
   Pages rebuilds on merge to `main`.

### Option C — Subroute of the main deploy

If you want one deploy pipeline, serve `docs/status-page/` under a
subroute of the backend or the app's static host. Downside: a total
backend outage takes the status page with it, which defeats the
purpose. Only use this in the very early days; migrate to A or B
once the product has real traffic.

Suggested routing (nginx in front of the app):

```
location = /status/ {
  alias /srv/inspira/docs/status-page/;
  try_files $uri $uri/status-page.html =404;
}
```

---

## 2. Updating the JSON blob

The page reads `./incidents.json` in the same directory. The schema is
defined in `incidents-example.json`; see section 4 for the shape.

### 2.1 During a quiet period

Edit `incidents.json` and re-upload. Example "everything is healthy":

```json
{
  "current_status": "operational",
  "components": [
    { "name": "Website",        "status": "operational", "message": "" },
    { "name": "App",            "status": "operational", "message": "" },
    { "name": "Planner (LLM)",  "status": "operational", "message": "" },
    { "name": "Database",       "status": "operational", "message": "" },
    { "name": "Email delivery", "status": "operational", "message": "" }
  ],
  "incidents": [],
  "last_updated": "2026-04-20T10:00:00Z"
}
```

### 2.2 During an incident

1. Flip `current_status` to `"degraded"` or `"outage"`.
2. Flip the affected component row(s) to `"degraded"` / `"outage"` and
   add a one-line `message` explaining the user-visible symptom (not
   the internal cause).
3. Append a new entry to `incidents`:
   - `id`: `inc-YYYY-MM-DD-short-slug`.
   - `severity`: `minor` (SEV-3), `major` (SEV-2), `critical` (SEV-1).
   - `started_at`: ISO-8601 UTC timestamp of first detection.
   - `affected_components`: array of component names (used to color
     the 90-day dot strip).
   - `status_updates[0]`: the "Investigating" update, per the
     playbook in `docs/ops/incident-response.md` Section 5.
4. Re-upload the file.
5. As the incident progresses, append new entries to `status_updates`
   (Identified &rarr; Monitoring &rarr; Resolved). Re-upload on each
   update — the page cache-busts the fetch with a `?t=<timestamp>`
   query string so users see the fresh state within seconds.
6. When resolved, set `resolved_at`, set `current_status` back to
   `"operational"`, and flip component statuses back to `"operational"`.
7. Bump `last_updated` on every edit.

Suggested update cadence per severity (mirrors the incident-response
playbook):

| Severity | Cadence | Example |
| --- | --- | --- |
| `critical` | Every 30 min until resolved. | Complete outage, data loss. |
| `major` | Every 60 min until resolved. | Core flow down for most users. |
| `minor` | On state changes only. | One endpoint broken, fallback exists. |

---

## 3. Integrating with a third-party uptime service (future)

The page is provider-agnostic: it reads a JSON file. The fastest path
to automated updates is to have a monitoring service *write* that
JSON on a schedule.

### 3.1 BetterStack (recommended — free tier)

1. Sign up, create an **Uptime monitor** for
   `https://api.tryinspira.com/api/health`. 1-minute interval.
2. Create a **Status page** inside BetterStack (they host it too — you
   can either mirror this page or run both in parallel).
3. Optional: use BetterStack's **Incidents API** to sync incident
   state into this repo's `incidents.json` via a nightly cron. The
   script ships under `scripts/sync-betterstack-incidents.py` when we
   wire it up.
4. Page-able channels: SMS to the on-call number, email to
   `on-call@tryinspira.com`.

### 3.2 UptimeRobot

1. Create a monitor for `/api/health` on a 5-minute interval (free
   tier cap).
2. Enable "Public status page" &mdash; not a replacement for this one
   (we keep our own so the branding and copy are ours), but useful as
   a redundant signal.
3. Wire alerts to email + Slack.

### 3.3 Pingdom

Paid, use only if we outgrow the free tiers. Pros: more detailed
transaction monitoring (synthetic checks of kickoff &rarr; topic_turn).
Cons: price. Revisit after 1,000 active users.

### 3.4 Automating the JSON blob

Short-term, keep `incidents.json` hand-edited — one operator, low
traffic. Once we pass two incidents a month or a second operator
joins, wire the sync:

1. Monitoring service writes incidents to its own store.
2. A cron job (GitHub Actions, `scheduled-tasks` MCP, or a tiny
   function-as-a-service) pulls the feed, transforms to our schema,
   commits to this directory, and the static host redeploys.
3. Keep a **manual override**: a boolean `manual_override` in the JSON
   means "humans are in charge right now, do not overwrite." The sync
   job reads it first and bails if true.

---

## 4. Schema

```ts
type Status = "operational" | "degraded" | "outage";
type Severity = "minor" | "major" | "critical";

interface StatusPayload {
  current_status: Status;
  components: Array<{
    name: string;       // "Website" / "App" / "Planner (LLM)" / ...
    status: Status;
    message: string;    // one-line user-facing note; empty string when operational
  }>;
  incidents: Array<{
    id: string;
    title: string;
    severity: Severity;
    started_at: string;            // ISO-8601 UTC
    resolved_at: string | null;    // null while ongoing
    affected_components: string[]; // must match component.name exactly
    status_updates: Array<{
      at: string;                  // ISO-8601 UTC
      message: string;             // neutral, factual, no internal jargon
    }>;
  }>;
  last_updated: string;            // ISO-8601 UTC
}
```

Validation rules (enforced by the operator, not by the page):

- `components[].name` in `incidents[].affected_components` must match
  an entry in `components`. Otherwise the 90-day dot strip for that
  component won't color the incident day.
- `status_updates` should read chronologically. The page only shows
  the *last* update text under each incident title; keep it fresh.
- `last_updated` is the source of truth for "when this page was last
  touched by a human or job" and is shown in the footer. Always bump
  it on edits.

---

## 5. Copy rules

Follow the same voice rules the app uses (see `CLAUDE.md`'s Inspira
product vision note):

- Neutral, factual, declarative. No marketing voice, no apology
  spiral, no speculation about root cause before a post-mortem.
- Use plain terms. "Users cannot sign in" &mdash; not "auth subsystem
  experiencing anomalous behavior."
- Never name an external vendor on the public page during an
  incident. Say "our LLM provider is experiencing elevated latency,"
  not "OpenAI is down." The post-mortem is where specifics go.
- No emojis on the status page. Ever.

---

## 6. Maintenance

- Do a full read-through of this README quarterly, same cadence as
  `docs/ops/runbook.md`.
- When the component set changes (new surfaces: a real-time
  connection, a mobile app, etc.), update both `DEFAULT_COMPONENTS`
  in `status-page.html` and the template `incidents.json`.
- Keep this page on a separate host from the backend and the app.

Companion documents:

- `docs/ops/runbook.md` &mdash; daily operations.
- `docs/ops/incident-response.md` &mdash; severity definitions, templates.
- `docs/ops/monitoring.md` &mdash; what we monitor and why.
- `docs/ops/sla-targets.md` &mdash; internal targets (not public).
