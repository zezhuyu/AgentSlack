# Build an Agent Slack-Compatible Agent System

Use this document as the implementation directive for an agent building or adapting a multi-agent project to run under Agent Slack.

## Objective

Build a host-owned agent system that Agent Slack can register, discover, and execute without adding project-specific conditions or scheduling policy to `subsystems/agent_slack`. All routing, delegation, dependencies, tools, and policies live in the host's native agent definitions.

## Non-Negotiable Boundary

Do not edit Slack runtime code to recognize a coordinator, domain, workflow, keyword, participant graph, or dependency. The manifest only connects a runner and lead ID; configure orchestration in the host lead prompt and native agent system.

## Required Build Sequence

### 1. Define Stable Agents

Create one Markdown file per agent in a supported discovery location:

```text
.claude/agents/<agent-id>.md
.claude/subagents/<agent-id>/<agent-id>.md
.codex/agents/<path>/<agent-id>.md
```

Use this template:

```md
---
name: evidence_researcher
summary: Collects evidence and returns source-backed findings.
tools:
  - Read
  - Bash
---
# Evidence Researcher

## Mission

Complete evidence requests using available project tools and files.

## Inputs

- objective from the current chat
- recent transcript
- project-local data and tools

## Required Output

- findings
- evidence paths or source references
- uncertainty and blockers

## Forbidden

- returning only a plan or promise
- inventing unavailable evidence
- performing another agent's responsibility
```

Every `name` must be unique and stable because chats, memory, meetings, and native Task events reference it.

### 2. Choose Orchestrators Deliberately

An orchestrator is an ordinary discovered agent whose ID is declared in the manifest. Its prompt must require it to:

- classify the objective;
- spawn appropriate native subagents;
- run independent work in parallel and dependent work after prerequisites;
- collect specialist Task results;
- reconcile contradictions;
- identify unresolved blockers;
- synthesize the final response;
- avoid duplicating specialist work unless verification is necessary.

Do not infer orchestrators from filenames or titles. Declaration in the manifest exposes a lead to the UI; the lead prompt owns the workflow.

### 3. Create the Architecture Manifest

Create `<project-root>/.agent-slack.json`:

```json
{
  "schema_version": 1,
  "runner": "auto",
  "orchestrators": [
    {
      "agent_id": "system_coordinator"
    }
  ]
}
```

Manifest rules:

- set `runner` to `auto`, `codex`, or `claude`; `AGENT_SLACK_CLI` remains an optional process-wide override;
- reference only stable agent IDs;
- prefer `claude` when the host lead uses Claude Code native Task delegation;
- keep routing, participant selection, dependency ordering, and concurrency out of this file;
- use multiple orchestrator entries only when the host genuinely exposes separate lead agents.

### 4. Make Agents Executable

Ensure either `codex` or `claude` is installed and authenticated in the daemon environment. Agent prompts must identify project-relative commands and artifact locations. Commands should be non-interactive and bounded.

If the host needs a custom runtime rather than Codex/Claude, implement a generic runtime adapter interface inside Agent Slack; do not place host-domain logic in that adapter. The adapter accepts an agent profile, objective, transcript, and memory, and returns text or stream events.

### 5. Preserve Ownership

Host project owns:

- definitions and system prompts;
- native orchestration prompts and workflows;
- domain tools and data;
- policy and approval rules;
- generated domain artifacts.

Agent Slack owns:

- coworker directory UI;
- DMs, groups, and meetings;
- streamed display;
- chat persistence;
- overlay memory.

### 6. Validate Compatibility

Start locally:

```bash
python subsystems/agent_slack/run.py --project-root "$PWD" --host 127.0.0.1 --port 8899
```

Verify discovery and architecture:

```bash
curl -s http://127.0.0.1:8899/api/agents
curl -s http://127.0.0.1:8899/api/health
```

Acceptance checklist:

- every intended agent appears once;
- agent titles and summaries are meaningful;
- health reports the configured orchestrator IDs;
- regular DMs run only the selected agent;
- an orchestrator DM starts one CLI process using `--agent <lead-id>`;
- native Task results appear as separate messages before the lead's final answer;
- group members remain context and do not cause frontend-managed execution;
- replies stream and persist after reload;
- no host-specific ID appears in Slack runtime source.

Audit the final boundary:

```bash
rg -n "<host-name>|<orchestrator-id>|<domain-keyword>" \
  subsystems/agent_slack/agent_slack \
  subsystems/agent_slack/static
```

The command must return no project-specific runtime branches. Host-specific values are allowed only in agent definitions, host data, tests/fixtures, and `.agent-slack.json`.

## Completion Report

The builder agent must report:

- discovered agent count and IDs;
- manifest path and orchestrator IDs;
- route-to-participant mapping;
- CLI runtime selected;
- DM, group, automatic-meeting, streaming, and persistence test results;
- any missing agents, invalid IDs, or execution blockers.
