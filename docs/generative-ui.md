# Agent-generated interfaces

AgentSlack's Generative UI is a platform capability shared by discovered agents.
It extends the existing Agent mode message stream and saved message metadata.
Chat mode remains a separate read-only provider path. The UI source of truth is
versioned JSON; the browser creates fixed DOM elements and never executes agent
code, JSX, HTML, URLs, or tool names.

## Architecture and data flow

The frontend is the existing static HTML/CSS/JavaScript client served by Python;
there is no React build or frontend package manager in this repository. Agent
messages are stored in workspace chat storage as `{author_type, author_id,
body, metadata}`. Agent runs stream NDJSON correlated by `run_id` and `task_id`,
and `/api/runs/active` recovers in-flight task text after reconnect. The
implementation therefore uses the existing renderer, storage, run protocol,
provider abstraction, and workspace selection. `@json-render/*`, React,
shadcn/ui, Zod, and Recharts would add an unused second frontend runtime, so the
approved catalog is implemented with bounded Python validation and a small
vanilla DOM renderer.

On completion, an agent may include one `agent-ui` fenced JSON block. The
backend validates it against schema version `1`, size, depth, node count,
registered component props, and action types. It binds opaque action IDs and
persists the envelope in `message.metadata.agent_ui`. Text outside the block
stays Markdown. Existing messages without a block do not make an extra model
call. The agent's existing response model composes the optional UI in the same
response; there is no separate generation request.

The envelope contains `schemaVersion`, `messageId`, `workspaceId`, `agentId`,
`runId`, `taskId`, `content.text`, `content.ui`, and `status`. It reuses the
saved message and run identities and is compatible with old messages that have
no `agent_ui` metadata. Only JSON specifications are persisted, never DOM
nodes.

The stream emits `ui.started`, `ui.patch`, `ui.completed`, and `ui.error`
correlated with message, workspace, agent, run, and task IDs. The current agent
protocol produces a complete validated snapshot at completion (sequence 1),
not token-by-token tree diffs. The client ignores duplicate or stale sequences.
Ordinary text/tool events continue through their original paths. If the stream
ends early, the active-run endpoint restores running tasks; the saved message
restores a completed UI after refresh. Incomplete UI JSON is hidden from the
Markdown view and an invalid final snapshot falls back to ordinary text.

## Component catalog

The built-in catalog includes:

- Layout: `Stack`, `Row`, `Grid`, `Card`, `Divider`, `Tabs`
- Display: `Text`, `Heading`, `Badge`, `Progress`, `Statistic`, `Spinner`
- Data: `Table`, `LineChart`, `BarChart`, `PieChart`
- Inputs: `Button`, `TextInput`, `Select`, `Checkbox`, `Slider`, `Form`
- Agent surfaces: `AgentStatus`, `TaskProgress`, `ToolResult`, `ApprovalCard`,
  `ArtifactPreview`

Props are allowlisted in `agent_slack/generative_ui.py` and the matching browser
renderer registry in `static/generative-ui.js`. Unsupported component names
render a text fallback; invalid specs fail closed without affecting Markdown.
Charts use small local SVG/DOM output and accept bounded data. Controls have
labels, native keyboard behavior, and local form/slider state. Table and chart
collections are capped. No arbitrary external resource is loaded.
`Spinner` is the only animated catalog entry: it uses one built-in CSS rotation,
accepts only an accessible text label, and respects reduced-motion preferences.
Agents cannot provide animation code, styles, or timing values.

~~~json
{"schemaVersion":"1","root":{"type":"Stack","props":{"gap":2},"children":[{"type":"Spinner","props":{"label":"Refreshing mock data"}},{"type":"Text","props":{"text":"Last updated: just now"}}]}}
~~~

### Add a component

1. Add its prop allowlist in `PROP_KEYS` in `agent_slack/generative_ui.py`.
2. Add the same allowlist and a fixed DOM renderer in `static/generative-ui.js`.
3. Use `textContent`, native controls, and bounded arrays/numbers. Add
   accessible labels and keyboard behavior.
4. Add Python validation and Node renderer tests. Never interpret strings as
   markup, executable code, a URL, or a backend operation.

## Agent output example

The following can be pasted into an agent response and is parsed as-is:

~~~markdown
Illustrative portfolio snapshot (mock data).

```agent-ui
{"schemaVersion":"1","root":{"type":"Grid","props":{"columns":2},"children":[{"type":"Card","props":{"title":"Allocation — mock data"},"children":[{"type":"PieChart","props":{"title":"Portfolio allocation (mock)","data":[{"name":"Cash","value":20},{"name":"Equities","value":60},{"name":"Bonds","value":20}]}}]},{"type":"Card","props":{"title":"Risk (mock)"},"children":[{"type":"Statistic","props":{"label":"Volatility","value":"12%","change":"Illustrative only"}},{"type":"LineChart","props":{"title":"Performance (mock)","data":[{"name":"Jan","value":2},{"name":"Feb","value":3},{"name":"Mar","value":2.5}]}}]},{"type":"Form","props":{"submitLabel":"Refresh analysis","action":{"type":"submit_form","agentIds":["aegis-wealth"],"objective":"Refresh this illustrative analysis using the supplied period."}},"children":[{"type":"Select","props":{"name":"period","label":"Analysis period","options":[{"label":"1 year","value":"1y"},{"label":"5 years","value":"5y"}]}}]}]}}
```
~~~

The form performs no operation until submitted. Submitted fields must match
field names declared in the saved form and are sent only as bounded strings to
the selected chat agent. A slider and tabs update locally and do not call an
agent.

## Interactions and authorization

The server has a closed action registry: `run_analysis`, `submit_form`,
`answer_question`, `approve`, and `reject`. A model cannot name an MCP tool,
SQL operation, filesystem path, or arbitrary endpoint. The server reloads the
saved message, confirms its workspace and author, confirms that the action node
is present in that exact UI, checks agent membership in that chat, validates
form fields, and applies an idempotency key before starting a run. Approval
cards can approve only a bounded agent run stored on the server; the client
cannot replace its target. Approval/rejection status is saved before execution.
The renderer never writes directly to storage. Each action's latest status is
read from the saved chat: pending work stays visibly disabled through refresh,
completed buttons say “Run again,” and failed actions say “Retry.” Repeatable
actions remain available with that explicit label.

AgentSlack currently has no authenticated user/session or role model. The HTTP
service is designed as a local, single-user application; same-origin checks and
workspace/chat membership are not user authentication. Therefore these
controls must not be treated as authorization for a remotely exposed,
multiple-user service or as a privileged destructive approval system. Do not
expose the server to an untrusted network until the application has a real user
identity and permission layer and this action route is bound to it. Existing
server routes share this broader limitation.

## Agent status and artifacts

Every running agent task has its own message row correlated by task and agent
IDs; sibling updates alter only that row. Parent and child task context remains
in the existing run/task events. `AgentStatus` and `TaskProgress` can render
coordinator summaries, while `ArtifactPreview` references artifacts through
AgentSlack's existing attachment metadata. Refresh restores completed views
from chat history and active task text through the existing reconnect endpoint.
No new orchestrator is introduced.

A coordinator can summarize multiple specialists using this data-only tree:

~~~json
{"schemaVersion":"1","root":{"type":"Grid","props":{"columns":3},"children":[{"type":"TaskProgress","props":{"title":"Research","status":"completed","progress":100,"summary":"Results saved."}},{"type":"TaskProgress","props":{"title":"Analysis","status":"running","progress":55,"summary":"Calculating risk."}},{"type":"TaskProgress","props":{"title":"Review","status":"failed","progress":100,"summary":"Source unavailable."}}]}}
~~~

A human approval card uses `action.type: "run_analysis"`, with an explicit
impact summary. Its approve and reject controls send decisions to the server;
only the stored bounded run target can execute.

## Errors, observability, and performance

Validation failures are logged without agent content. Render errors are caught
inside the renderer and leave the Markdown message available. Action failures
are returned as UI action status events. Specs are limited to 64 KiB, 120 nodes,
and nesting depth 8; chart/table inputs and form values are bounded. The
renderer is a small static script with no chart bundle or lazy-loading cost.

The system currently records validation failures and existing run/action
failures through server logging, but it does not yet publish structured latency,
component-usage, render-failure, or token-usage metrics. No performance gain is
claimed; the Phase 0 baseline was 178 passing Python tests, with no latency
benchmark recorded.

## Disable / rollback

Set `AGENT_SLACK_GENERATIVE_UI=0` (also accepts `false` or `off`) before starting
the server. This disables UI extraction, rendering, and action requests; normal
Markdown/tool/run handling continues. Persisted UI JSON remains inert and can
be rendered again when enabled. Remove the flag to restore the feature.

## Verification snapshot

Phase 0 discovery found Python 3.11+, static JavaScript, and `uv` as the
project's Python package manager; no web package manager or frontend build is
present. Current tests cover validation, malicious text handling, unknown
components, persistence, stream/action correlation, form restrictions,
workspace/chat target checks, idempotency, and same-origin route requirements.
The remaining known limitation is user authentication for remote or shared
instances; see the security section above.
