# Build an Agent Slack-Compatible Agent System

Use this document as the implementation directive for an agent building or adapting a multi-agent project to run under Agent Slack.

## Objective

Build a host-owned agent system that Agent Slack can register as a server, discover, and execute without adding project-specific conditions to `subsystems/agent_slack`. All domain names, routing decisions, tools, policies, and participant graphs must live in the host project.

## Non-Negotiable Boundary

Do not edit Slack runtime code to recognize a particular coordinator, domain, workflow, or keyword. Configure orchestration in `<project-root>/.agent-slack.json`. A reusable Slack subsystem must produce the same behavior for any host that satisfies this contract.

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

Every `name` must be unique and stable because chats, memory, meetings, and manifest routes reference it.

### 2. Choose Orchestrators Deliberately

An orchestrator is an ordinary discovered agent whose ID is declared in the manifest. Its prompt must require it to:

- classify the objective;
- use specialist findings already present in the meeting transcript;
- reconcile contradictions;
- identify unresolved blockers;
- synthesize the final response;
- avoid duplicating specialist work unless verification is necessary.

Do not infer orchestrators from filenames or titles. Declaration in the manifest is the only automatic-meeting authority.

### 3. Create the Architecture Manifest

Create `<project-root>/.agent-slack.json`:

```json
{
  "schema_version": 1,
  "runner": "auto",
  "orchestrators": [
    {
      "agent_id": "system_coordinator",
      "default_participants": ["intake_agent"],
      "routes": [
        {
          "keywords": ["quality", "regression", "test"],
          "participants": ["test_engineer", "critic"]
        },
        {
          "keywords": ["architecture", "migration"],
          "participants": ["architect", "dependency_reviewer"]
        }
      ]
    }
  ]
}
```

Manifest rules:

- set `runner` to `auto`, `codex`, or `claude`; `AGENT_SLACK_CLI` remains an optional process-wide override;
- reference only stable agent IDs;
- keep keyword lists domain-specific but concise;
- put always-required reviewers in `default_participants`;
- put conditional specialists in routes;
- do not include the orchestrator in participant lists; it is inserted automatically;
- allow multiple routes to match when work spans specialties;
- use multiple orchestrator entries only when the host genuinely has separate coordination authorities.

### 4. Make Agents Executable

Ensure either `codex` or `claude` is installed and authenticated in the daemon environment. Agent prompts must identify project-relative commands and artifact locations. Commands should be non-interactive and bounded.

If the host needs a custom runtime rather than Codex/Claude, implement a generic runtime adapter interface inside Agent Slack; do not place host-domain logic in that adapter. The adapter accepts an agent profile, objective, transcript, and memory, and returns text or stream events.

### 5. Preserve Ownership

Host project owns:

- definitions and system prompts;
- orchestration manifest;
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
- orchestrator DMs select the expected route participants;
- specialists appear before the orchestrator in meeting output;
- groups without orchestrators still work;
- unknown participant IDs are ignored safely;
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
