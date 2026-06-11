# Inspira — Claude MCP Connector

Connect Inspira to Claude.ai as a custom **MCP server** so Claude can
read and write your canvas on your behalf, using your existing Claude
Pro subscription. Claude runs the reasoning; Inspira persists the
resulting canvas. No LLM tokens are billed to your Inspira account —
we simply store whatever the model on the other end says you said.

The integration uses the **Model Context Protocol** (MCP). Inspira
exposes the MCP server over HTTPS at `https://mcp.tryinspira.com`. The
same 11 tool operations are available to ChatGPT Custom GPTs via
OpenAPI — see `docs/integrations/chatgpt-custom-gpt.md` for that path.

## Prerequisites

1. A Claude Pro or Claude Team subscription (MCP connectors are a
   paid feature).
2. An Inspira account at https://tryinspira.com.
3. A Personal Access Token minted from
   **Account Settings → API tokens**. You'll paste it into the Claude
   MCP connector dialog in step 2.

## 1. Server endpoint

Inspira's MCP server runs on:

```
https://mcp.tryinspira.com
```

- **Transport**: streamable HTTP (MCP spec).
- **Auth**: Bearer Personal Access Token.
  Header: `Authorization: Bearer inspira_pat_<hex>`
- **Base path**: `/mcp` (handled automatically by Claude's MCP client).

The server is stateless — every request authenticates independently,
so scaling out happens transparently on Fly.

## 2. Connect from Claude.ai

1. In Claude.ai, open **Settings → Connectors** (may appear as
   "Integrations" depending on your plan).
2. Click **Add custom connector → Remote MCP server**.
3. Fill the dialog:
   - **Name**: `Inspira`
   - **Server URL**: `https://mcp.tryinspira.com`
   - **Authentication**: `Bearer token`
   - **Token**: paste your `inspira_pat_...` value.
4. Save. Claude runs a handshake against the server — if auth works,
   you'll see the 11 tool names listed: `create_canvas`,
   `list_projects`, `list_topics`, `add_topic`, `update_topic`,
   `delete_topic`, `add_relationship`, `record_answer`, `add_decision`,
   `get_summary`, `export_markdown`.

## 3. Starter prompts

In a new Claude conversation, attach the Inspira connector and try:

- Plan a week-long solo writing retreat.
- Help me think through switching careers to design.
- Draft an outline for a 20-minute talk on deep work.
- What's on my Inspira canvas? Let's pick up where we left off.

## 4. Manifest JSON (alternative manual wiring)

For clients that want the raw manifest (e.g. a self-hosted Claude
workspace), the minimal config is:

```json
{
  "name": "Inspira",
  "description": "Inspira canvas — create projects, topics, decisions, and Q&A turns.",
  "server": {
    "transport": "streamable_http",
    "url": "https://mcp.tryinspira.com"
  },
  "auth": {
    "type": "bearer",
    "header": "Authorization",
    "token_prefix": "inspira_pat_"
  }
}
```

## 5. Rotate the PAT

PATs are long-lived. Revoke any token you're not using via
**Account Settings → API tokens → Revoke**. To rotate:

1. Mint a new token in Inspira.
2. In Claude.ai, open the Inspira connector and replace the token.
3. Revoke the old token.

## Troubleshooting

- **"Authentication failed"** — the PAT is wrong or has been revoked.
  Mint a fresh token in Inspira and update the connector.
- **"Connector offline"** — Inspira's MCP process may be waking from
  idle. Retry after a few seconds; if the issue persists, check the
  status page at https://tryinspira.com/status.
- **Tools missing from the list** — the server lists all 11 tools at
  connection time. If any are missing, the server build may be stale
  — open an issue.
- **403 / 429** — rate limits per PAT. Wait a few seconds and retry.

## Tool surface

| Tool              | Purpose                                     |
| ----------------- | ------------------------------------------- |
| create_canvas     | Spin up a new canvas from a one-line idea.  |
| list_projects     | List your active canvases.                   |
| list_topics       | List topics on a canvas.                    |
| add_topic         | Add a topic card.                           |
| update_topic      | Rename or change a topic's icon.            |
| delete_topic      | Soft-delete a topic.                        |
| add_relationship  | Draw a connection between two topics.       |
| record_answer     | Append a Q&A exchange to a topic.           |
| add_decision      | Record a decision on a topic.               |
| get_summary       | Get topic/decision counts + last updated.   |
| export_markdown   | Export the whole canvas as Markdown.        |
