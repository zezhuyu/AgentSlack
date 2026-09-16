# Design

## Source of truth
- Status: Active
- Last refreshed: 2026-07-27
- Primary product surfaces: desktop/web Agent Slack, macOS shell, native iOS client
- Evidence reviewed: `README.md`, `MOBILE-API.md`, `static/index.html`, `static/styles.css`, `static/app.js`, `static/markdown.js`, `agent_slack/server.py`, `ios/AgentSlack/Views/RootView.swift`, `ios/AgentSlack/Views/ChatView.swift`, `ios/AgentSlack/State/AppModel.swift`, `ios/AgentSlack/Networking/AgentSlackAPI.swift`

## Brand
- Personality: capable, direct, collaborative, and familiar to Slack users without copying proprietary artwork.
- Trust signals: clear server identity, visible agent identity, explicit connection errors, persistent chat history, and honest running state.
- Avoid: finance-specific assumptions, hidden background actions, decorative gradients that reduce readability, and UI that implies public-internet safety.

## Product goals
- Goals: make locally hosted agent systems feel like coworkers in a familiar chat workspace; switch servers quickly; open a person directly; preserve readable structured output.
- Non-goals: reproduce Slack branding/assets exactly, host agents on iOS, expose Agent Slack directly to the public internet, or replace the desktop server.
- Success signals: a user can add a daemon URL, select one of its workspaces, open a person, send a message, watch the streamed reply, and understand Markdown or JSON without leaving the app.

## Personas and jobs
- Primary personas: developers and knowledge workers running Claude Code or Codex agent systems on a trusted Mac or LAN host.
- User jobs: connect to a host, switch agent systems, find one or more specialists with `@` mentions, continue chats, create groups, and monitor replies.
- Key contexts of use: phone on the same trusted network, iPad at a desk, intermittent foreground/background transitions.

## Information architecture
- Primary navigation: saved daemon connections -> server/workspace -> chats or people -> conversation.
- Core routes/screens: startup connection gate, workspace sidebar, chat/people list, new group sheet, conversation detail.
- Content hierarchy: current workspace first; chats and people second; messages and active reply state third.

## Design principles
- Familiar workspace model: preserve the dark aubergine server/sidebar identity while the conversation surface follows the device appearance.
- Native where it matters: use SwiftUI navigation, sheets, accessibility, Dynamic Type, safe areas, and platform controls instead of embedding the web UI.
- Structured output stays legible: render Markdown as semantic blocks and valid JSON—including JSON embedded after an agent’s prose preamble—as nested cards.
- Tradeoffs: compact iPhone layouts collapse the three desktop columns into navigation; iPad retains a multi-column workspace.

## Visual language
- Color: aubergine `#3F0F40`, deep rail `#221527`, brand accent `#611F69`, Slack-like blue/green status accents, and an adaptive conversation canvas with system label colors. Purple remains a brand/background color; interactive text and structured labels switch to high-contrast system cyan in Dark Mode and use purple only in Light Mode.
- Typography: system font with semantic text styles and monospaced code/JSON values.
- Spacing/layout rhythm: 4/8/12/16/24 point rhythm.
- Shape/radius/elevation: 8–14 point radii, restrained separators, light card shadows only for structured content.
- Motion: native navigation and progress transitions; respect Reduce Motion.
- Imagery/iconography: SF Symbols and server-provided logos; do not use Slack-owned artwork.

## Components
- Existing components to reuse conceptually: workspace rail, backend-provided server logo, chat row, people row, message row, composer, structured JSON card.
- New/changed components: saved host connection picker, compact workspace switcher, full-screen chat focus toggle, multi-select `@` mention picker and selection chips, file/photo pickers with removable attachment chips, sent attachment cards, native Markdown block renderer, recursive JSON renderer, destructive chat actions, active-run stop controls, and agent-authored choice cards.
- Variants and states: selected/unselected server, direct/group chat, green-tinted user bubble, neutral agent bubble, streaming/error/offline, and pending/submitting/answered interaction.
- Structured JSON cards size to their rendered content and trim boundary-only whitespace from string values; they must not introduce empty vertical regions around a message.
- Token/component ownership: iOS tokens live in `ios/AgentSlack/Design/SlackTheme.swift`; web tokens remain in `static/styles.css`.

## Accessibility
- Target standard: WCAG 2.2 AA intent plus native iOS accessibility behavior.
- Keyboard/focus behavior: hardware-keyboard submission and native navigation focus where SwiftUI provides it.
- Contrast/readability: adaptive system text and surfaces provide dark text on light bubbles in Light Mode and light text on dark bubbles in Dark Mode; no information is conveyed by color alone.
- Screen-reader semantics: descriptive labels for servers, people, send state, and message authors.
- Reduced motion and sensory considerations: no required animation; progress is described with text and indicators.

## Responsive behavior
- Supported breakpoints/devices: iOS 17+ on iPhone and iPad.
- Layout adaptations: `NavigationSplitView` provides three columns on regular width and allows the user to collapse them into a full-width chat. On compact width, selecting a workspace advances from the server screen to the chat directory, selecting a chat advances to the conversation, and native Back navigation returns through those levels. A deliberate left swipe dismisses the full-screen server selector when a workspace is available.
- Touch/hover differences: minimum 44-point touch targets; optional pointer hover comes from native controls on iPad.

## Interaction states
- Loading: an aubergine native launch background transitions immediately into a branded connection-check screen with a concise task label; never show an unexplained black frame.
- Empty: actionable prompts to add a server, choose a workspace, open a person, or create a group.
- Error: an unreachable saved backend returns to the startup connection gate with the saved URL prefilled and editable; in-workspace errors use non-destructive alert text.
- Success: the requested content replaces its loading state; no noisy success toast for routine chat operations.
- Mention targeting: typing `@` opens installed-agent suggestions in a horizontally centered, bounded panel on wide layouts while retaining the available composer width on compact iPhone layouts. Each selection inserts the stable agent ID and remains visible as a chip. One mention targets one agent; two or more mentions start a meeting with all selected agents and preserve mention order for lead selection.
- Conversation entry: opening or switching chats starts at the newest message. New messages and streamed replies continue to follow the bottom while the active conversation is changing.
- Attachments: desktop/web and iOS expose file and image pickers plus removable pending chips. Uploading occurs before message submission; sent messages show filename, type, and size while the backend privately resolves opaque attachment IDs to absolute paths for the agent.
- Chat deletion: swipe or context actions expose Delete, require destructive confirmation, and never delete a chat with an active run.
- Run control: each visible running-agent row exposes Stop, with a separate stop-all control for the active run; stopping is acknowledged immediately and remains visible until the backend confirms completion.
- Head-agent meetings: a message sent in a configured head-agent direct chat starts an automatic meeting. The backend-created meeting is a distinct group conversation, and the client follows that new chat as soon as `meeting_created` is streamed.
- Agent questions: when an agent cannot continue without a decision, the backend persists its prompt, single- or multi-select mode, and task-specific options as message metadata. Desktop and iOS render those supplied labels and descriptions as native controls; clients never substitute hard-coded approval choices. On iOS, the choice card is horizontally centered with a bounded readable width on wide layouts. Submitting a valid selection records one user message, marks the card answered, and starts the next agent turn.
- Disabled: unavailable servers and send controls visibly dim and explain why.
- Offline/slow network: verify the selected saved backend on every cold launch; retain and prefill its URL when unreachable so the user can retry or modify it without losing configuration. Reachability uses a short timeout, while active agent streams tolerate multi-hour tasks without presenting a false timeout. When iOS backgrounds, it detaches its foreground stream without cancelling the Mac-side run; on activation it reloads the selected chat and `/runs/active` state before permitting another run.

## Content voice
- Tone: concise, factual, and operational.
- Terminology: “server” means a saved Agent Slack daemon; “workspace” means a registered agent system returned by that daemon; “people” means discovered agents.
- Microcopy rules: name the failing action and recovery; never call a disconnected stream an agent failure.

## Implementation constraints
- Framework/styling system: native SwiftUI, Foundation networking, no third-party dependencies.
- Design-token constraints: extend the desktop palette; do not introduce a separate brand system.
- Performance constraints: first-frame UI must not wait for reachability; backend verification waits up to 20 seconds for LAN connectivity, agent output uses incremental NDJSON parsing, message lists are lazy, and polling is bounded to visible chats.
- Compatibility constraints: versioned `/api/v1` endpoints, `X-Agent-Slack-Server`, and user-configured trusted LAN HTTP through the app transport exception.
- Test/screenshot expectations: model/parser unit tests and `xcodebuild` compilation; simulator visual review when CoreSimulator is available.

## Open questions
- [ ] Public distribution authentication / product owner / required before internet-facing or App Store release.
- [ ] Background notifications for completed runs / product owner / requires an authenticated push or local relay design.
