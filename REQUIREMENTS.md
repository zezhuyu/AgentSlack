# Agent Slack Host Requirements

This is the runtime contract for placing `subsystems/agent_slack` on top of another multi-agent project. Agent Slack is a frontend and transport: it discovers profiles, starts one host-selected CLI session, relays native subagent events, and persists chat. Agent identities, routing, dependencies, and orchestration policy belong to the connected agent system.

## 1. Required Architecture

A compatible host has four layers:

1. **Agent definitions**: stable Markdown profiles discovered from the project root.
2. **Connection manifest**: optional `.agent-slack.json` selecting the CLI and lead agents exposed by the host.
3. **Execution runtime**: a locally authenticated Claude Code CLI for native multi-agent streaming, or Codex for single-agent compatibility.
4. **Artifacts and state**: host-owned project files plus Agent Slack-owned chats and memory.

Agent Slack works without a manifest. The host lead—not Agent Slack—selects and schedules subagents through its native runtime.

One Agent Slack installation may register multiple compatible hosts. Each host is a separate server with isolated discovery, architecture, chats, memories, and CLI working directory.

## 2. Agent Discovery Contract

Definitions are discovered from:

```text
<project-root>/.claude/agents/*.md
<project-root>/.claude/subagents/*/<matching-folder-name>.md
<project-root>/.codex/agents/**/*.md
```

Preferred definition:

```md
---
name: coordinator
summary: Routes requests to specialists and synthesizes their findings.
tools:
  - Read
  - Bash
---
# System Coordinator

You classify the objective, delegate evidence work, and synthesize a final response.
```

Requirements:

- `name` is a stable, unique machine ID.
- `summary` is concise and distinguishes the agent from peers.
- the Markdown body is a complete standalone system prompt.
- an agent instructed to write artifacts must identify valid project-relative paths.
- specialist agents return findings, not promises to investigate.

Agent Slack extracts `agent_id`, `name`, `title`, `summary`, `system_prompt`, `source_path`, `group`, `kind`, and `tools`.

## 3. Connection Manifest Contract

The host may provide `.agent-slack.json` or `agent-slack.json` at its project root. The hidden filename takes precedence.

```json
{
  "schema_version": 1,
  "runner": "auto",
  "orchestrators": [
    {
      "agent_id": "coordinator"
    }
  ]
}
```

Semantics:

- `runner` selects this host's default local CLI: `auto`, `codex`, or `claude`; the `AGENT_SLACK_CLI` environment variable overrides it globally when set.
- `schema_version` must currently be `1`.
- `orchestrators[].agent_id` must match a discovered agent ID.
- routing tables, participant lists, dependency graphs, and concurrency limits are intentionally not part of this contract.
- legacy routing fields are ignored so an old manifest cannot move orchestration back into the frontend.
- multiple host lead agents are supported; the user or chat selects which native session to start.

The manifest is configuration, not executable code. Do not add host-specific branches to Agent Slack source files.

## 4. Chat Behavior Contract

- DM to an agent: Agent Slack starts that one agent's native CLI session.
- DM to a configured orchestrator: Agent Slack starts the orchestrator; the orchestrator selects and runs native subagents.
- Group or manual meeting: members are UI/context metadata; only the selected lead session executes.
- Native Claude `Task`/agent events create subagent rows as they finish, followed by the lead's final answer.

Replies stream as NDJSON events:

```json
{"type":"run_started","mode":"respond","agent_ids":["researcher"]}
{"type":"agent_started","agent_id":"researcher","agent_label":"Research Agent"}
{"type":"delta","agent_id":"researcher","text":"Finding chunk"}
{"type":"agent_completed","agent_id":"researcher"}
{"type":"run_completed","chat_id":"abc123"}
```

A native-agent failure emits `agent_failed` and persists a visible error message. Meeting state is persisted as `running`, `completed`, or `completed_with_errors`. The backend completes and persists an active run even if the requesting stream disconnects.

## 5. Execution Contract

Agent Slack invokes a local CLI selected by `AGENT_SLACK_CLI`:

- `auto`: Codex first, then Claude Code.
- `codex`: require the Codex CLI.
- `claude`: require the Claude Code CLI.
- `offline`: deterministic unavailable-runner response for tests.

The host runtime must already be authenticated. Agent Slack does not use direct model APIs.

For Claude, Agent Slack runs one `claude --agent <lead> --output-format stream-json` process. The CLI loads the host's agent definition and emits native Task/subagent results. Agent Slack adds only chat title, objective, recent transcript, and display-format context; it does not add delegation instructions.

## 6. Persistence Contract

Agent Slack owns one global registry and one state namespace per server:

```text
<data-root>/
  servers.json
  server-assets/<server-id>/logo.<ext>
  servers/<server-id>/
    agents.json
    chats/<chat-id>.json
    memories/<agent-id>.json
    memories/<agent-id>.md
```

The desktop app sets `<data-root>` to `~/Library/Application Support/Agent Slack/data`. A server ID, not a folder name or agent ID, is the storage namespace. Removing a registry entry must never delete or modify the connected host project.

The host owns agent definitions, the architecture manifest, domain data, tools, and generated artifacts. Agent Slack must not redefine domain policy.

## 7. Network Contract

The service has no built-in authentication and binds to `0.0.0.0:8899` by default. `AGENT_SLACK_IP` and `AGENT_SLACK_PORT` are the global bind overrides. The desktop menu can restrict the service to loopback and can register the app as a macOS login item. Internet exposure requires an authenticated reverse proxy or private tunnel.

Mobile and external clients must use the versioned `/api/v1` surface. Existing
`/api/*` paths remain compatibility aliases for the bundled web client. Clients
select an agent-system server with `X-Agent-Slack-Server`; omitting the header
uses the currently active server. Agent execution streams use NDJSON rather
than WebSockets so native clients can consume incremental replies over ordinary
HTTP.

## 8. Compatibility Acceptance Criteria

A host is compatible when all of these pass:

1. `GET /api/agents` returns every intended profile with stable IDs.
2. `GET /api/health` reports the expected `architecture.orchestrator_ids`.
3. A regular-agent DM produces one persisted agent reply.
4. An orchestrator DM starts exactly one host lead session.
5. Native subagent Task results stream as separate agent messages before the final answer.
6. Group and manual-meeting flows complete without frontend scheduling policy.
7. Legacy route and dependency fields do not affect execution.
8. Restarting the daemon preserves chats and memory.
9. Registering two hosts with the same agent IDs keeps their chats and memories isolated.
10. Switching servers changes the agent directory and architecture without restarting the daemon.

For implementation instructions intended for a coding agent, use [AGENT-SYSTEM-COMPATIBILITY.md](./AGENT-SYSTEM-COMPATIBILITY.md).
