# highlevel-mcp

MCP server for **HighLevel (GoHighLevel)** — a CRM and marketing-automation platform used by agencies to run their clients' sales and marketing. Gives an MCP-compatible AI client read access to a connected sub-account's (Location's) contacts, sales pipelines/opportunities, calendar appointments, and conversations, plus narrow, auditable write access to contacts.

<div align="center">

## MSPbots Connector

**The MCP Gateway built for MSPs: visible, controlled, and your data stays where it belongs.**

[**Get started at connect.mspbots.ai →**](https://connect.mspbots.ai)

</div>

---

MSPbots Connector gives your AI clients one MCP URL to reach your entire MSP stack (RMM, PSA, documentation, security, finance), and gives you control over who can call which tool and a record of everything that was called.

**Hosted service: [connect.mspbots.ai](https://connect.mspbots.ai)** · Free to use.

### Why Connector

![MSPbots Connector](https://stgoss.mspbots.ai/files/mcp/mspbots-mcp.png)

| | |
|---|---|
| **Visible** | Every tool call is logged: who, which vendor, when. Usage dashboard, CSV export, SIEM streaming. |
| **Controlled** | Role-based, per-tool allowlist. Deny by default. |
| **Protected** | Credentials encrypted per record. AI clients never see your vendor passwords or tokens. |
| **MSP-native** | Built for the MSP stack only: RMM, PSA, documentation, security, finance. |

### Quick start

1. **Sign up** at [connect.mspbots.ai](https://connect.mspbots.ai).
2. **Connect your vendors.** Pick from 150+ connectors and authorize each one on the vendor's own page, or bring your own API keys. No extra vendor accounts needed.
3. **Set access.** Create roles and decide which tools each role may call. Nothing is allowed until you allow it.
4. **Add your MCP URL to your AI client.** Copy the URL from your Connector dashboard into Claude, ChatGPT, Cursor, VS Code, Gemini, Codex, or any MCP-capable client.
5. **Ask your AI to do real work**, and watch every call appear in the audit log.

### Features

**Unified gateway**
- One MCP URL for all your AI clients
- 150+ connectors across the MSP stack
- Custom MCP servers supported, free
- Authorization happens on each vendor's own page; the AI never sees passwords or tokens
- Bring your own credentials: use the vendor API keys you already have
- Multiple accounts per app; connect once, reuse across AI clients
- Tool search: finds the right action for the task
- One sign-in; vendor credentials are injected behind the scenes and OAuth tokens refresh automatically

**Visibility**
- Audit log for every tool call (who, which vendor, when)
- Usage dashboard: call volume, top vendors, error rate, per-user activity
- Export audit logs to CSV, or stream to Loki, Graylog, or LogScale (SIEM)
- Optional recording of tool arguments and result summaries (off by default, enabled per organization)

**Control**
- Per-role, per-tool allowlist, deny by default
- owner / admin / member roles
- SCIM provisioning with Entra ID, Okta, JumpCloud, and Google Workspace

**Security**
- Credentials encrypted one record at a time (AES-256-GCM, per-record key)
- AI clients authenticate with OAuth 2.1 + PKCE
- Vendor responses are scanned for prompt-injection patterns and returned as structured data. This is one layer of defense, not a guarantee against every attack.
- Static egress IP for customer firewall and API allowlisting

### Pricing

**Free.**

Links: [connect.mspbots.ai](https://connect.mspbots.ai) · [mspbots.ai](https://mspbots.ai)

---

## Overview

Implements the [Model Context Protocol](https://modelcontextprotocol.io/) (Streamable HTTP transport) and exposes **12 tools** across four resource families: **contacts** (search/get, plus four light-touch writes), **opportunities/pipelines** (read), **calendar** (read), and **conversations** (read). An agent should reach for this MCP for requests like:

- "Find the contact for jane@example.com" → `highlevel_search_contacts`
- "Log a note on this contact that I called them" → `highlevel_add_contact_note`
- "What pipelines and stages do we have set up?" → `highlevel_list_pipelines`
- "What's on the calendar for this client next week?" → `highlevel_list_appointments`
- "Show me the conversation history with this contact" → `highlevel_list_conversations` then `highlevel_get_conversation_messages`
- "Tag this contact as VIP and update their phone number" → `highlevel_add_contact_tags` and `highlevel_update_contact`

There are **no delete tools** and **no payments / webhook tools** in this version (see Known Gaps / Scope below).

| Category | Tool | Annotations |
|---|---|---|
| Contacts | `highlevel_search_contacts`, `highlevel_get_contact` | readOnly |
| | `highlevel_create_contact`, `highlevel_add_contact_note` | — |
| | `highlevel_update_contact`, `highlevel_add_contact_tags` | idempotent |
| Opportunities | `highlevel_list_pipelines`, `highlevel_list_opportunities` | readOnly |
| Calendar | `highlevel_list_calendars`, `highlevel_list_appointments` | readOnly |
| Conversations | `highlevel_list_conversations`, `highlevel_get_conversation_messages` | readOnly |

## Quick Start

### Docker (recommended)

```bash
docker compose up --build
```

The server starts on `http://localhost:8080`.

### Local (uv)

```bash
uv sync
python -m highlevel_mcp
```

## Health Check

```bash
curl http://localhost:8080/health
# {"status": "ok"}
```

No credentials are required for the health endpoint.

## Authentication headers

Every request to `/mcp` must include the following HTTP headers:

| Header | Type | Required | Default | Allowed values | Description | Example |
|---|---|---|---|---|---|---|
| `X-HighLevel-Private-Token` | password | Yes | None | None (free text) | HighLevel Private Integration Token, generated in the target sub-account's Settings → Private Integrations. Forwarded as `Authorization: Bearer <token>` to the HighLevel API. | `X-HighLevel-Private-Token: pit-xxxxxxxx` |
| `X-HighLevel-Location-Id` | str | Yes | None | None (free text) | The HighLevel sub-account (Location) ID this token is scoped to. Required on every call regardless of whether the token is already Location-scoped (safe either way — see Known Gaps). | `X-HighLevel-Location-Id: ve9EPM428h8vShlRW1KT` |

Missing either header returns `401 Unauthorized` with a body naming both required headers.

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `MCP_HTTP_PORT` | `8080` | Listening port |
| `MCP_HTTP_HOST` | `0.0.0.0` | Listening host |

No credential ever lives in an environment variable — see Authentication headers above.

## MCP Endpoint

```
POST http://localhost:8080/mcp
```

Connect your MCP client with:
- Transport: `http` (Streamable HTTP, stateless — no `initialize` handshake required before `tools/list` or `tools/call`)
- Headers: `X-HighLevel-Private-Token`, `X-HighLevel-Location-Id` (both required)

## Test Example

```bash
curl -X POST http://localhost:8080/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "X-HighLevel-Private-Token: <token>" \
  -H "X-HighLevel-Location-Id: <location-id>" \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tools/call",
    "params": {
      "name": "highlevel_search_contacts",
      "arguments": {"query": "jane"}
    }
  }'
```

## Pagination

All list tools share one contract regardless of HighLevel's own pagination mechanism underneath: `limit` (default 50, hard cap 200) and an opaque `cursor` string. When a result set is larger than the page returned, the response includes `has_more: true` and a `cursor` to pass back on the next call. The cursor never exposes HighLevel's native field names — it is an internal token for this server.

## API Reference

- Vendor docs: [highlevel.stoplight.io/docs/integrations](https://highlevel.stoplight.io/docs/integrations/) (official HighLevel developer portal), supplementary: [marketplace.gohighlevel.com/docs](https://marketplace.gohighlevel.com/docs)
- Base URL and the required `Version` header were confirmed live against the real API host on 2026-11-04 (not read from the JS-rendered docs site, which this implementation's tooling could not render): `https://services.leadconnectorhq.com`, `Version: 2021-07-28`. Every route this server calls was confirmed to exist the same way (a well-formed request with a placeholder bearer token returns `401 Invalid JWT`, not `404`) — see `src/highlevel_mcp/client.py`'s module docstring for the exact probes.
- Ticket: [PRD-21281](https://app.clickup.com/t/86e3v6wj5)

## Known Gaps / Scope

- **No payments / invoicing tools** — a separate, higher-risk API surface; deferred to a later ticket if needed.
- **No webhook subscription management** — receiving HighLevel webhooks needs a stateful subscription and a public callback endpoint, a different architecture than this stateless request/response MCP; out of scope for v1.
- **No delete tools of any kind.**
- **No OAuth 2.0 marketplace-app flow** — v1 uses a Private Integration Token only (see "Decisions made by DEV01" in [the ticket's tech design](docs/tickets/PRD-21281.md)). OAuth would only be needed if MSPbots later wants to list this as a HighLevel Marketplace app.
- **Private Integration Token Location-scoping assumption**: `X-HighLevel-Location-Id` is required on every call regardless of whether the token is already scoped to one Location — safe either way, and avoids a breaking contract change if that assumption turns out wrong for some account configurations.
- **AC6 (multi-tenant isolation), AC7 (write + read-your-write), AC10 (update/tag roundtrip), and the live-data half of AC3/AC4/AC8/AC9 need a real HighLevel sub-account to verify end to end** — not available during development (SOP "no test account is not a blocker"). The unit tests cover the connector's own logic (pagination, error mapping, header isolation) against a stubbed upstream; see `docs/tickets/PRD-21281.md`'s Self-test section for exactly which cases are "needs a real vendor account."
- **Local Docker build was not verified in the development sandbox** (no `docker` group membership there) — the Dockerfile follows the same multi-stage, non-root, `curl`-installed pattern already running in production for sibling MCP repos in this org; verify on first INT deploy.
