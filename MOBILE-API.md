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
| `DELETE` | `/api/v1/chats/{id}` | Delete a chat after its runs have stopped |
| `POST` | `/api/v1/uploads` | Upload one file or image as raw bytes |
| `POST` | `/api/v1/chats/{id}/messages` | Post a user message |
| `POST` | `/api/v1/chats/{id}/questions/{question_id}/answer` | Answer an agent-authored choice question |
| `POST` | `/api/v1/chats/{id}/run-stream` | Run agents with streamed NDJSON events |
| `GET` | `/api/v1/runs/active` | Reconnect to active runs and task state |
| `POST` | `/api/v1/runs/{run_id}/cancel` | Stop a running chat or meeting |
| `POST` | `/api/v1/runs/{run_id}/tasks/{task_id}/cancel` | Stop one native task inside a run |
| `POST` | `/api/v1/chats/{id}/meeting` | Run an explicit meeting |
| `POST` | `/api/v1/chats/{id}/auto-meeting` | Start a separate head-agent meeting chat |

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

Upload a file before posting its message:

```bash
UPLOAD_ID="$(
  curl -sS -X POST http://127.0.0.1:8899/api/v1/uploads \
    -H 'Content-Type: application/pdf' \
    -H 'X-Agent-Slack-Filename: release%20report.pdf' \
    -H 'X-Agent-Slack-Server: <server-id>' \
    --data-binary @release-report.pdf |
  python3 -c 'import json,sys; print(json.load(sys.stdin)["attachment_id"])'
)"

curl -X POST http://127.0.0.1:8899/api/v1/chats/<chat-id>/messages \
  -H 'Content-Type: application/json' \
  -H 'X-Agent-Slack-Server: <server-id>' \
  -d "{\"body\":\"Analyze the attached report.\",\"attachment_ids\":[\"$UPLOAD_ID\"]}"
```

Uploads are limited to 50 MB each and ten attachments per message. The upload
response contains an opaque `attachment_id`; clients must submit that ID rather
than a filesystem path. The selected server validates the ID and adds the
server-owned absolute path to the agent transcript.

The stream emits one JSON object per line, including `run_started`,
`agent_started`, `delta`, `agent_completed`, `agent_failed`, and `run_completed`
events. A mobile client should process complete lines as bytes arrive and then
reload the chat after `run_completed`. Closing the stream does not cancel the
backend run; persisted results remain available through the chat endpoint.

Reconnect after an app restart or a dropped stream:

```bash
curl http://127.0.0.1:8899/api/v1/runs/active?chat_id=<chat-id> \
  -H 'X-Agent-Slack-Server: <server-id>'
```

Stop a run or one active task:

```bash
curl -X POST http://127.0.0.1:8899/api/v1/runs/<run-id>/cancel \
  -H 'X-Agent-Slack-Server: <server-id>'

curl -X POST http://127.0.0.1:8899/api/v1/runs/<run-id>/tasks/<task-id>/cancel \
  -H 'X-Agent-Slack-Server: <server-id>'
```

Delete a chat only after `GET /api/v1/runs/active` shows no active run for that
chat. `DELETE /api/v1/chats/{id}` returns HTTP `409` when the chat still owns an
active run, so a mobile client should stop the run first and then retry the
delete request.

## Agent-authored questions

## Voice and created files

`GET /api/v1/voice/capabilities` advertises the opt-in local voice channel and
file preview support. A client submits one committed utterance to
`POST /api/v1/voice/utterances` with `chat_id`, `text`, and a stable
`request_id`; the response is the same NDJSON run stream used by text clients.
Retrying the same request ID does not append another user message or start a
second voice request.

Message attachment metadata includes an opaque `attachment_id`. User uploads
are previewed at `/api/v1/uploads/{attachment_id}`. Files newly created by an
agent are copied into Agent Slack-owned artifact storage and appear in the
completed agent message's `metadata.attachments` with a `/api/artifacts/...`
`url`. Clients should open that URL on click and should not use the stored
server-side `path` as a client URL.

An agent that needs user input persists an ordinary agent message whose
`metadata.interaction` defines the UI:

```json
{
  "kind": "choice",
  "question_id": "9ce6...",
  "prompt": "Approve the specification?",
  "multiple": false,
  "status": "pending",
  "selected_option_ids": [],
  "options": [
    {
      "id": "approve",
      "label": "Approve",
      "description": "Begin implementation."
    },
    {
      "id": "revise",
      "label": "Request changes",
      "description": "Return to specification work."
    }
  ]
}
```

Clients render the supplied `prompt`, `label`, and `description` values rather
than maintaining a built-in approval list. Submit selected IDs with:

```http
POST /api/v1/chats/{chat_id}/questions/{question_id}/answer
Content-Type: application/json
X-Agent-Slack-Server: <server_id>

{"option_ids":["approve"]}
```

The response is the updated chat. The question is marked `answered`, the
selected IDs are recorded, and a linked user message is appended. Start the
next `/run-stream` turn using that answer so the agent can continue. Repeated,
unknown, empty, or invalid-cardinality answers return `400`.

## Security Boundary

Agent Slack deliberately has no token authentication. LAN mode therefore gives
devices on that trusted network the ability to read chats and invoke local
agents. Do not expose port `8899` directly to the public internet. Remote access
must use an authenticated reverse proxy, VPN, or private tunnel; authentication
can be added before a public phone release without changing the versioned
resource model.
