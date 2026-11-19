# Engram memory connector

This optional adapter uses Agent Slack's existing MCP server to call Engram's authenticated remote MCP server. It does not replace Agent Slack's project shared memory or create a mirror. Existing tunnel support in `macos/main.js` remains separate: it provides ingress for the Agent Slack MCP service, while the Engram bridge connects outbound to the configured Engram URL. For a private Engram instance, configure an HTTPS endpoint reachable from the Agent Slack host; a local loopback URL is allowed only for local development.

Set `AGENT_SLACK_ENGRAM_MCP_URL` and `AGENT_SLACK_ENGRAM_WORKSPACES_JSON`. The JSON maps each Agent Slack workspace key to a fixed Engram workspace and a token environment variable:

```sh
AGENT_SLACK_ENGRAM_MCP_URL=https://memory.example.org/mcp
AGENT_SLACK_ENGRAM_WORKSPACES_JSON='{"project":{"tokenEnv":"ENGRAM_PROJECT_TOKEN","allowWrites":true,"scope":{"type":"workspace","workspaceId":"project"},"collectionId":"shared","allowDelivery":true,"deliveryChats":[{"chatId":"<direct-chat-id>","enabled":true,"userOptIn":true}]}}'
ENGRAM_PROJECT_TOKEN=<secret from a secret manager>
```

The token must be a dedicated scoped Engram HTTP credential with matching vault ACLs. Create separate tokens per Agent Slack workspace. Search and get use `engram_search` / `engram_get`; create and patch use `engram_create` / `engram_patch`. Writes require both `allowWrites: true` and an Engram write grant. Create requires an idempotency key and records `agent-slack` as the source; patch additionally requires the current expected revision ID. Engram's `createdBy` records the mapped service actor. This adapter does not validate individual human senders, so it must not label agent-paraphrased input as user-authored evidence. Timeout errors tell the caller to retry with the same key.

New MCP tools are `search_engram_memory`, `get_engram_memory`, `create_engram_memory`, `patch_engram_memory`, and `deliver_engram_memory`. The delivery tool fetches the authorized current revision and persists a bounded, source-cited Engram message in an existing Agent Slack direct chat only when that exact chat is configured with `enabled: true` and `userOptIn: true`. It verifies the destination is a direct chat, enforces a per-chat rate limit of 10 messages/hour, and deduplicates by idempotency key. Delivery reports `accepted` after local chat persistence; the UI shows it through its existing chat history. There is no external push receipt or arbitrary channel/multi-recipient delivery. The user consent and chat allowlist are independent of the service's Engram read grant. Retrieved Markdown is marked as untrusted data. This adapter bounds response size, rejects public plaintext HTTP and redirects, and never accepts a caller-provided Engram workspace scope.


## Setup checklist (verified against a live Engram service)

A credential-bound actor needs **both** of these on the Engram side, otherwise searches silently return no hits instead of an error:

1. The credential's own permissions, for example `mdb credential create --actor agent-slack --permissions workspace:project:read,workspace:project:write`.
2. A grant inside the vault for the same actor: `mdb workspace grant project agent-slack --permissions read,write`.

The `collectionId` in the bridge configuration must name a collection that already exists in that workspace (`mdb collection create shared --workspace project`).

Behaviour worth knowing:

- Search and create are limited to the configured workspace and collection by the bridge itself; `get` and `patch` rely on the credential's access rules, so give each Agent Slack workspace its own scoped credential.
- The bridge reuses one MCP session per credential (one HTTP request per call). Engram's default limit is 120 requests per minute per credential, so keep the limit at that default or higher.
- Memory bodies larger than 200,000 bytes are refused before anything is sent, because Engram echoes the stored document in its response and the bridge accepts responses up to 1 MB.
- `deliver_engram_memory` needs the Agent Slack workspace key to be a registered Agent Slack project and the destination to be an existing direct chat that is listed with `enabled` and `userOptIn`.
- A Codex agent can only call these tools if Codex has the Agent Slack MCP server configured, and a non-interactive Codex run needs each tool approved (for example `approval_mode = "approve"` per tool in `~/.codex/config.toml`), otherwise it answers "MCP tool call requires approval, but approval policy is never".
