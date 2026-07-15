# Agent Slack Host Requirements

This is the runtime contract for placing `subsystems/agent_slack` on top of another multi-agent project. The Slack subsystem contains no host-specific agent IDs, routing keywords, participant lists, or workspace behavior. Those decisions belong to the host project.

## 1. Required Architecture

A compatible host has four layers:

1. **Agent definitions**: stable Markdown profiles discovered from the project root.
2. **Architecture manifest**: optional `.agent-slack.json` declaring orchestrators and routing rules.
3. **Execution runtime**: a locally authenticated Codex or Claude Code CLI.
4. **Artifacts and state**: host-owned project files plus Agent Slack-owned chats and memory.

Agent Slack works without a manifest. In that mode, direct messages, group replies, and manually configured meetings remain available. Automatic subagent selection requires the manifest.

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

## 3. Architecture Manifest Contract

The host may provide `.agent-slack.json` or `agent-slack.json` at its project root. The hidden filename takes precedence.

```json
{
  "schema_version": 1,
  "orchestrators": [
    {
      "agent_id": "coordinator",
      "default_participants": ["intake"],
      "routes": [
        {
          "keywords": ["security", "threat"],
          "participants": ["security_reviewer", "critic"]
        },
        {
          "keywords": ["release", "deploy"],
          "participants": ["test_engineer", "release_manager"]
        }
      ]
    }
  ]
}
```

Semantics:

- `schema_version` must currently be `1`.
- `orchestrators[].agent_id` must match a discovered agent ID.
- `default_participants` join every automatic meeting led by that orchestrator.
- a route matches when any configured keyword appears case-insensitively in the objective.
- participants from all matching routes are merged and deduplicated.
- IDs not found in the discovered agent directory are ignored safely.
- specialists run first and the orchestrator runs last for synthesis.
- multiple orchestrators are supported; the first configured orchestrator present in a group leads that message.

The manifest is configuration, not executable code. Do not add host-specific branches to Agent Slack source files.

## 4. Chat Behavior Contract

- DM to a regular agent: that agent runs and replies.
- DM to a configured orchestrator: matching participants are added and a meeting runs automatically.
- Group with a configured orchestrator: current group members run, with the orchestrator last.
- Group without an orchestrator: every current member runs sequentially.
- Manual meeting: the user chooses any lead and participants regardless of manifest.

Replies stream as NDJSON events:

```json
{"type":"run_started","mode":"respond","agent_ids":["researcher"]}
{"type":"agent_started","agent_id":"researcher","agent_label":"Research Agent"}
{"type":"delta","agent_id":"researcher","text":"Finding chunk"}
{"type":"agent_completed","agent_id":"researcher"}
{"type":"run_completed","chat_id":"abc123"}
```

## 5. Execution Contract

Agent Slack invokes a local CLI selected by `AGENT_SLACK_CLI`:

- `auto`: Codex first, then Claude Code.
- `codex`: require the Codex CLI.
- `claude`: require the Claude Code CLI.
- `offline`: deterministic unavailable-runner response for tests.

`AGENT_SLACK_CLI_TIMEOUT` controls the per-agent timeout, defaulting to 180 seconds. The host runtime must already be authenticated. Agent Slack does not use direct model APIs.

The generated prompt includes the agent definition, memory summary, chat members, objective, and recent transcript. It requires agents to perform available work before replying and to disclose retrieval blockers.

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

The service has no built-in authentication and binds to `127.0.0.1` by default. Bind to `0.0.0.0` only for a trusted LAN. Internet exposure requires an authenticated reverse proxy or private tunnel.

## 8. Compatibility Acceptance Criteria

A host is compatible when all of these pass:

1. `GET /api/agents` returns every intended profile with stable IDs.
2. `GET /api/health` reports the expected `architecture.orchestrator_ids`.
3. A regular-agent DM produces one persisted agent reply.
4. An orchestrator DM selects only configured/default participants and runs the orchestrator last.
5. Unknown manifest IDs do not break a run.
6. Group and manual-meeting flows complete without domain-specific Slack code.
7. Stream events arrive before the final persisted message.
8. Restarting the daemon preserves chats and memory.
9. Registering two hosts with the same agent IDs keeps their chats and memories isolated.
10. Switching servers changes the agent directory and architecture without restarting the daemon.

For implementation instructions intended for a coding agent, use [AGENT-SYSTEM-COMPATIBILITY.md](./AGENT-SYSTEM-COMPATIBILITY.md).
