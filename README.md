# Agent Slack

`Agent Slack` is a standalone local desktop/web app that connects to Codex/Claude-style multi-agent projects. Each registered project folder appears as a Slack-style server.

It does not modify the host project's codepath. It discovers agent definitions, builds profiles, stores per-agent memory, persists chat history, and relays one native CLI session. A host lead coordinates its own subagents; Agent Slack displays each native result and the final synthesis.

## What it does

- discovers agent definitions from:
  - `.claude/agents/*.md`
  - `.claude/subagents/*/<subagent-name>.md`
  - `.codex/agents/**/*.md`
- creates a coworker directory with title, summary, prompt source, and profile metadata
- creates persistent direct messages and group chats
- stores each chat's full history on disk
- stores per-agent memory files with:
  - recent channels
  - recent responsibilities
  - last response summary
  - message ledger
- supports manual and automatic meetings
- registers multiple agent-system folders as isolated servers and switches between them from the workspace rail
- supports any host-defined orchestrator and participant-routing rules through `.agent-slack.json`
- runs replies through the locally authenticated Codex or Claude Code CLI
- falls back to an actionable local-runner error when neither CLI is available

## Run

From the repo root:

```bash
source .venv/bin/activate
python subsystems/agent_slack/run.py \
  --project-root "$PWD" \
  --port 8899
```

Then open:

```text
http://127.0.0.1:8899
```

The browser UI and API have no built-in token authentication. The daemon binds
to `0.0.0.0` by default so trusted LAN clients can connect. Because agent
runners have unrestricted terminal-equivalent access, any client that can
reach this API can cause commands to run with the desktop user's permissions.
Expose it only to clients and networks you fully trust.

Omit `--project-root` to start with an empty server registry, then use the `+` button in the workspace rail and enter an agent-system folder. Use `--data-root <folder>` to move the server registry, chats, and memories out of the source tree.

Agent replies use `AGENT_SLACK_CLI=auto` by default, preferring Codex and then Claude Code. Set `AGENT_SLACK_CLI=codex` or `AGENT_SLACK_CLI=claude` to force a runner, and optionally set `AGENT_SLACK_CLI_TIMEOUT` to change the 180-second reply timeout. Both runners execute with the same unrestricted filesystem, command, network, MCP, plugin, and user-configuration access as their trusted terminal CLIs. Agent Slack also directs either runner to honor matching skills from project and user `.agents/skills`, `.claude/skills`, and `.codex/skills` roots.

## macOS app

### ChatGPT MCP and Slack connectors

The optional connectors resolve every request through `workspace_key`, then
through the existing server registry. They cannot read another registered
project, arbitrary absolute paths, secrets, or excluded build/dependency
directories.

Install the connector dependencies with `python3 -m pip install -r
requirements-connectors.txt`. The macOS app starts the MCP HTTP endpoint
automatically alongside the normal AgentSlack backend:

```bash
http://127.0.0.1:8899/mcp
```

Use the OpenAI Secure MCP Tunnel to forward that private endpoint to ChatGPT.
The macOS app automatically downloads the matching official `tunnel-client`
release for the Mac from OpenAI's public release channel, verifies the release
digest when provided, stores it under app data, and starts it as needed.
The standalone stdio adapter remains available for tunnel profiles that use a
command instead of a local HTTP URL. Use the project instruction:
`workspace_key: seens; use AgentSlack MCP for local project context; use
create_task for implementation work.`

From the macOS app's Server Settings, choose **Install** under **Claude local
MCP**. This registers the already-bundled AgentSlack backend in Claude
Desktop's `claude_desktop_config.json`, preserving a backup of any existing
file. No separate AgentSlack MCP package is needed. Restart Claude Desktop
after installation. Claude Desktop and Claude Code launch the packaged backend
with `--mcp-stdio`; every tool call still requires
`workspace_key` and remains restricted to the configured project roots.

For Claude Platform MCP tunnels, use the **Claude MCP Tunnel** Configure button
in Server Settings. Create the tunnel in Claude Console, paste its domain and
token, and Agent Slack prepares the pinned `mcp-proxy`/`cloudflared` Docker
Compose stack, generates the local certificates, and starts it. Upload the
generated `data/ca.crt` in the tunnel's Certificates section in Claude Console,
then attach the route URL shown by Agent Slack (for example
`https://agent-slack.<domain>/mcp`) to the Claude agent. Claude's tunnel service
is separate from OpenAI's tunnel client; a local stdio server cannot be used by
Claude's remote API directly.

For Slack Socket Mode, create a Slack app with Socket Mode enabled, subscribe
to `app_mention`, grant `app_mentions:read`, `chat:write`, `files:write`, and
the channel history/read scopes required by the workspace. Set
`SLACK_BOT_TOKEN=xoxb-...` and `SLACK_APP_TOKEN=xapp-...`, then run:

```bash
python3 -m agent_slack.slack_connector --data-root "$HOME/Library/Application Support/Agent Slack/data"
```

Set the target channel ID in that project’s `.agent-slack.json` under
`connectors.slack.channel_id`. Tokens are environment variables only.

The Electron shell packages a universal2 frozen backend and static UI into an installable `Agent Slack.app`. Installed apps do not require system Python. They use the locally authenticated Codex or Claude Code CLI; credentials are never bundled and no model API is called.

Local builds are ad-hoc signed so the complete bundle can be verified reproducibly on both architectures. The generated DMGs are not Apple-notarized; public distribution requires a Developer ID certificate and notarization credentials.

```bash
cd subsystems/agent_slack/macos
npm install
python3 -m pip install -r requirements-build.txt
npm run backend
npm run build  # unpacked .app for local verification
npm run dist   # arm64 + x64 DMGs
npm run verify # frozen-backend smoke test, signatures, and DMG integrity
```

`npm run verify` boots the packaged backend against a temporary generic Codex
agent system and verifies both top-level and nested agent discovery. Run the
Python and Markdown regression suites from the repository root:

```bash
.venv/bin/python -m pytest -q subsystems/agent_slack/tests
node --test subsystems/agent_slack/tests/test_markdown_renderer.js
```

The desktop app uses a native folder chooser when adding a server. Runtime state is stored under `~/Library/Application Support/Agent Slack/data`, outside the read-only app bundle and outside connected agent-system folders.

Closing the desktop window destroys that window while the Agent Slack backend
continues to run from the menu bar, keeps the HTTP API available, and removes
the app from the Dock. Closing the last window never quits the background
service. Login-item
launches also start directly in this background state. Use the transparent-logo
menu-bar icon to create a new window, copy the API URL, allow trusted LAN access,
enable **Launch at Login**, or choose **Quit Agent Slack Entirely**. While the
window is open, `Cmd+Q`, the application menu, and the Dock menu all expose the
same full quit action. The
desktop API uses `0.0.0.0:8899` by default; `AGENT_SLACK_IP` and
`AGENT_SLACK_PORT` override those values globally. `AGENT_SLACK_HOST` remains a
backward-compatible alias for `AGENT_SLACK_IP`.

## iOS app

The native SwiftUI client lives in [`ios/`](./ios). It connects to one or more
Agent Slack daemons, exposes every registered agent-system workspace, and
supports people, direct messages, group chats, streamed replies, Markdown, and
structured JSON cards on iPhone and iPad. The web, macOS, and iOS composers can
attach files or photos; the Mac daemon stores each upload and gives the agent
its validated absolute path with the user's prompt.

Open `ios/AgentSlack.xcodeproj`, run the shared `AgentSlack` scheme, then use
**Add Server** with the API URL copied from the macOS menu-bar app. The iOS
client requires iOS 17 or later and has no third-party dependencies. See
[`ios/README.md`](./ios/README.md) for setup and security notes.

## Server behavior

- each server has a stable generated ID and points to one local agent-system root folder
- each server can use a custom PNG, JPEG, WebP, or GIF logo selected when it is created or from Server Settings
- the server rail creates and switches servers without restarting the app
- agent discovery, `.agent-slack.json`, CLI working directory, chats, and memories are scoped to that server
- duplicate folder registration reuses the existing server instead of creating conflicting state
- browser requests carry the selected server ID so another window cannot redirect an in-flight run
- missing folders remain visible but disabled until the folder becomes available again

## Chat behavior

- Select any agent in the **People** list to open a direct message; Agent Slack reopens the existing DM or creates one when needed
- Create a group chat by choosing multiple agents in **New Chat**; the group is persisted in the sidebar and remains available for follow-up messages
- Messaging a configured system orchestrator can create a separate automatic meeting group with architecture-routed participants; the new group appears in the sidebar and opens before replies stream
- Automatic meetings preserve the source direct message, while subsequent messages inside the meeting continue with the same group instead of creating another channel
- In a **direct message** chat, pressing `Send` behaves like Slack:
  - your message is posted immediately
  - the direct-message agent shows a working indicator and streams its reply automatically
- In a direct message to an orchestrator declared in `.agent-slack.json`, `Send` automatically selects matching subagents and runs the orchestrator last for synthesis
- In a **group chat**:
  - if a configured orchestrator is a member, `Send` triggers an orchestrator-led meeting across the current chat members
  - otherwise, `Send` starts one selected host-agent session; that host agent owns any native subagent delegation
- `Run Members`, `Auto Meeting`, and `Manual Meeting` remain available when you want to force or shape orchestration explicitly
- Agent CLIs run with project tools enabled so substantive requests must produce findings, not a promise to investigate
- Agent replies are requested as Slack-ready Markdown rather than JSON envelopes; the shared, server-agnostic renderer displays structured JSON from any connected agent system as readable cards, including nested objects and arrays
- Native agent sessions continue in the backend if the initiating browser disconnects, and emitted subagent/final results persist in the meeting thread
- Messages render safe ChatGPT-style Markdown, including headings, lists, links, code blocks, blockquotes, and tables
- Open chats and the sidebar synchronize every two seconds across desktop, web, and the native iOS client; focus changes trigger an immediate refresh
- In the message composer, `Return` sends and `Shift+Return` inserts a newline; IME composition is never submitted prematurely
- Files and images upload to the selected server before the message is posted; messages retain attachment metadata and agents receive only backend-validated absolute paths
- On mobile, Send stays above the device safe area and People opens the same dynamically discovered agent list in a drawer

## Outside access

- local API: `http://127.0.0.1:8899/api/v1`
- LAN access: enabled by default; use **Copy API URL** from the desktop menu-bar icon
- internet access: put the daemon behind an authenticated reverse proxy or private tunnel; Agent Slack itself does not authenticate requests
- same-origin browser app: the UI and JSON API are served from the same daemon, so no separate frontend build or CORS setup is required

The stable mobile-client contract, endpoint list, server-selection header, and
NDJSON streaming examples are documented in [MOBILE-API.md](./MOBILE-API.md).

## Data layout

Daemon mode stores state under `subsystems/agent_slack/data/` by default. Desktop mode uses its Application Support directory. Both use this layout:

```text
<data-root>/
  servers.json
  servers/<server-id>/
    agents.json
    chats/<chat-id>.json
    memories/<agent-id>.json
    memories/<agent-id>.md
```

## Generic use on other projects

Copy the `subsystems/agent_slack/` folder into another project and run:

```bash
python subsystems/agent_slack/run.py --project-root "$PWD" --host 127.0.0.1 --port 8899
```

If the target project uses Claude/Codex-style markdown agent definitions, the app will discover them automatically.

For automatic subagent routing, add the versioned architecture manifest described in [AGENT-SYSTEM-COMPATIBILITY.md](./AGENT-SYSTEM-COMPATIBILITY.md). Without a manifest, DMs, groups, and manual meetings still work.

For the full integration contract, see [REQUIREMENTS.md](./REQUIREMENTS.md).

## Current limits

- group chat orchestration is sequential, not concurrent
- memory summarization remains heuristic
- agent discovery is markdown-definition based; if a project stores agents in a different format, add a discovery adapter in this subfolder
# Engram memory bridge (optional)

Agent Slack can connect to an Engram remote MCP endpoint for shared historical recall and versioned writes. See [`docs/engram-integration.md`](docs/engram-integration.md) for workspace-bound credential configuration and limits. This integration is opt-in and leaves Agent Slack's existing project memory, task store, and tunnel support intact.
