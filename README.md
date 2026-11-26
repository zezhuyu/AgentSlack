# Agent Slack

`Agent Slack` is a standalone local desktop/web app that connects to Codex/Claude-style multi-agent projects. Each registered project folder appears as a Slack-style server.

It does not modify the host project's codepath. It discovers agent definitions, builds profiles, stores per-agent memory, persists chat history, and relays one native CLI session. A host lead coordinates its own subagents; Agent Slack displays each native result and the final synthesis.

### Choosing the agent and model for a chat

New chats and chats with no agent reply yet can choose Codex or Claude Code and select a model from the available list. The choice is saved with the chat and applies to its runs. After the first agent reply, or while a run is active, the controls lock and the selected agent and model remain visible in the chat header.

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

Set `AGENT_SLACK_IP` and `AGENT_SLACK_PORT` to choose the listener address and
port. `AGENT_SLACK_HOST` is also accepted as an alias for `AGENT_SLACK_IP`.
For example, to listen on all network interfaces at port 9000:

```bash
AGENT_SLACK_IP=0.0.0.0 AGENT_SLACK_PORT=9000 python run.py
```

The web UI and API share this listener. From another device on the same
network, open `http://<this-computer-lan-ip>:9000` (find the computer's LAN IP
in its network settings). Command-line `--host` and `--port` options take
precedence over the environment variables.

The web UI and API require the password configured on first launch in the
macOS app. Browser sessions use an HTTP-only cookie; each iOS installation gets
its own revocable device token. macOS app traffic uses a private local desktop
credential and remains signed in. Keep LAN access on trusted networks and use
a VPN or authenticated tunnel for remote access.

## Push notifications

Push notifications require HTTPS through a reverse proxy. On iOS, install Agent
Slack from Safari's Share menu with **Add to Home Screen**, then open the Home
Screen app before enabling notifications (iOS 16.4+). Safari 18.4+ uses
Declarative Web Push; older supported browsers use the service worker fallback.
Install the optional crypto dependency with `pip install -r requirements-push.txt`.
Set `AGENT_SLACK_PUSH_SUBJECT` to a valid `mailto:` or HTTPS contact URI (default
`mailto:agent-slack@localhost`). VAPID keys and subscriptions are stored in
`<data-root>/push.json` with owner-only permissions; back up that file to retain
subscriptions and the VAPID identity. Enable notifications in Server Settings,
then use **Send test** to confirm delivery. Agent completion and failure
notifications open the related chat when available.

Omit `--project-root` to start with an empty server registry, then use the `+` button in the workspace rail and enter an agent-system folder. Use `--data-root <folder>` to move the server registry, chats, and memories out of the source tree.

Agent replies use `AGENT_SLACK_CLI=auto` by default, preferring Codex and then Claude Code. Set `AGENT_SLACK_CLI=codex` or `AGENT_SLACK_CLI=claude` to force a runner, and optionally set `AGENT_SLACK_CLI_TIMEOUT` to change the 180-second reply timeout. Both runners execute with the same unrestricted filesystem, command, network, MCP, plugin, and user-configuration access as their trusted terminal CLIs. Agent Slack also directs either runner to honor matching skills from project and user `.agents/skills`, `.claude/skills`, and `.codex/skills` roots.

## macOS app

### Install a release build

1. Download the latest `Agent Slack` macOS release from
   [GitHub Releases](https://github.com/zezhuyu/AgentSlack/releases).
2. Open the downloaded DMG and drag `Agent Slack.app` into `/Applications`.
3. In Terminal, remove the quarantine attribute so macOS can open the app:

   ```bash
   sudo xattr -rd com.apple.quarantine "/Applications/Agent Slack.app"
   ```

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

MCP task calls continue in the same Agent Slack chat for a conversation:
ChatGPT supplies its conversation identity automatically through
`openai/session`; Claude connectors do not send a conversation id, so pass the
returned `session_id` from an earlier call to continue that chat. Calls without
either identity start a new chat each time.

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
- Attach files with the paperclip button, paste screenshots, or drag files into the composer. On iPhone and iPad, the picker offers the system photo and file sources; attachments can be sent without accompanying text.
- Images up to 5 MB each (five per turn) are passed directly to Claude Code and Codex; other file types remain available by validated path. The agent is instructed to open and read each attachment before answering.
- Files and images upload to the selected server before the message is posted; messages retain attachment metadata and render previews or download cards after reload
- On mobile, Send stays above the device safe area and People opens the same dynamically discovered agent list in a drawer

## Outside access

- local API: `http://127.0.0.1:8899/api/v1`
- LAN access: enabled by default; use **Copy API URL** from the desktop menu-bar icon
- internet access: put the daemon behind an authenticated reverse proxy or private tunnel; Agent Slack itself does not authenticate requests
- same-origin browser app: the UI and JSON API are served from the same daemon, so no separate frontend build or CORS setup is required

### Run behind a reverse proxy / Tailscale

The browser app supports a URL sub-path. `AGENT_SLACK_BASE_PATH` defaults to
`/agent_slack`; the server accepts requests with or without that prefix, so it
works with proxies that strip the location prefix and proxies that preserve it.
Set it to another leading-slash path to use a different mount, or to an empty
string to disable prefix stripping. Browser API and artifact URLs remain
root-relative in the app and `base-path.js` derives the public mount from its
own script URL. This leaves the un-prefixed API URLs used by the native iOS and
macOS clients unchanged.

The installed Tailscale CLI accepts this form (`tailscale serve --help`):

```sh
tailscale serve --bg --https=443 --set-path /agent_slack http://127.0.0.1:8899
```

For nginx, define the WebSocket mapping once in the `http` block:

```nginx
map $http_upgrade $connection_upgrade {
    default upgrade;
    ''      close;
}
```

Add this exact location once to redirect the no-trailing-slash URL while
retaining its query string:

```nginx
location = /agent_slack { return 308 /agent_slack/$is_args$args; }
```

For strip mode, use a trailing slash on `proxy_pass` to remove the location
prefix:

```nginx
location /agent_slack/ {
    proxy_pass http://127.0.0.1:8899/;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-Prefix /agent_slack;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection $connection_upgrade;
    proxy_buffering off;
    proxy_read_timeout 3600s;
}
```

For keep mode, omit the URI suffix on `proxy_pass` so the daemon receives
`/agent_slack/` and strips it:

```nginx
location /agent_slack/ {
    proxy_pass http://127.0.0.1:8899;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection $connection_upgrade;
    proxy_buffering off;
    proxy_read_timeout 3600s;
}
```

`proxy_buffering off` preserves NDJSON streaming, and the Upgrade headers carry
the voice WebSocket handshake. `X-Forwarded-Proto` lets login cookies include
`Secure` when TLS terminates at the proxy. For strip mode, `X-Forwarded-Prefix`
sets the cookie path to `/agent_slack`; in keep mode the server detects that
path from the request itself.

The stable mobile-client contract, endpoint list, server-selection header, and
NDJSON streaming examples are documented in [MOBILE-API.md](./MOBILE-API.md).

## Permission requests & session notifications

Agent Slack can receive native Claude Code and Codex `PermissionRequest` hooks.
Install the scripts and preview the config snippets with:

```sh
python3 scripts/hooks/install-hooks.py
```

The installer prints mergeable entries for `~/.claude/settings.json` and
`~/.codex/hooks.json`; it does not edit them unless you explicitly pass
`--apply`. Applying first creates timestamped `.bak-*` copies and merges the
entries without removing existing hooks. The scripts use Node's built-in
`fetch` and have no package dependencies. They read the hook token from
`AGENT_SLACK_HOOK_TOKEN` or `<AGENT_SLACK_DATA_ROOT>/hook-token` (the desktop
default is `~/Library/Application Support/agent-slack-desktop/data/hook-token`)
and send requests to `AGENT_SLACK_URL` (default `http://127.0.0.1:8899`).

Permission cards show the source, tool, project, a short summary and expiry.
Approve or deny in the authenticated Agent Slack UI. If the bridge is
unavailable, the request expires, or the user takes no action, the hook prints
nothing and Claude/Codex continue with their own permission prompt. Agent Slack
never auto-approves on a failure path. Push notifications are alerts only; the
hook's synchronous decision is the permission gate. Hook payloads can contain
commands and file paths, so keep the token private and protect access to the
Agent Slack account.

Imported project sidebars list host sessions under their project root; the
global Host Sessions view lists sessions outside imported roots. Session
notifications link to the corresponding host conversation. Managed Agent Slack
runs keep streamed text while work is active and show the final response when
the run completes.

Provider-native Claude and Codex sub-agents are grouped under their launching
host session instead of appearing as separate sessions. Their status and
progress are visible in the Agents panel; opening a card shows a read-only
transcript and progress history.

If your Codex config already routes `PermissionRequest` to the VibeBridge hook
at `127.0.0.1:8875`, either replace that entry with the Agent Slack script or
keep both hook handlers in the event list. Existing handlers remain in place
when using the installer; inspect the merged config to avoid duplicate
permission bridges.

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
