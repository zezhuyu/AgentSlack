# Agent Slack Mobile API

Agent Slack can remain active as a background macOS server after its desktop
window closes. A future native phone app can use the same persisted servers,
agents, chats, meetings, and streamed CLI responses through HTTP API version 1.

## Start the Background Server

In the macOS app menu bar:

1. Enable **Launch at Login** to start Agent Slack with macOS.
2. Leave **Allow Trusted LAN Access** enabled to accept connections from the local network.
3. Choose **Copy API URL** and use that URL as the phone app's server URL.

The server binds to `0.0.0.0:8899` by default and the menu displays a reachable
private-network address. `AGENT_SLACK_IP` and `AGENT_SLACK_PORT` override these
values globally. Closing the window only hides it; choose **Quit Agent Slack
Server** to stop the backend.

For a headless terminal process:

```bash
python subsystems/agent_slack/run.py \
  --host 0.0.0.0 \
  --port 8899 \
  --data-root "$HOME/Library/Application Support/Agent Slack/data"
```

## Discover the API

```bash
curl http://127.0.0.1:8899/api/v1
curl http://127.0.0.1:8899/api/v1/openapi.json
curl http://127.0.0.1:8899/api/v1/health
```

Every response includes `X-Agent-Slack-Api-Version: 1`. The bundled browser
continues to use legacy `/api/*` aliases, but external clients should always use
`/api/v1/*`.

## Select an Agent System

List registered servers and retain the selected `server_id`:

```bash
curl http://127.0.0.1:8899/api/v1/servers
```

On a fresh installation, `GET /api/v1/agents` and `GET /api/v1/chats`
return empty collections. Server-scoped write endpoints return
`code: server_not_configured` with the setup endpoint until an agent-system
folder is registered.

Send it on agent-system-scoped requests:

```text
X-Agent-Slack-Server: <server-id>
```

Without this header, requests use the desktop app's active server.

## Core Endpoints

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `GET` | `/api/v1/health` | Service, server, and architecture status |
| `GET` | `/api/v1/servers` | Registered agent systems |
| `POST` | `/api/v1/servers` | Register an agent-system folder |
| `POST` | `/api/v1/servers/{id}/activate` | Change the active server |
| `GET` | `/api/v1/agents` | Discoverable people for the selected server |
| `POST` | `/api/v1/agents/discover` | Refresh agent definitions and manifest |
| `GET` | `/api/v1/chats` | Sidebar chat list |
| `POST` | `/api/v1/chats` | Create a direct or group chat |
| `GET` | `/api/v1/chats/{id}` | Full persisted chat and meeting history |
| `POST` | `/api/v1/chats/{id}/messages` | Post a user message |
| `POST` | `/api/v1/chats/{id}/run-stream` | Run agents with streamed NDJSON events |
| `POST` | `/api/v1/chats/{id}/meeting` | Run an explicit meeting |
| `POST` | `/api/v1/chats/{id}/auto-meeting` | Run manifest-based participant routing |

Create a direct message:

```bash
curl -X POST http://127.0.0.1:8899/api/v1/chats \
  -H 'Content-Type: application/json' \
  -H 'X-Agent-Slack-Server: <server-id>' \
  -d '{"title":"Researcher","kind":"direct","member_ids":["researcher"]}'
```

Post a message, then stream the reply:

```bash
curl -X POST http://127.0.0.1:8899/api/v1/chats/<chat-id>/messages \
  -H 'Content-Type: application/json' \
  -H 'X-Agent-Slack-Server: <server-id>' \
  -d '{"body":"Analyze the current release risk."}'

curl -N -X POST http://127.0.0.1:8899/api/v1/chats/<chat-id>/run-stream \
  -H 'Content-Type: application/json' \
  -H 'X-Agent-Slack-Server: <server-id>' \
  -d '{"mode":"respond","agent_ids":["researcher"]}'
```

The stream emits one JSON object per line, including `run_started`,
`agent_started`, `delta`, `agent_completed`, and `run_completed` events. A
mobile client should process complete lines as bytes arrive and then reload the
chat after `run_completed`.

## Security Boundary

Agent Slack deliberately has no token authentication. LAN mode therefore gives
devices on that trusted network the ability to read chats and invoke local
agents. Do not expose port `8899` directly to the public internet. Remote access
must use an authenticated reverse proxy, VPN, or private tunnel; authentication
can be added before a public phone release without changing the versioned
resource model.
