const state = {
  servers: [],
  activeServerId: null,
  generativeUiEnabled: true,
  agents: [],
  chats: [],
  hostSessions: [],
  permissions: [],
  hostSessionId: null,
  hostSubagentId: null,
  hostSessionLastError: new Map(),
  hostSessionStatus: null,
  hostMessagesStatus: null,
  hostResumes: new Map(),
  hostResumeAliases: new Map(),
  hostPendingPrompts: new Map(),
  hostSessionMessages: new Map(),
  hostSessionDetails: new Map(),
  hostSessionOpenRequest: 0,
  hostSessionsView: false,
  currentChatId: null,
  currentChatUpdatedAt: null,
  currentChatSize: null,
  orchestratorIds: [],
  peopleExpanded: false,
  mobileSidebarOpen: false,
  openingDmAgentIds: new Set(),
  runInProgress: false,
  runnerOptions: null,
  runnerOptionsAt: 0,
  currentChatSettings: null,
  currentRunId: null,
  currentRunChatId: null,
  stoppingTaskIds: new Set(),
  syncInProgress: false,
  streamAttached: false,
  chatListSignature: "",
  streamingRows: new Map(),
  answeringQuestionIds: new Set(),
  pendingUIActionKeys: new Set(),
  pendingAttachments: [],
  serverDialogMode: "create",
  peoplePickers: {
    chat: new Set(),
    meetingLead: new Set(),
    meetingParticipants: new Set(),
  },
};

const HOST_PENDING_PROMPTS_KEY = "agentSlackHostPendingPrompts";
try {
  const saved = JSON.parse(localStorage.getItem(HOST_PENDING_PROMPTS_KEY) || "[]");
  if (Array.isArray(saved)) {
    for (const [sessionId, prompts] of saved) {
      if (typeof sessionId === "string" && Array.isArray(prompts)) {
        state.hostPendingPrompts.set(sessionId, prompts.filter((item) => item && typeof item.body === "string"));
      }
    }
  }
} catch {}

function saveHostPendingPrompts() {
  try { localStorage.setItem(HOST_PENDING_PROMPTS_KEY, JSON.stringify([...state.hostPendingPrompts])); }
  catch {}
}

let authSetupToken = null;
let desktopClient = Boolean(window.agentSlack);

const $ = (id) => document.getElementById(id);
const renderMarkdown = (value) => window.AgentSlackMarkdown.render(value);
const appendStreamMarkdown = (currentText, delta) => window.AgentSlackMarkdown.appendStream(currentText, delta);
const chatSync = window.AgentSlackSync;
const composerKeys = window.AgentSlackComposer;
const mentionHelpers = window.AgentSlackMentions;
const interactionHelpers = window.AgentSlackInteractions;
const generativeUI = window.AgentSlackGenerativeUI;
const voiceState = {
  recorder: null, stream: null, active: false, speaking: false, chunks: [],
  voiceChatId: null, sourceChatId: null, audio: null, audioContext: null,
  analyser: null, monitorFrame: null, silenceSince: 0, processing: false,
  queuedBlob: null, discardNextRecording: false,
};

async function api(path, options = {}) {
  const headers = {
    "Content-Type": "application/json",
    ...(state.activeServerId ? { "X-Agent-Slack-Server": state.activeServerId } : {}),
    ...(options.headers || {}),
  };
  const response = await fetch(path, {
    headers,
    ...options,
  });
  if (!response.ok) {
    if (response.status === 401) showAuthDialog(false, null);
    const text = await response.text();
    const error = new Error(text || `HTTP ${response.status}`);
    error.status = response.status;
    throw error;
  }
  return response.json();
}

function showAuthDialog(setup, setupToken) {
  authSetupToken = setupToken;
  $("authTitle").textContent = setup ? "Set your web password" : "Sign in to Agent Slack";
  $("authHelp").textContent = setup
    ? "Create a password for browser and iOS access. The macOS app remains signed in."
    : "Enter the password configured in the macOS app.";
  $("authConfirmLabel").hidden = !setup;
  $("authPasswordLabel").firstChild.textContent = setup ? "New password" : "Password";
  $("authPassword").autocomplete = setup ? "new-password" : "current-password";
  $("authSubmit").textContent = setup ? "Set password" : "Sign in";
  $("authError").hidden = true;
  if (!$("authDialog").open) $("authDialog").showModal();
}

async function initializeAuth() {
  $("signOutBtn").hidden = desktopClient;
  const status = await fetch("/api/v1/auth/status").then((response) => response.json());
  if (!status.password_set) {
    if (desktopClient && status.setup_token) {
      showAuthDialog(true, status.setup_token);
      return false;
    }
    $("authHelp").textContent = "Password setup is available in the Agent Slack macOS app. Open it on the host Mac, set a password, then sign in here.";
    showAuthDialog(false, null);
    $("authSubmit").disabled = true;
    return false;
  }
  if (!desktopClient) {
    const response = await fetch("/api/v1/servers");
    if (response.status === 401) {
      showAuthDialog(false, null);
      return false;
    }
  }
  return true;
}

$("authForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const password = $("authPassword").value;
  const setup = Boolean(authSetupToken);
  try {
    if (setup) {
      if (password !== $("authConfirm").value) throw new Error("Passwords do not match");
      const response = await fetch("/api/v1/auth/setup", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password, setup_token: authSetupToken }),
      });
      if (!response.ok) throw new Error((await response.json()).error || "Could not set password");
      authSetupToken = null;
      $("authPassword").value = "";
      if (desktopClient) {
        $("authDialog").close();
        await loadServers().then(loadActiveServer);
        return;
      }
      showAuthDialog(false, null);
      $("authSubmit").disabled = false;
      $("authError").textContent = "Password set. Sign in to continue.";
      $("authError").hidden = false;
      return;
    }
    const response = await fetch("/api/v1/auth/login", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ password }),
    });
    if (!response.ok) throw new Error((await response.json()).error || "Sign in failed");
    $("authDialog").close();
    $("authPassword").value = "";
    await loadServers().then(loadActiveServer);
  } catch (error) {
    $("authError").textContent = error.message;
    $("authError").hidden = false;
  }
});

$("signOutBtn").addEventListener("click", async () => {
  try {
    const response = await fetch("/api/v1/auth/logout", { method: "POST" });
    if (!response.ok) throw new Error(`Sign out failed (HTTP ${response.status})`);
    window.location.reload();
  } catch (error) {
    alert(error.message);
  }
});

async function streamApi(path, payload, onEvent, extraHeaders = {}) {
  const headers = {
    "Content-Type": "application/json",
    ...(state.activeServerId ? { "X-Agent-Slack-Server": state.activeServerId } : {}),
    ...extraHeaders,
  };
  const response = await fetch(path, {
    method: "POST",
    headers,
    body: JSON.stringify(payload),
  });
  if (!response.ok || !response.body) {
    throw new Error(await response.text() || `HTTP ${response.status}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";
    for (const line of lines) {
      if (line.trim()) await onEvent(JSON.parse(line));
    }
    if (done) break;
  }
  if (buffer.trim()) await onEvent(JSON.parse(buffer));
}

async function loadHealth() {
  const payload = await api("/api/health");
  $("workspaceLabel").textContent = payload.workspace || "Agent Slack";
  state.generativeUiEnabled = payload.generative_ui !== false;
  state.orchestratorIds = payload.architecture?.orchestrator_ids || [];
}

async function loadServers() {
  const payload = await api("/api/servers");
  state.servers = payload.servers || [];
  state.activeServerId = payload.active_server_id || null;
  renderServers();
  return state.activeServerId;
}

function renderServers() {
  $("hostSessionsServerBtn").classList.toggle("active", state.hostSessionsView);
  $("hostSessionsServerBtn").setAttribute("aria-current", state.hostSessionsView ? "true" : "false");
  $("serverList").innerHTML = "";
  state.servers.forEach((server) => {
    const button = document.createElement("button");
    button.type = "button";
    const isActive = !state.hostSessionsView && server.server_id === state.activeServerId;
    button.className = `server-btn ${isActive ? "active" : ""}`;
    if (server.logo_url) {
      const image = document.createElement("img");
      image.src = `${server.logo_url}?v=${encodeURIComponent(server.logo_revision || "1")}`;
      image.alt = "";
      button.appendChild(image);
    } else {
      button.textContent = initials(server.project_name || server.name);
    }
    button.title = server.available ? server.name : `${server.name} (folder unavailable)`;
    button.disabled = !server.available;
    button.setAttribute("aria-label", `Switch to ${server.name}`);
    button.setAttribute("aria-current", isActive ? "true" : "false");
    button.onclick = () => switchServer(server.server_id);
    $("serverList").appendChild(button);
  });
}

async function switchServer(serverId) {
  if (serverId === state.activeServerId && !state.hostSessionsView) return;
  state.hostSessionsView = false;
  state.hostSessionId = null; setHostMode(false);
  clearPendingAttachments();
  await api(`/api/servers/${serverId}/activate`, { method: "POST", body: "{}" });
  state.activeServerId = serverId;
  state.currentChatId = null;
  state.currentChatUpdatedAt = null;
  state.agents = [];
  state.chats = [];
  state.chatListSignature = "";
  await loadServers();
  await loadActiveServer();
}

async function loadActiveServer() {
  $("peoplePanel").hidden = false;
  $("chatListHeading").textContent = "Direct Messages";
  if (!state.activeServerId) {
    renderEmptyServerState();
    return;
  }
  setWorkspaceAvailable(true);
  await loadHealth();
  await loadAgents();
  await loadChats();
  await loadAllSessions();
  refreshPushSettings();
}

async function refreshPushSettings() {
  const status = $("pushStatus");
  const hint = $("pushInstallHint");
  try {
    const pushState = await window.AgentSlackPush.state();
    status.textContent = pushState === "subscribed" ? "Notifications are enabled on this device."
      : pushState === "needs-install" ? "Install Agent Slack to your Home Screen to enable notifications."
        : pushState === "denied" ? "Notifications are blocked in browser settings."
          : pushState === "unsupported" ? "Push notifications are not supported by this browser."
            : "Get a notification when an agent run finishes.";
    hint.hidden = pushState !== "needs-install";
    $("pushEnableBtn").hidden = pushState === "subscribed" || pushState === "unsupported" || pushState === "denied";
    $("pushDisableBtn").hidden = pushState !== "subscribed";
    $("pushTestBtn").disabled = pushState !== "subscribed";
  } catch (error) { status.textContent = error.message; }
}

$("pushEnableBtn").onclick = async () => {
  const status = $("pushStatus");
  try { await window.AgentSlackPush.enable(); await refreshPushSettings(); }
  catch (error) { status.textContent = error.message; }
};
$("pushDisableBtn").onclick = async () => {
  try { await window.AgentSlackPush.disable(); await refreshPushSettings(); }
  catch (error) { $("pushStatus").textContent = error.message; }
};
$("pushTestBtn").onclick = async () => {
  try { await window.AgentSlackPush.test(); $("pushStatus").textContent = "Test notification sent."; }
  catch (error) { $("pushStatus").textContent = error.message; }
};

async function openHostSessionsServer() {
  state.hostSessionOpenRequest += 1;
  state.hostSessionsView = true;
  state.hostSessionId = null; setHostMode(false);
  state.hostSessionStatus = null;
  state.hostMessagesStatus = null;
  state.currentChatId = null;
  renderServers();
  $("workspaceLabel").textContent = "Host Sessions";
  $("chatListHeading").textContent = "Host Sessions";
  $("peoplePanel").hidden = true;
  $("serverSettingsBtn").disabled = true;
  $("refreshAgentsBtn").disabled = true;
  $("newChatBtnHeader").disabled = true;
  $("runAgentsBtn").disabled = true;
  $("autoMeetingBtn").disabled = true;
  $("manualMeetingBtn").disabled = true;
  $("deleteChatBtn").disabled = true;
  $("messageInput").disabled = true;
  $("sendBtn").disabled = true;
  $("sendBtn").textContent = "Send";
  $("messageInput").placeholder = "Select a host session to continue";
  await loadAllSessions();
  renderUnonboardedHostSessions();
  setChatTitle("Select a host session", false);
  $("chatMembers").textContent = "Sessions from folders not onboarded in Agent Slack";
  $("messages").innerHTML = '<div class="empty-server"><strong>Host sessions</strong><p>Choose a Claude Code or Codex conversation from the sidebar.</p></div>';
}

function activeServer() {
  return state.servers.find((server) => server.server_id === state.activeServerId) || null;
}

function renderUnonboardedHostSessions() {
  const sessions = state.hostSessions
    .filter((session) => !mostSpecificServer(session.project_root))
    .sort(compareHostSessions);
  $("chatList").innerHTML = "";
  $("chatCount").textContent = String(sessions.length);
  sessions.forEach((session) => $("chatList").appendChild(renderHostSessionRow(session)));
  renderProjectHostSessions();
}

function renderHostSessionRow(session) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = `chat-row ${session.session_id === state.hostSessionId ? "active" : ""}`;
  button.classList.toggle("unavailable", !session.available);
  const isResuming = session.resuming || state.hostResumes.has(session.session_id);
  const isRunning = session.status === "Running" || isResuming;
  const statusLabel = isResuming ? "Resuming…" : session.awaiting ? "Needs you" : isRunning ? "Running" : "Idle";
  const title = document.createElement("strong"); title.textContent = session.title;
  const subtitle = document.createElement("span"); subtitle.textContent = `${session.agent_type} · ${session.project_name}`;
  const small = document.createElement("small");
  const status = document.createElement("span"); status.className = `host-session-status ${session.awaiting ? "is-awaiting" : isRunning ? "is-running" : ""}`;
  if (isRunning) { const dot = document.createElement("i"); dot.className = "host-session-dot"; dot.setAttribute("aria-hidden", "true"); status.appendChild(dot); }
  status.append(document.createTextNode(statusLabel));
  small.append(status, document.createTextNode(` · ${session.last_message_preview || session.session_id}`));
  if (session.queued_count) {
    const queuedChip = document.createElement("span"); queuedChip.className = "queued-chip";
    queuedChip.textContent = `${session.queued_count} queued`;
    small.append(document.createTextNode(" · "), queuedChip);
  }
  if (session.subagents_total) {
    const chip = document.createElement("span"); chip.className = `subagent-chip ${session.subagents_running ? "is-running" : ""}`;
    chip.textContent = `${session.subagents_total} agents · ${session.subagents_running || 0} running`;
    small.append(document.createTextNode(" · "), chip);
  }
  if (session.is_subagent) {
    const orphan = document.createElement("span"); orphan.className = "subagent-chip orphan";
    orphan.textContent = session.parent_missing ? "Orphan sub-agent" : "Sub-agent";
    small.append(document.createTextNode(" · "), orphan);
  }
  button.append(title, subtitle, small);
  button.title = session.available ? `${session.project_name} · ${session.session_id}` : `${session.project_name} · ${session.session_id} · Folder unavailable; session is read-only`;
  button.onclick = () => openHostSession(session.session_id);
  return button;
}

function compareHostSessions(left, right) {
  const isRunning = (session) => session.status === "Running" || session.resuming || state.hostResumes.has(session.session_id);
  const runningOrder = Number(isRunning(right)) - Number(isRunning(left));
  if (runningOrder) return runningOrder;
  return String(right.updated_at || "").localeCompare(String(left.updated_at || ""));
}

function normalizeFolder(folder) {
  const normalized = String(folder || "").replace(/\\/g, "/").replace(/\/+$/, "");
  return normalized || (String(folder || "").startsWith("/") ? "/" : "");
}

function folderIsOnboarded(folder, roots) {
  const candidate = normalizeFolder(folder);
  return roots.some((root) => candidate === root || candidate.startsWith(`${root === "/" ? "" : root}/`));
}

function mostSpecificServer(folder) {
  const candidate = normalizeFolder(folder);
  return state.servers.filter((server) => {
    const root = normalizeFolder(server.project_root);
    return root && (candidate === root || candidate.startsWith(`${root === "/" ? "" : root}/`));
  }).sort((a, b) => normalizeFolder(b.project_root).length - normalizeFolder(a.project_root).length)[0] || null;
}

function renderProjectHostSessions() {
  const section = $("projectHostSessionsSection");
  const server = activeServer();
  const visible = Boolean(server && !state.hostSessionsView);
  section.hidden = !visible;
  if (!visible) return;
  const root = normalizeFolder(server.project_root);
  const sessions = state.hostSessions.filter((session) => mostSpecificServer(session.project_root)?.server_id === server.server_id && folderIsOnboarded(session.project_root, [root])).sort(compareHostSessions);
  $("projectHostSessionCount").textContent = String(sessions.length);
  const list = $("projectHostSessionsList");
  list.replaceChildren(...sessions.map(renderHostSessionRow));
}

async function loadAllSessions() {
  try {
    const payload = await api("/api/host-sessions");
    state.hostSessions = payload.sessions || [];
    if (state.hostSessionsView) renderUnonboardedHostSessions();
    else renderProjectHostSessions();
    if (state.hostSessionId) {
      const activeSession = state.hostSessions.find((session) => session.session_id === state.hostSessionId);
      if (activeSession) {
        renderHostSessionDetails(activeSession);
        syncHostComposer(activeSession);
        if (state.hostSubagentId) {
          await openHostSubagent(activeSession.session_id, state.hostSubagentId);
        } else if ((state.currentChatUpdatedAt !== (activeSession.updated_at || null)
          || (activeSession.size_bytes !== undefined && state.currentChatSize !== activeSession.size_bytes))
          && !(document.activeElement === $("messageInput") && $("messageInput").value)) {
          await openHostSession(activeSession.session_id, { preserveScroll: true });
        }
      }
    }
  } catch (error) { console.warn("Unable to load all sessions", error); }
}

function setChatTitle(text, canRename) {
  $("chatTitle").textContent = text;
  $("chatRenameBtn").hidden = !canRename;
}

// Rename the open conversation in place: a host session (stored by Agent Slack, the CLI's transcript is
// untouched) or a project chat. Enter saves, Escape cancels; clearing a host session name restores the generated one.
function startTitleRename() {
  const target = state.hostSessionId ? { kind: "host", id: state.hostSessionId }
    : state.currentChatId ? { kind: "chat", id: state.currentChatId } : null;
  const heading = $("chatTitle");
  if (!target || heading.querySelector("input")) return;
  const original = heading.textContent;
  const input = document.createElement("input");
  input.className = "chat-title-input";
  input.value = original;
  input.maxLength = 60;
  input.setAttribute("aria-label", "Conversation name");
  heading.replaceChildren(input);
  input.focus();
  input.select();
  let finished = false;
  const finish = async (save) => {
    if (finished) return;
    finished = true;
    const name = input.value.trim();
    if (!save || name === original || (!name && target.kind === "chat")) { heading.textContent = original; return; }
    try {
      const path = target.kind === "host" ? `/api/host-sessions/${encodeURIComponent(target.id)}/title` : `/api/chats/${encodeURIComponent(target.id)}`;
      const result = await api(path, { method: "PATCH", body: JSON.stringify({ title: name }) });
      heading.textContent = result.title || original;
      if (target.kind === "host") await loadAllSessions(); else await loadChats({ openFirst: false });
    } catch (error) {
      heading.textContent = original;
      console.warn("Unable to rename conversation", error);
    }
  };
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") { event.preventDefault(); finish(true); }
    else if (event.key === "Escape") { event.preventDefault(); finish(false); }
  });
  input.addEventListener("blur", () => finish(true));
}

function setHostMode(on) {
  if (!on && $("hostResumeHint")) $("hostResumeHint").hidden = true;
  document.body.classList.toggle("host-session-open", Boolean(on));
}

function renderHostSessionDetails(session) {
  setHostMode(true);
  const isResuming = session.resuming || state.hostResumes.has(session.session_id);
  const status = isResuming ? "Resuming…" : session.status === "Running" ? "Running" : "Idle";
  state.hostSessionStatus = session.status || "History";
  $("chatMembers").textContent = `${status} · ${session.project_name} · ${session.agent_type} · ${session.session_id}`;
}

function renderHostProgress(steps, subagents = []) {
  const details = document.createElement("details");
  details.className = "host-progress";
  details.open = true;
  details.appendChild(createProgressHeader());
  const list = document.createElement("ul");
  list.className = "progress-steps";
  const nested = subagents.filter((agent) => agent.status === "running").map((agent) => ({
    kind: "subagent", state: "running", label: `Subagent: ${agent.role} – ${agent.current_step || agent.task || "Working"}`,
  }));
  const allSteps = progressActions([...steps, ...nested]);
  const visible = allSteps.slice(-4);
  visible.forEach((step) => list.appendChild(createHostProgressStep(step)));
  details.appendChild(list);
  if (allSteps.length > visible.length) {
    const showAll = document.createElement("button");
    showAll.type = "button";
    showAll.className = "progress-more";
    showAll.textContent = `Show all ${allSteps.length} steps`;
    showAll.onclick = () => {
      list.replaceChildren(...allSteps.map(createHostProgressStep));
      showAll.remove();
    };
    details.appendChild(showAll);
  }
  $("messages").appendChild(details);
}

// The agent is blocked on a question or plan approval that is answered in the terminal.
function renderHostAwaiting(awaiting) {
  const card = document.createElement("section");
  card.className = `host-awaiting kind-${awaiting.kind}`;
  const heading = document.createElement("div");
  heading.className = "host-awaiting-heading";
  heading.textContent = awaiting.kind === "plan" ? "Plan ready for your approval" : "Waiting for your answer";
  const text = document.createElement("div");
  text.className = "host-awaiting-text";
  text.textContent = awaiting.text;
  card.append(heading, text);
  if (awaiting.options?.length) {
    const options = document.createElement("ul");
    options.className = "host-awaiting-options";
    awaiting.options.forEach((label) => {
      const item = document.createElement("li");
      item.textContent = label;
      options.appendChild(item);
    });
    card.appendChild(options);
  }
  const note = document.createElement("div");
  note.className = "host-awaiting-note";
  note.textContent = `Answer in the terminal${awaiting.multi ? " (you can pick several)" : ""}${awaiting.questions > 1 ? ` · ${awaiting.questions} questions` : ""}.`;
  card.appendChild(note);
  $("messages").appendChild(card);
}

// Messages submitted in the terminal while the agent was busy; they become normal turns once dequeued.
function renderHostQueue(queued) {
  const wrap = document.createElement("section");
  wrap.className = "host-queue";
  const heading = document.createElement("div");
  heading.className = "host-queue-heading";
  heading.textContent = queued.length === 1 ? "1 message queued" : `${queued.length} messages queued`;
  wrap.appendChild(heading);
  queued.forEach((item) => {
    const bubble = document.createElement("div");
    bubble.className = "host-queue-item";
    const pill = document.createElement("span");
    pill.className = "host-queue-pill";
    pill.textContent = "Queued";
    const text = document.createElement("span");
    text.className = "host-queue-text";
    text.textContent = item.content;
    bubble.append(pill, text);
    wrap.appendChild(bubble);
  });
  $("messages").appendChild(wrap);
}

function createProgressHeader(elapsed = null) {
  const header = document.createElement("summary");
  header.className = "managed-progress-header";
  const spinner = document.createElement("span");
  spinner.className = "progress-spinner";
  spinner.setAttribute("aria-hidden", "true");
  const title = document.createElement("span");
  title.className = "progress-title";
  title.textContent = "Working…";
  header.append(spinner, title);
  if (elapsed) header.appendChild(elapsed);
  return header;
}

function createHostProgressStep(step) {
  const running = step.state === "running";
  const item = document.createElement("li");
  item.className = `progress-step ${running ? "step-running" : "step-done"} kind-${step.kind || "tool"}`;
  const dot = document.createElement("span");
  dot.className = "progress-dot";
  dot.setAttribute("aria-hidden", "true");
  const label = document.createElement("span");
  label.className = "progress-label";
  label.textContent = step.label || "Working";
  label.title = label.textContent;
  item.append(dot, label);
  return item;
}

// Assistant commentary is already visible as chat text; the progress list only shows actions.
function progressActions(steps) {
  return steps.filter((step) => step.kind !== "text" && step.kind !== "commentary");
}

function permissionInputSummary(input) {
  if (typeof input === "string") return input.slice(0, 300);
  if (!input || typeof input !== "object") return String(input ?? "");
  const value = input.command || input.description || input.path || input.file_path || input.url || JSON.stringify(input);
  return String(value).slice(0, 300);
}

// Claude's AskUserQuestion arrives as a permission request; answer it with options and free text instead of approve/deny.
function renderQuestionForm(permission, card) {
  const questions = Array.isArray(permission.tool_input?.questions) ? permission.tool_input.questions : [];
  const form = document.createElement("form");
  form.className = "permission-question-form";
  const fields = questions.map((question, index) => {
    const box = document.createElement("fieldset");
    box.className = "permission-question";
    const legend = document.createElement("legend");
    legend.textContent = [question.header, question.question].filter(Boolean).join(" · ") || `Question ${index + 1}`;
    box.appendChild(legend);
    const multi = Boolean(question.multiSelect);
    const inputs = (question.options || []).map((option) => {
      const label = document.createElement("label");
      label.className = "permission-option";
      const input = document.createElement("input");
      input.type = multi ? "checkbox" : "radio";
      input.name = `q${index}`;
      input.value = option.label || "";
      const text = document.createElement("span");
      text.textContent = option.label || "";
      if (option.description) { const hint = document.createElement("small"); hint.textContent = option.description; text.appendChild(hint); }
      label.append(input, text);
      box.appendChild(label);
      return input;
    });
    const other = document.createElement("input");
    other.type = "text";
    other.className = "permission-other";
    other.placeholder = "Or type your own answer";
    other.oninput = () => { if (other.value && !multi) inputs.forEach((item) => { item.checked = false; }); };
    inputs.forEach((item) => { item.onchange = () => { if (!multi && item.checked) other.value = ""; }; });
    box.appendChild(other);
    form.appendChild(box);
    return { question: question.question || legend.textContent, inputs, other };
  });
  const actions = document.createElement("div");
  actions.className = "permission-card-actions";
  const submit = document.createElement("button");
  submit.type = "submit";
  submit.className = "permission-approve";
  submit.textContent = "Send answer";
  const skip = document.createElement("button");
  skip.type = "button";
  skip.className = "permission-deny";
  skip.textContent = "Skip";
  const send = async (body) => {
    submit.disabled = skip.disabled = true;
    try { await api(`/api/permissions/${encodeURIComponent(permission.request_id)}/decision`, { method: "POST", body: JSON.stringify(body) }); }
    catch (error) { if (![404, 409].includes(error.status)) console.warn("Unable to answer question", error); submit.disabled = skip.disabled = false; }
    await loadPermissions();
  };
  form.onsubmit = (event) => {
    event.preventDefault();
    const answers = {};
    fields.forEach((field) => {
      const typed = field.other.value.trim();
      const picked = field.inputs.filter((item) => item.checked).map((item) => item.value);
      const value = [...picked, ...(typed ? [typed] : [])].join(", ");
      if (value) answers[field.question] = value;
    });
    if (!Object.keys(answers).length) return;
    void send({ decision: "allow", answers });
  };
  skip.onclick = () => send({ decision: "deny", reason: "The user chose not to answer in Agent Slack." });
  actions.append(submit, skip);
  form.appendChild(actions);
  card.appendChild(form);
}

async function loadPermissions() {
  if (document.hidden) return;
  try {
    const payload = await api("/api/permissions");
    state.permissions = payload.permissions || [];
    renderPermissions();
  } catch (error) {
    if (!/401|Authentication required/.test(error.message)) console.warn("Unable to load permission requests", error);
  }
}

function renderPermissions() {
  const root = $("permissionCards");
  const pending = state.permissions.filter((item) => item.status === "pending" && item.expires * 1000 > Date.now());
  $("permissionCount").textContent = String(pending.length);
  $("permissionCount").hidden = !pending.length;
  // The list is polled every few seconds; rebuilding unchanged cards would wipe what the user is typing or selecting.
  const visible = [...pending, ...state.permissions.filter((item) => item.status === "resolved" && Date.now() - item.resolved_at * 1000 < 5000)];
  const signature = visible.map((item) => `${item.request_id}:${item.status}:${item.decision || ""}`).join("|")
    + `|${new URLSearchParams(window.location.hash.slice(1)).get("permission") || ""}`;
  if (signature === state.permissionSignature) return;
  state.permissionSignature = signature;
  root.replaceChildren();
  const focusId = new URLSearchParams(window.location.hash.slice(1)).get("permission");
  visible.forEach((permission) => {
    const card = document.createElement("article");
    card.className = `permission-card ${permission.status === "resolved" ? "resolved" : ""} ${permission.request_id === focusId ? "is-focused" : ""}`;
    card.dataset.permissionId = permission.request_id;
    const header = document.createElement("header");
    const title = document.createElement("span");
    title.textContent = `${permission.source === "codex" ? "Codex" : "Claude"} · ${permission.tool_name || "Tool"}`;
    const cwd = document.createElement("span");
    cwd.textContent = permission.cwd || "Unknown project";
    header.append(title, cwd);
    const summary = document.createElement("div");
    summary.className = "permission-card-summary";
    summary.textContent = permission.status === "resolved" ? (permission.tool_name === "AskUserQuestion" ? (permission.decision === "allow" ? "Answered" : "Skipped") : permission.decision === "allow" ? "Approved" : "Denied") : permissionInputSummary(permission.tool_input);
    card.append(header, summary);
    if (permission.status === "pending" && permission.tool_name === "AskUserQuestion" && permission.tool_input?.questions) {
      summary.textContent = "Claude is asking you a question";
      renderQuestionForm(permission, card);
    } else if (permission.status === "pending") {
      const expiry = document.createElement("small");
      expiry.className = "permission-expiry";
      expiry.textContent = `Expires in ${Math.max(0, Math.ceil(permission.expires - Date.now()))}s`;
      const actions = document.createElement("div");
      actions.className = "permission-card-actions";
      [["Approve", "allow", "permission-approve"], ["Deny", "deny", "permission-deny"]].forEach(([label, decision, className]) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = className;
        button.textContent = label;
        button.onclick = async () => {
          button.disabled = true;
          try { await api(`/api/permissions/${encodeURIComponent(permission.request_id)}/decision`, { method: "POST", body: JSON.stringify({ decision }) }); }
          catch (error) { if (![404, 409].includes(error.status)) console.warn("Unable to resolve permission", error); }
          await loadPermissions();
        };
        actions.appendChild(button);
      });
      card.append(expiry, actions);
    }
    root.appendChild(card);
    if (permission.request_id === focusId) requestAnimationFrame(() => card.scrollIntoView?.({ block: "center" }));
  });
}

async function handleDeepLink() {
  const params = new URLSearchParams(window.location.hash.slice(1));
  if (params.has("permission")) {
    await loadPermissions();
    return;
  }
  const hostId = params.get("host");
  if (!hostId) return;
  const session = state.hostSessions.find((item) => item.session_id === hostId);
  if (session) {
    const owner = mostSpecificServer(session.project_root);
    if (owner && (owner.server_id !== state.activeServerId || state.hostSessionsView)) await switchServer(owner.server_id);
    else if (!owner && !state.hostSessionsView) await openHostSessionsServer();
    await openHostSession(hostId);
  } else if (!state.hostSessionsView) {
    await loadAllSessions();
    const discovered = state.hostSessions.find((item) => item.session_id === hostId);
    if (discovered) await handleDeepLink();
  }
}

function syncHostComposer(session) {
  const resuming = state.hostResumes.has(session.session_id) || session.resuming;
  const available = Boolean(session.available);
  $("messageInput").disabled = !available || Boolean(resuming);
  $("messageInput").placeholder = !available
    ? "Folder unavailable; this session is read-only"
    : resuming ? "Working…" : "Message · @ mention · /path to attach";
  $("sendBtn").disabled = !available || Boolean(resuming);
  $("sendBtn").textContent = resuming ? "Working..." : "Send";
}

async function openHostSession(sessionId, { preserveScroll = false } = {}) {
  state.hostSubagentId = null;
  const requestId = ++state.hostSessionOpenRequest;
  const cached = state.hostSessionDetails.get(sessionId);
  if (cached) renderHostSession(cached, sessionId, requestId, preserveScroll);
  try {
    const session = await api(`/api/host-sessions/${encodeURIComponent(sessionId)}`);
    state.hostSessionDetails.set(session.session_id, session);
    if (requestId !== state.hostSessionOpenRequest || (!state.hostSessionsView && !state.activeServerId)) return;
    renderHostSession(session, sessionId, requestId, preserveScroll);
  } catch (error) {
    if (requestId !== state.hostSessionOpenRequest || (!state.hostSessionsView && !state.activeServerId)) return;
    const message = error.message || "The session could not be loaded.";
    if (state.hostSessionLastError.get(sessionId) !== message) {
      state.hostSessionLastError.set(sessionId, message);
      const messages = $("messages");
      let row = [...messages.querySelectorAll("[data-host-session-error]")]
        .find((item) => item.dataset.hostSessionError === sessionId);
      if (!row) {
        row = document.createElement("article");
        row.className = "message host-session-error";
        row.dataset.hostSessionError = sessionId;
        row.setAttribute("role", "status");
        messages.appendChild(row);
      }
      row.textContent = `Unable to load host session: ${message}`;
    }
    console.warn("Unable to load host session", sessionId, error);
  }
}

function renderHostSession(session, sessionId, requestId, preserveScroll) {
  if (requestId !== state.hostSessionOpenRequest || (!state.hostSessionsView && !state.activeServerId)) return;
  const messages = $("messages");
  const followLatest = messages.scrollHeight - messages.scrollTop - messages.clientHeight < 80;
  const previousScrollTop = messages.scrollTop;
  state.hostSessionId = session.session_id;
  state.currentChatId = session.session_id;
  state.currentChatUpdatedAt = session.updated_at;
  state.currentChatSize = session.size_bytes;
  state.hostMessagesStatus = session.status || "History";
  state.hostSessionLastError.delete(sessionId);
  $("deleteChatBtn").disabled = true;
  if (state.hostSessionsView) renderUnonboardedHostSessions(); else renderProjectHostSessions();
  syncHostComposer(session);
  const hostHint = $("hostResumeHint");
  hostHint.textContent = session.resume_hint || "";
  hostHint.hidden = !hostHint.textContent;
  ["runAgentsBtn", "autoMeetingBtn", "manualMeetingBtn"].forEach((id) => { $(id).disabled = true; });
  setChatTitle(session.title, true);
  renderHostSessionDetails(session);
  state.streamingRows.forEach(clearManagedProgress);
  state.streamingRows.clear();
  $("messages").innerHTML = "";
  renderHostSubagents(session);
  let previousAuthor = null;
  (session.messages || []).forEach((message) => {
    const row = document.createElement("article");
    const author = message.author_label || message.author_id;
    const showAuthor = !["user", "system"].includes(message.author_type) && author !== previousAuthor;
    row.className = `message ${message.author_type}${showAuthor ? " show-author" : ""}`;
    row.innerHTML = `<div class="message-avatar">${initials(message.author_label)}</div><div class="message-body"><header><strong>${escapeHtml(message.author_label)}</strong></header><div class="message-content">${renderMarkdown(message.body)}</div></div>`;
    $("messages").appendChild(row);
    previousAuthor = author;
  });
  state.hostSessionMessages.set(session.session_id, session.messages || []);
  renderHostPendingPrompts(session.session_id, session.messages || []);
  if (session.resuming && !state.hostResumes.has(session.session_id)) {
    state.hostResumes.set(session.session_id, true);
    void resumeHostSessionInBackground(session.session_id, "", { attach: true });
  }
  if (session.status === "Running" && (session.progress?.length || session.subagents?.length)) renderHostProgress(session.progress || [], session.subagents || []);
  if (session.status === "Running" && session.queued?.length) renderHostQueue(session.queued);
  if (session.awaiting) renderHostAwaiting(session.awaiting);
  messages.scrollTop = preserveScroll && !followLatest ? previousScrollTop : messages.scrollHeight;
}

function renderHostSubagents(session) {
  const agents = session.subagents || [];
  if (!agents.length) return;
  const running = agents.some((agent) => agent.status === "running");
  const panel = document.createElement("details");
  panel.className = "host-subagents";
  panel.open = running;
  const summary = document.createElement("summary");
  const finished = agents.filter((agent) => agent.status === "done").length;
  const attention = agents.filter((agent) => ["failed", "aborted", "stalled"].includes(agent.status)).length;
  summary.textContent = running ? `Agents · ${agents.filter((agent) => agent.status === "running").length} running`
    : attention ? `Agents · ${finished} finished · ${attention} need attention`
      : `${agents.length} agents finished`;
  panel.appendChild(summary);
  agents.forEach((agent) => {
    const card = document.createElement("button"); card.type = "button"; card.className = "subagent-card";
    const name = document.createElement("strong"); name.textContent = `${agent.nickname} · ${agent.role}`;
    const status = document.createElement("span"); status.className = `subagent-status ${agent.status}`;
    status.textContent = ({ running: "◌ Running", done: "✓ Done", failed: "! Failed", aborted: "↶ Aborted", stalled: "… Stalled" })[agent.status] || agent.status;
    const task = document.createElement("span"); task.textContent = agent.task || "Sub-agent task";
    card.append(name, status, task);
    if (agent.status === "running" && agent.current_step) { const step = document.createElement("small"); step.textContent = agent.current_step; card.appendChild(step); }
    if (agent.status === "done" && agent.result_preview) { const result = document.createElement("small"); result.textContent = agent.result_preview; card.appendChild(result); }
    card.onclick = () => openHostSubagent(session.session_id, agent.id);
    panel.appendChild(card);
  });
  $("messages").appendChild(panel);
}

async function openHostSubagent(parentId, subId) {
  try {
    const child = await api(`/api/host-sessions/${encodeURIComponent(parentId)}/subagents/${encodeURIComponent(subId)}`);
    state.hostSubagentId = subId;
    const messages = $("messages"); messages.replaceChildren();
    const back = document.createElement("button"); back.type = "button"; back.className = "secondary-button"; back.textContent = "← Back to parent";
    back.onclick = () => openHostSession(parentId, { preserveScroll: true }); messages.appendChild(back);
    const heading = document.createElement("h3"); heading.textContent = `${child.nickname} · ${child.role} · ${child.status}`; messages.appendChild(heading);
    (child.messages || []).forEach((message) => {
      const row = document.createElement("article"); row.className = `message ${message.author_type}`;
      const body = document.createElement("div"); body.className = "message-content"; body.textContent = message.body; row.appendChild(body); messages.appendChild(row);
    });
    if (child.progress?.length) renderHostProgress(child.progress);
    $("messageInput").disabled = true; $("sendBtn").disabled = true;
  } catch (error) { console.warn("Unable to load sub-agent", error); }
}

// A host-session prompt stays visible until the transcript confirms it, including after a failed resume.
function appendPendingUserRow(body, note = "", pending = null) {
  const row = document.createElement("article");
  row.className = "message user pending";
  row.dataset.pending = "1";
  if (pending?.request_id) row.dataset.requestId = pending.request_id;
  row.innerHTML = `<div class="message-avatar">${initials("You")}</div><div class="message-body"><header><strong>You</strong><time>sending…</time></header><div class="message-content">${renderMarkdown(body)}</div></div>`;
  if (note) {
    const extra = document.createElement("div");
    extra.className = "pending-note";
    extra.textContent = note;
    row.querySelector(".message-body").appendChild(extra);
  }
  $("messages").appendChild(row);
  if (pending?.partial_text) appendPendingAssistantText(pending.request_id, pending.partial_text);
  if (pending?.failed) markPendingPromptFailed(row, pending);
  $("messages").scrollTop = $("messages").scrollHeight;
  return row;
}

function countUserMessages(messages, body) {
  const wanted = body.trim();
  return (messages || []).filter((message) => message.author_type === "user" && String(message.body || "").trim() === wanted).length;
}

function appendHostSessionPrompt(sessionId, body) {
  // baseline: how many identical prompts the transcript already holds, so a repeated "continue" is not
  // mistaken for the one we are still waiting to see.
  const known = state.hostSessionMessages.get(sessionId) || [];
  const pending = state.hostPendingPrompts.get(sessionId) || [];
  const requestId = globalThis.crypto?.randomUUID?.() || `resume-${Date.now()}-${Math.random().toString(36).slice(2)}`;
  const prompt = { request_id: requestId, body, baseline: countUserMessages(known, body) + pending.filter((item) => item.body.trim() === body.trim()).length };
  pending.push(prompt);
  state.hostPendingPrompts.set(sessionId, pending);
  saveHostPendingPrompts();
  appendPendingUserRow(body, "", prompt);
  return requestId;
}

// Re-draw prompts the transcript does not contain yet (the CLI writes them a moment after it starts).
function renderHostPendingPrompts(sessionId, messages) {
  const pending = state.hostPendingPrompts.get(sessionId);
  if (!pending?.length) return;
  const remaining = pending.filter((item) => countUserMessages(messages, item.body) <= item.baseline);
  if (remaining.length) state.hostPendingPrompts.set(sessionId, remaining); else state.hostPendingPrompts.delete(sessionId);
  saveHostPendingPrompts();
  remaining.forEach((item) => appendPendingUserRow(item.body, "", item));
}

function appendPendingAssistantText(requestId, text) {
  const row = [...document.querySelectorAll("#messages [data-request-id]")].reverse()
    .find((item) => item.dataset.requestId === requestId);
  if (!row) return;
  let partial = row.querySelector(".pending-assistant");
  if (!partial) {
    partial = document.createElement("div");
    partial.className = "pending-assistant message-content";
    row.appendChild(partial);
  }
  partial.textContent = text;
}

function markPendingPromptFailed(row, pending) {
  row.classList.add("failed");
  const time = row.querySelector("time");
  if (time) time.textContent = pending.cancelled ? "stopped · kept here" : "failed · kept here";
  if (row.querySelector("[data-retry]")) return;
  const retry = document.createElement("button");
  retry.type = "button";
  retry.className = "secondary-button";
  retry.dataset.retry = "1";
  retry.textContent = "Retry";
  retry.onclick = () => { $("messageInput").value = pending.body; $("messageInput").focus(); };
  row.appendChild(retry);
}

function formatClock(seconds) {
  const total = Math.max(0, Math.round(seconds || 0));
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

// While the CLI works: live clock on the button, stage text, and a Stop button.
function renderHostResumeProgress(sessionId, status) {
  if (state.hostSessionId !== sessionId) return;
  const loading = status.stage === "starting";
  $("sendBtn").textContent = `${loading ? "Loading session" : "Working"}… ${formatClock(status.elapsed)}`;
  $("messageInput").placeholder = loading ? "Loading the session…" : "Working… you can stop it at any time";
  $("stopRunBtn").hidden = false;
  $("stopRunBtn").disabled = false;
  $("stopRunBtn").textContent = "Stop";
}

// A failed poll (network blip, phone tab suspended, proxy 5xx) says nothing about the run: the CLI keeps
// working on the server. Only a status the server reports (failed / cancelled) ends the wait.
const RESUME_POLL_TOLERANCE = 30;

async function waitForHostSessionResume(sessionId, requestId) {
  let misses = 0;
  let after = -1;
  while (true) {
    let status;
    try {
      status = await api(`/api/host-sessions/${encodeURIComponent(sessionId)}/resume?after=${after}`);
      misses = 0;
    } catch (error) {
      if (error.status === 401 || ++misses > RESUME_POLL_TOLERANCE) throw error;
      await new Promise((resolve) => setTimeout(resolve, Math.min(5000, 1000 * misses)));
      continue;
    }
    if (status.request_id && requestId && status.request_id !== requestId) {
      const stale = new Error("Resume status belongs to a different request");
      stale.lost = true;
      throw stale;
    }
    let assistantDelta = "";
    for (const event of status.events || []) {
      after = Math.max(after, Number(event.i) || 0);
      if (event.type === "assistant" && typeof event.text === "string") assistantDelta += event.text;
    }
    if (Number.isFinite(status.next)) after = Math.max(after, status.next - 1);
    if (status.session_id && status.session_id !== sessionId) {
      transferPendingHostPrompts(sessionId, status.session_id);
      if (state.hostSessionId === status.session_id) void openHostSession(status.session_id, { preserveScroll: true });
    }
    const pending = (state.hostPendingPrompts?.get(status.session_id || sessionId) || [])
      .find((item) => item.request_id === requestId);
    if (pending && (typeof status.partial_text === "string" || assistantDelta)) {
      pending.partial_text = typeof status.partial_text === "string"
        ? status.partial_text : `${pending.partial_text || ""}${assistantDelta}`;
      saveHostPendingPrompts();
      appendPendingAssistantText(requestId, pending.partial_text);
    }
    if (status.status === "completed") return status;
    if (status.status === "cancelled") { const stopped = new Error("Stopped"); stopped.cancelled = true; throw stopped; }
    if (status.status === "failed") {
      const failure = new Error(status.error || "Native session resume failed");
      failure.detail = status.detail || "";
      throw failure;
    }
    if (status.status !== "running") {
      // The server forgot the job (it restarted). The transcript is the source of truth, so keep the prompt.
      const lost = new Error("Lost track of the run (the server may have restarted). Showing the transcript as it stands.");
      lost.lost = true;
      throw lost;
    }
    renderHostResumeProgress(sessionId, status);
    await new Promise((resolve) => setTimeout(resolve, 400));
  }
}

function transferPendingHostPrompts(sourceId, returnedId) {
  if (sourceId === returnedId) return;
  if (state.hostPendingPrompts?.has(sourceId)) {
    const pending = state.hostPendingPrompts.get(sourceId);
    const known = state.hostSessionMessages.get(returnedId) || [];
    state.hostPendingPrompts.set(returnedId, pending.map((item, index) => ({
      ...item,
      baseline: Math.max(item.baseline || 0, countUserMessages(known, item.body)
        + pending.slice(0, index).filter((prior) => prior.body.trim() === item.body.trim()).length),
    })));
    state.hostPendingPrompts.delete(sourceId);
    saveHostPendingPrompts();
  }
  if (state.hostResumes.has(sourceId)) {
    state.hostResumeAliases.set(sourceId, returnedId);
    state.hostResumes.set(returnedId, true);
  }
  if (state.hostSessionId === sourceId) state.hostSessionId = returnedId;
}

// attach: the page was reloaded (or opened elsewhere) while the server's CLI run is still going, so only follow it.
async function resumeHostSessionInBackground(sessionId, body, options) {
  const attach = Boolean(options && options.attach);
  let requestId = options?.requestId || "";
  try {
    if (!requestId) requestId = (state.hostPendingPrompts.get(sessionId) || []).find((item) => !item.failed)?.request_id || "";
    if (!attach) {
      await api(`/api/host-sessions/${encodeURIComponent(sessionId)}/resume`, {
        method: "POST", body: JSON.stringify({ body, request_id: requestId }),
      });
    }
    const resumed = await waitForHostSessionResume(sessionId, requestId);
    await loadAllSessions();
    const returnedId = resumed.session_id || sessionId;
    if (returnedId !== sessionId) transferPendingHostPrompts(sessionId, returnedId);
    if (state.hostSessionId === sessionId || state.hostSessionId === returnedId) {
      state.hostSessionId = returnedId;
      await openHostSession(returnedId, { preserveScroll: true });
    }
  } catch (error) {
    const pendingSessionId = state.hostResumeAliases.get(sessionId) || sessionId;
    const pending = state.hostPendingPrompts.get(pendingSessionId) || [];
    pending.forEach((item) => { if (!requestId || item.request_id === requestId) { item.failed = true; item.cancelled = Boolean(error.cancelled); } });
    if (pending.length) state.hostPendingPrompts.set(pendingSessionId, pending);
    saveHostPendingPrompts();
    document.querySelectorAll("#messages [data-pending]").forEach((row) => {
      const item = pending.find((candidate) => candidate.request_id === row.dataset.requestId);
      if (item) markPendingPromptFailed(row, item);
    });
    if (state.hostSessionId === sessionId || state.hostSessionId === pendingSessionId) {
      const row = document.createElement("article");
      row.className = "message host-session-error";
      row.setAttribute("role", "status");
      row.textContent = error.lost ? error.message : error.cancelled ? "Stopped. Your message is back in the box." : `Unable to continue native session: ${error.message}`;
      if (error.detail) {
        const details = document.createElement("details");
        const summary = document.createElement("summary");
        summary.textContent = "Details";
        const raw = document.createElement("pre");
        raw.textContent = error.detail;
        details.append(summary, raw);
        row.appendChild(details);
      }
      if (!error.lost && body && !pending.some((item) => item.request_id === requestId && item.body === body)) {
        const retry = document.createElement("button");
        retry.type = "button";
        retry.className = "secondary-button";
        retry.textContent = "Retry";
        retry.onclick = () => {
          $("messageInput").value = body;
          $("messageInput").focus();
        };
        row.appendChild(retry);
      }
      $("messages").appendChild(row);
      if (body) $("messageInput").value = body;
      $("messages").scrollTop = $("messages").scrollHeight;
    }
  } finally {
    state.hostResumes.delete(sessionId);
    const forkedSessionId = state.hostResumeAliases.get(sessionId);
    if (forkedSessionId) state.hostResumes.delete(forkedSessionId);
    state.hostResumeAliases.delete(sessionId);
    $("stopRunBtn").hidden = true;
    $("stopRunBtn").disabled = false;
    if (state.hostSessionsView) renderUnonboardedHostSessions();
    await loadAllSessions();
    const session = state.hostSessions.find((item) => item.session_id === state.hostSessionId);
    if (session) syncHostComposer(session);
  }
}

function setWorkspaceAvailable(available) {
  ["serverSettingsBtn", "newChatBtnHeader", "refreshAgentsBtn", "runAgentsBtn", "autoMeetingBtn", "manualMeetingBtn", "sendBtn"]
    .forEach((id) => { $(id).disabled = !available; });
  $("deleteChatBtn").disabled = !available || !state.currentChatId || state.runInProgress;
  $("messageInput").disabled = !available;
  $("objectiveInput").disabled = !available;
}

function renderEmptyServerState() {
  state.agents = [];
  state.chats = [];
  state.currentChatId = null;
  state.currentChatUpdatedAt = null;
  state.chatListSignature = "";
  state.orchestratorIds = [];
  $("workspaceLabel").textContent = "Agent Slack";
  setChatTitle("Add an agent system", false);
  $("chatMembers").textContent = "Each server connects to one local agent-system folder.";
  $("messages").innerHTML = `
    <div class="empty-server">
      <strong>No servers yet</strong>
      <p>Use the + button in the server rail to choose an agent-system folder.</p>
      <button type="button" id="emptyAddServerBtn">Add Agent System</button>
    </div>
  `;
  $("emptyAddServerBtn").onclick = openServerDialog;
  $("chatList").innerHTML = "";
  $("agentList").innerHTML = "";
  $("chatCount").textContent = "0";
  $("agentCount").textContent = "0";
  setWorkspaceAvailable(false);
  queueMicrotask(() => {
    if (!state.activeServerId && !$("serverDialog").open) openServerDialog();
  });
}

async function loadAgents() {
  const payload = await api("/api/agents");
  state.agents = payload.agents || [];
  $("agentCount").textContent = String(state.agents.length);
  renderAgents();
  renderDialogs();
}

function applyChatList(chats) {
  const signature = chatSync.chatListSignature(chats);
  state.chats = chats;
  $("chatCount").textContent = String(state.chats.length);
  if (signature !== state.chatListSignature) {
    state.chatListSignature = signature;
    renderChats();
    renderAgents();
  }
}

async function loadChats({ openFirst = true } = {}) {
  const payload = await api("/api/chats");
  applyChatList(payload.chats || []);
  const deepLinkChat = new URLSearchParams(window.location.hash.slice(1)).get("chat");
  if (deepLinkChat && state.chats.some((chat) => chat.chat_id === deepLinkChat)) {
    await openChat(deepLinkChat);
    return;
  }
  if (openFirst && !state.currentChatId && state.chats.length) {
    await openChat(state.chats[0].chat_id);
  } else if (!state.chats.length) {
    renderNoChatState();
  }
}

function renderNoChatState() {
  state.currentChatId = null;
  state.currentChatUpdatedAt = null;
  setChatTitle("Select a chat", false);
  $("chatMembers").textContent = "";
  $("messages").innerHTML = '<div class="empty-server"><strong>No chats yet</strong><p>Create a chat or open a person to begin.</p></div>';
  $("deleteChatBtn").disabled = true;
}

async function openChat(chatId) {
  try {
    const chat = await api(`/api/chats/${chatId}`);
    try { await loadRunnerOptions(); } catch (error) { console.warn("Unable to load runner options", error); }
    state.hostSessionId = null; setHostMode(false);
    ["runAgentsBtn", "autoMeetingBtn", "manualMeetingBtn"].forEach((id) => { $(id).disabled = !state.activeServerId; });
    $("messageInput").disabled = !state.activeServerId;
    $("sendBtn").disabled = !state.activeServerId;
    state.currentChatId = chatId;
    $("deleteChatBtn").disabled = state.runInProgress;
    renderChat(chat);
    // Run restoration and list refresh are secondary calls. They must not
    // turn a successfully loaded conversation into an "unable to open" error.
    try { await restoreActiveRun(chatId); } catch (error) { console.warn("Unable to restore active run", error); }
    try { await loadChats({ openFirst: false }); } catch (error) { console.warn("Unable to refresh chat list", error); }
    renderChats();
    renderAgents();
    if (state.mobileSidebarOpen) setMobileSidebarOpen(false);
  } catch (error) {
    console.warn("Unable to open chat", chatId, error);
    state.currentChatId = null;
    state.currentChatUpdatedAt = null;
    $("deleteChatBtn").disabled = true;
    setChatTitle("Unable to open chat", false);
    $("chatMembers").textContent = "";
    $("messages").innerHTML = `<div class="empty-server chat-open-error"><strong>Unable to open this chat</strong><p>${escapeHtml(error.message || "The chat could not be loaded.")}</p><button type="button" class="secondary-button" data-retry-chat="${escapeHtml(chatId)}">Retry</button></div>`;
    $("messages").querySelector("[data-retry-chat]")?.addEventListener("click", () => openChat(chatId));
  }
}

async function syncChatState() {
  if (state.hostSessionsView || state.hostSessionId || !state.activeServerId || state.streamAttached || state.syncInProgress || document.hidden) return;
  const serverId = state.activeServerId;
  state.syncInProgress = true;
  try {
    const payload = await api("/api/chats");
    if (serverId !== state.activeServerId) return;
    applyChatList(payload.chats || []);

    if (!state.currentChatId) {
      if (state.chats.length) await openChat(state.chats[0].chat_id);
      return;
    }
    const summary = state.chats.find((chat) => chat.chat_id === state.currentChatId);
    const chatId = state.currentChatId;
    if (chatSync.shouldRefreshChat(summary, state.currentChatUpdatedAt)) {
      const chat = await api(`/api/chats/${chatId}`);
      if (serverId === state.activeServerId && chatId === state.currentChatId && !state.streamAttached) {
        renderChat(chat);
      }
    }
    await restoreActiveRun(chatId);
  } catch (error) {
    console.warn("Agent Slack background sync failed", error);
  } finally {
    state.syncInProgress = false;
  }
}

function renderChats() {
  $("chatList").innerHTML = "";
  state.chats.forEach((chat) => {
    const button = document.createElement("button");
    button.className = `chat-row ${chat.chat_id === state.currentChatId ? "active" : ""}`;
    button.innerHTML = `
      <strong>${escapeHtml(chat.title)}</strong>
      <span>${escapeHtml(chat.member_titles.join(", "))}</span>
      <small>${escapeHtml(chat.last_message_preview || "No messages yet")}</small>
    `;
    button.onclick = () => openChat(chat.chat_id);
    $("chatList").appendChild(button);
  });
}

function renderAgents() {
  $("agentList").innerHTML = "";
  state.agents.forEach((agent) => {
    const card = document.createElement("button");
    const directChat = state.chats.find((chat) => isDirectChatWith(chat, agent.agent_id));
    card.type = "button";
    card.className = `agent-card ${directChat?.chat_id === state.currentChatId ? "active" : ""}`;
    card.dataset.description = agent.summary || "No summary";
    card.dataset.title = agent.title || agent.name || "Agent";
    card.setAttribute("aria-label", `Direct message ${agent.title}`);
    card.setAttribute("aria-current", directChat?.chat_id === state.currentChatId ? "page" : "false");
    card.disabled = state.openingDmAgentIds.has(agent.agent_id);
    card.innerHTML = `
      <div class="agent-badge">${initials(agent.title)}</div>
      <div class="agent-name">
        <strong>${escapeHtml(agent.title)}</strong>
      </div>
    `;
    card.addEventListener("click", () => openAgentDm(agent));
    card.addEventListener("mouseenter", (event) => showPeopleTooltip(event.currentTarget));
    card.addEventListener("mouseleave", hidePeopleTooltip);
    card.addEventListener("focus", (event) => showPeopleTooltip(event.currentTarget));
    card.addEventListener("blur", hidePeopleTooltip);
    $("agentList").appendChild(card);
  });
}

function isDirectChatWith(chat, agentId) {
  return chat.kind === "direct"
    && chat.member_ids.length === 1
    && chat.member_ids[0] === agentId;
}

async function openAgentDm(agent) {
  if (state.openingDmAgentIds.has(agent.agent_id)) return;
  state.openingDmAgentIds.add(agent.agent_id);
  renderAgents();
  hidePeopleTooltip();
  try {
    let chat = state.chats.find((item) => isDirectChatWith(item, agent.agent_id));
    if (!chat) {
      chat = await api("/api/chats", {
        method: "POST",
        body: JSON.stringify({
          title: agent.title,
          member_ids: [agent.agent_id],
          kind: "direct",
        }),
      });
    }
    await openChat(chat.chat_id);
  } catch (error) {
    console.error(error);
    alert(`Unable to open direct message: ${error.message}`);
  } finally {
    state.openingDmAgentIds.delete(agent.agent_id);
    renderAgents();
  }
}

async function loadRunnerOptions(force = false) {
  if (!force && state.runnerOptions && Date.now() - state.runnerOptionsAt < 60000) return state.runnerOptions;
  state.runnerOptions = await api("/api/runners");
  state.runnerOptionsAt = Date.now();
  return state.runnerOptions;
}

function fillRunnerSelect(select, modelSelect, selectedRunner, selectedModel) {
  const runners = state.runnerOptions?.runners || [];
  select.replaceChildren();
  runners.forEach((runner) => {
    const option = document.createElement("option");
    option.value = runner.id;
    option.textContent = `${runner.label}${runner.available ? "" : " (not installed)"}`;
    option.disabled = !runner.available;
    option.selected = runner.id === (selectedRunner || state.runnerOptions.default_runner);
    select.appendChild(option);
  });
  const runner = runners.find((item) => item.id === select.value) || runners[0];
  modelSelect.replaceChildren();
  const defaultOption = document.createElement("option");
  defaultOption.value = "";
  defaultOption.textContent = `Default${runner?.default_model ? ` (${runner.default_model})` : ""}`;
  modelSelect.appendChild(defaultOption);
  (runner?.models || []).forEach((model) => {
    const option = document.createElement("option");
    option.value = model.id;
    option.textContent = model.label;
    option.title = model.description || model.id;
    modelSelect.appendChild(option);
  });
  modelSelect.value = selectedModel || "";
}

async function renderChat(chat) {
  const messages = $("messages");
  const followLatest = messages.scrollHeight - messages.scrollTop - messages.clientHeight < 80;
  const previousScrollTop = messages.scrollTop;
  setChatTitle(chat.title, true);
  state.currentChatSettings = chat;
  if (!state.hostSessionId) {
    const locked = Boolean(chat.settings_locked || state.runInProgress);
    $("chatRunnerSettings").hidden = locked;
    const runnerInfo = state.runnerOptions?.runners?.find((item) => item.id === (chat.runner || state.runnerOptions.default_runner));
    if (locked) $("chatMembers").textContent = `${runnerInfo?.label || chat.runner || "Default"} · ${chat.model || "Default"}`;
    else $("chatMembers").textContent = chat.member_ids.join(", ");
    if (!locked && state.runnerOptions) fillRunnerSelect($("chatRunnerSelect"), $("chatModelSelect"), chat.runner, chat.model);
  }
  state.currentChatUpdatedAt = chat.updated_at || null;
  state.streamingRows.forEach(clearManagedProgress);
  state.streamingRows.clear();
  messages.innerHTML = "";
  let previousAuthor = null;
  chatSync.visibleMessages(chat.messages).forEach((message) => {
    const row = document.createElement("article");
    const author = message.author_label || message.author_id;
    const showAuthor = !["user", "system"].includes(message.author_type) && author !== previousAuthor;
    row.className = `message ${message.author_type}${showAuthor ? " show-author" : ""}`;
    row.innerHTML = `
      <div class="message-avatar">${initials(message.author_label || message.author_id)}</div>
      <div class="message-body">
        <header>
          <strong>${escapeHtml(message.author_label || message.author_id)}</strong>
          <time>${escapeHtml(formatTime(message.created_at))}</time>
        </header>
        <div class="message-content">${renderMarkdown(message.body || "")}</div>
      </div>
    `;
    appendLocalFilePreviews(row.querySelector(".message-content"));
    appendMessageAttachments(row.querySelector(".message-body"), message);
    appendChoiceInteraction(row.querySelector(".message-body"), message);
    const uiSpec = message.metadata?.agent_ui?.content?.ui;
    if (uiSpec && state.generativeUiEnabled) generativeUI.append(row.querySelector(".message-content"), uiSpec, {
      onAction: (action, args, nodeId, control) => requestAgentUIAction(chat.chat_id, message.message_id, action, args, nodeId, control),
      isActionPending: (_action, nodeId) => state.pendingUIActionKeys.has(uiActionKey(chat.chat_id, message.message_id, nodeId)),
      getActionStatus: (action, nodeId) => actionStatusForMessage(chat, message.message_id, nodeId, action?.type),
    });
    messages.appendChild(row);
    previousAuthor = author;
  });
  messages.scrollTop = followLatest ? messages.scrollHeight : previousScrollTop;
}

async function requestAgentUIAction(chatId, messageId, action, args, nodeId, control) {
  const actionKey = uiActionKey(chatId, messageId, nodeId);
  if (state.pendingUIActionKeys.has(actionKey)) return;
  state.pendingUIActionKeys.add(actionKey);
  const status = document.createElement("span");
  status.className = "agent-ui-action-status";
  status.setAttribute("role", "status");
  status.textContent = "Submitting…";
  control?.parentElement?.append(status);
  const controls = control?.querySelectorAll ? [control, ...control.querySelectorAll("button, input, select")]
    : control ? [control] : [];
  controls.forEach((item) => { item.disabled = true; });
  try {
    await streamApi(`/api/chats/${encodeURIComponent(chatId)}/ui-actions`, {
      message_id: messageId,
      actionId: action.type,
      node_id: nodeId,
      args: args || {},
      idempotency_key: globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`,
    }, async (event) => {
      if (event.type === "ui.action.completed" && event.status === "error") {
        throw new Error(event.message || "The action could not be completed");
      }
      await renderStreamEvent(event, chatId);
    }, { "X-Agent-Slack-UI-Action": "1" });
    status.textContent = "Completed";
    state.pendingUIActionKeys.delete(actionKey);
    if (state.currentChatId === chatId) await openChat(chatId);
  } catch (error) {
    state.pendingUIActionKeys.delete(actionKey);
    status.textContent = error.message || "Action failed";
    controls.forEach((item) => { item.disabled = false; });
  }
}

function uiActionKey(chatId, messageId, nodeId) {
  return `${chatId}:${messageId}:${nodeId}`;
}

function actionStatusForMessage(chat, messageId, nodeId, actionId) {
  const records = Object.values(chat.ui_action_requests || {})
    .filter((record) => record.message_id === messageId && record.node_id === nodeId && record.action_id === actionId)
    .sort((left, right) => String(right.created_at || "").localeCompare(String(left.created_at || "")));
  return records[0]?.status || null;
}

function attachmentUrl(attachment) {
  return attachment.url || `/api/uploads/${encodeURIComponent(attachment.attachment_id)}`;
}

function openLightbox(kind, url, label) {
  const overlay = document.createElement("div");
  overlay.className = "lightbox-overlay";
  overlay.addEventListener("click", (event) => {
    if (event.target === overlay) overlay.remove();
  });
  const media = kind === "video"
    ? Object.assign(document.createElement("video"), { src: url, controls: true, autoplay: true })
    : Object.assign(document.createElement("img"), { src: url, alt: label || "" });
  media.className = "lightbox-media";
  overlay.appendChild(media);
  document.body.appendChild(overlay);
}

async function appendTextPreview(container, attachment, url) {
  if (attachment.size > 200 * 1024) {
    appendGenericFilePreview(container, attachment, url, "Text file is larger than the inline preview limit");
    return;
  }
  try {
    const response = await fetch(url);
    if (!response.ok) {
      appendGenericFilePreview(container, attachment, url, "Inline preview unavailable");
      return;
    }
    const text = await response.text();
    const pre = document.createElement("pre");
    pre.className = "attachment-text-preview";
    pre.textContent = text.slice(0, 4000);
    container.appendChild(pre);
  } catch (_error) {
    appendGenericFilePreview(container, attachment, url, "Inline preview unavailable");
  }
}

function appendGenericFilePreview(container, attachment, url, reason = "Preview is not supported in this browser") {
  const fallback = document.createElement("div");
  fallback.className = "attachment-preview-fallback";
  const label = document.createElement("span");
  label.textContent = `${reason} — ${attachment.filename || "file"}`;
  const link = document.createElement("a");
  link.href = url;
  link.target = "_blank";
  link.rel = "noopener";
  link.download = attachment.filename || "";
  link.textContent = "Open / download";
  fallback.append(label, link);
  container.appendChild(fallback);
}

const EXTENSION_CONTENT_TYPES = {
  png: "image/png", jpg: "image/jpeg", jpeg: "image/jpeg", gif: "image/gif", webp: "image/webp", svg: "image/svg+xml",
  mp4: "video/mp4", webm: "video/webm", mov: "video/quicktime",
  mp3: "audio/mpeg", wav: "audio/wav", m4a: "audio/mp4", ogg: "audio/ogg",
  pdf: "application/pdf",
  txt: "text/plain", md: "text/markdown", json: "application/json", csv: "text/csv", log: "text/plain",
  yml: "text/yaml", yaml: "text/yaml", py: "text/x-python", js: "text/javascript", ts: "text/typescript",
  glb: "model/gltf-binary", gltf: "model/gltf+json", blend: "model/blender", obj: "model/obj",
  usdz: "model/vnd.usdz+zip", stl: "model/stl", fbx: "model/vnd.autodesk.fbx",
  zip: "application/zip", tar: "application/x-tar", gz: "application/gzip",
  doc: "application/msword", docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  xls: "application/vnd.ms-excel", xlsx: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
};

function guessContentType(filename) {
  const extension = String(filename || "").split(".").pop()?.toLowerCase() || "";
  return EXTENSION_CONTENT_TYPES[extension] || null;
}

function appendSourceReferences(container, referenceBubbles) {
  if (!referenceBubbles.childElementCount) return;
  const toggle = document.createElement("button");
  toggle.type = "button";
  toggle.className = "message-source-toggle";
  toggle.textContent = `Show sources (${referenceBubbles.childElementCount})`;
  toggle.setAttribute("aria-expanded", "false");
  toggle.addEventListener("click", () => {
    const showing = !referenceBubbles.hidden;
    referenceBubbles.hidden = showing;
    toggle.textContent = `${showing ? "Show" : "Hide"} sources (${referenceBubbles.childElementCount})`;
    toggle.setAttribute("aria-expanded", String(!showing));
  });
  referenceBubbles.hidden = true;
  container.appendChild(toggle);
  container.appendChild(referenceBubbles);
}

function appendLocalFilePreviews(container) {
  const previewCards = document.createElement("div");
  previewCards.className = "message-inline-previews";
  const referenceBubbles = document.createElement("div");
  referenceBubbles.className = "message-reference-bubbles";
  container.querySelectorAll('a[href^="/api/v1/files?path="], a[href^="/api/files?path="]').forEach((anchor) => {
    const url = new URL(anchor.getAttribute("href"), window.location.origin);
    const rawPath = decodeURIComponent(url.searchParams.get("path") || "");
    const filename = rawPath.replace(/:\d+(:\d+)?$/, "").split("/").pop() || "file";
    const contentType = guessContentType(filename);
    const listItem = anchor.closest("li");
    if (listItem && listItem.textContent.trim() === anchor.textContent.trim()) listItem.remove();
    else anchor.remove();
    const supportsInlinePreview = contentType && (
      contentType.startsWith("model/")
      || contentType.startsWith("image/")
      || contentType.startsWith("video/")
      || contentType.startsWith("audio/")
      || contentType === "application/pdf"
    );
    if (supportsInlinePreview) {
      const card = document.createElement("div");
      card.className = "message-attachment-card";
      const chip = document.createElement("a");
      chip.className = "message-attachment";
      chip.href = url.toString();
      chip.target = "_blank";
      chip.rel = "noopener";
      chip.title = rawPath;
      chip.textContent = `📎 ${filename}`;
      card.appendChild(chip);
      appendPreviewMedia(card, { filename, content_type: contentType, size: 0 }, url.toString());
      previewCards.appendChild(card);
    } else {
      const bubble = document.createElement("a");
      bubble.className = "message-reference-bubble";
      bubble.href = url.toString();
      bubble.target = "_blank";
      bubble.rel = "noopener";
      bubble.title = rawPath;
      bubble.textContent = `📎 ${filename}`;
      referenceBubbles.appendChild(bubble);
    }
  });
  if (previewCards.childElementCount) container.appendChild(previewCards);
  appendSourceReferences(container, referenceBubbles);
}

let modelViewerLoadPromise = null;
function ensureModelViewerLoaded() {
  if (customElements.get("model-viewer")) return Promise.resolve(true);
  if (!modelViewerLoadPromise) {
    modelViewerLoadPromise = new Promise((resolve) => {
      const script = document.createElement("script");
      script.type = "module";
      script.src = "https://unpkg.com/@google/model-viewer/dist/model-viewer.min.js";
      script.onload = () => resolve(true);
      script.onerror = () => resolve(false);
      document.head.appendChild(script);
    });
  }
  return modelViewerLoadPromise;
}

let objViewerLoadPromise = null;
function ensureObjViewerLoaded() {
  if (!objViewerLoadPromise) {
    objViewerLoadPromise = Promise.all([
      import("https://esm.sh/three@0.180.0"),
      import("https://esm.sh/three@0.180.0/examples/jsm/loaders/OBJLoader.js"),
      import("https://esm.sh/three@0.180.0/examples/jsm/controls/OrbitControls.js"),
    ]).then(([THREE, loader, controls]) => ({
      THREE, OBJLoader: loader.OBJLoader, OrbitControls: controls.OrbitControls,
    }))
      .catch(() => null);
  }
  return objViewerLoadPromise;
}

function siblingObjUrl(url) {
  try {
    const resolved = new URL(url, window.location.origin);
    const rawPath = resolved.searchParams.get("path") || "";
    if (!/\.blend$/i.test(rawPath)) return null;
    resolved.searchParams.set("path", rawPath.replace(/\.blend$/i, ".obj"));
    return resolved.toString();
  } catch (_) {
    return null;
  }
}

async function appendObjPreview(card, attachment, url) {
  const objUrl = attachment.content_type === "model/obj" ? url : siblingObjUrl(url);
  if (!objUrl) {
    appendGenericFilePreview(card, attachment, url, "This 3D format has no browser renderer");
    return;
  }
  const placeholder = document.createElement("div");
  placeholder.className = "attachment-preview-model attachment-preview-model-loading";
  placeholder.textContent = "Loading 3D preview…";
  card.appendChild(placeholder);
  const modules = await ensureObjViewerLoaded();
  if (!modules) {
    placeholder.textContent = "3D preview needs internet access — ";
    const link = document.createElement("a");
    link.href = objUrl;
    link.target = "_blank";
    link.rel = "noopener";
    link.textContent = "open the OBJ export";
    placeholder.appendChild(link);
    return;
  }
  const { THREE, OBJLoader, OrbitControls } = modules;
  const canvas = document.createElement("canvas");
  canvas.className = "attachment-preview-model attachment-preview-obj";
  card.replaceChild(canvas, placeholder);
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.setClearColor(0x000000, 0);
  const scene = new THREE.Scene();
  scene.add(new THREE.HemisphereLight(0xffffff, 0x334155, 2.2));
  const key = new THREE.DirectionalLight(0xffffff, 2.5);
  key.position.set(3, 5, 4);
  scene.add(key);
  const camera = new THREE.PerspectiveCamera(35, 1, 0.01, 1000);
  const group = new THREE.Group();
  scene.add(group);
  try {
    const object = await new OBJLoader().loadAsync(objUrl);
    group.add(object);
    const bounds = new THREE.Box3().setFromObject(object);
    const center = bounds.getCenter(new THREE.Vector3());
    const size = bounds.getSize(new THREE.Vector3());
    object.position.sub(center);
    const radius = Math.max(size.x, size.y, size.z, 0.1);
    camera.position.set(radius * 1.8, radius * 0.9, radius * 2.4);
    const orbit = new OrbitControls(camera, canvas);
    orbit.target.set(0, 0, 0);
    orbit.enableDamping = true;
    orbit.enablePan = true;
    orbit.minDistance = radius * 0.35;
    orbit.maxDistance = radius * 8;
    orbit.autoRotate = true;
    orbit.autoRotateSpeed = 1.2;
    orbit.update();
    const resize = () => {
      const width = Math.max(card.clientWidth - 24, 240);
      const height = 320;
      renderer.setSize(width, height, false);
      camera.aspect = width / height;
      camera.updateProjectionMatrix();
    };
    resize();
    const observer = new ResizeObserver(resize);
    observer.observe(card);
    const animate = () => {
      if (!canvas.isConnected) {
        observer.disconnect();
        orbit.dispose();
        renderer.dispose();
        return;
      }
      orbit.update();
      renderer.render(scene, camera);
      requestAnimationFrame(animate);
    };
    animate();
  } catch (_) {
    canvas.remove();
    const error = document.createElement("div");
    error.className = "attachment-preview-model attachment-preview-model-loading";
    error.textContent = "OBJ preview unavailable — ";
    const link = document.createElement("a");
    link.href = objUrl;
    link.target = "_blank";
    link.rel = "noopener";
    link.textContent = "open the OBJ export";
    error.appendChild(link);
    card.appendChild(error);
  }
}

function resolveContentType(attachment) {
  // Browsers/OSes often don't know newer extensions (.glb, .gltf, ...) and
  // report a generic type, or none at all, when a file is uploaded/dropped -
  // fall back to our own extension map rather than trusting a blank/generic
  // stored content_type.
  const stored = attachment.content_type || "";
  if (stored && stored !== "application/octet-stream") return stored;
  return guessContentType(attachment.filename) || stored || "application/octet-stream";
}

function appendPreviewMedia(card, attachment, url) {
  const contentType = resolveContentType(attachment);
  if (contentType === "model/blender" || contentType === "model/obj") {
    void appendObjPreview(card, attachment, url);
    return;
  }
  if (contentType.startsWith("model/gltf")) {
    const placeholder = document.createElement("div");
    placeholder.className = "attachment-preview-model attachment-preview-model-loading";
    placeholder.textContent = "Loading 3D preview…";
    card.appendChild(placeholder);
    void ensureModelViewerLoaded().then((loaded) => {
      if (!loaded) {
        placeholder.textContent = "3D preview needs internet access — ";
        const link = document.createElement("a");
        link.href = url;
        link.target = "_blank";
        link.rel = "noopener";
        link.textContent = "open the model file";
        placeholder.appendChild(link);
        return;
      }
      const viewer = document.createElement("model-viewer");
      viewer.className = "attachment-preview-model";
      viewer.setAttribute("src", url);
      viewer.setAttribute("camera-controls", "");
      viewer.setAttribute("auto-rotate", "");
      viewer.setAttribute("alt", attachment.filename);
      card.replaceChild(viewer, placeholder);
    });
    return;
  }
  if (contentType.startsWith("image/")) {
    const img = document.createElement("img");
    img.className = "attachment-preview-media attachment-preview-image";
    img.src = url;
    img.alt = attachment.filename;
    img.loading = "lazy";
    img.addEventListener("click", () => openLightbox("image", url, attachment.filename));
    card.appendChild(img);
  } else if (contentType.startsWith("video/")) {
    const video = document.createElement("video");
    video.className = "attachment-preview-media";
    video.src = url;
    video.controls = true;
    video.preload = "metadata";
    card.appendChild(video);
  } else if (contentType.startsWith("audio/")) {
    const audio = document.createElement("audio");
    audio.className = "attachment-preview-audio";
    audio.src = url;
    audio.controls = true;
    audio.preload = "metadata";
    card.appendChild(audio);
  } else if (contentType === "application/pdf") {
    const embed = document.createElement("iframe");
    embed.className = "attachment-preview-pdf";
    embed.src = url;
    card.appendChild(embed);
  } else if (contentType.startsWith("text/") || contentType === "application/json") {
    // "reference" attachments (docs/manifests an agent wrote to explain its own
    // output, not the output itself) stay a plain link - no inline content dump.
    if (attachment.role === "reference") {
      const note = document.createElement("span");
      note.className = "attachment-reference-note";
      note.textContent = "Source reference — click the file above to view";
      card.appendChild(note);
    } else {
      void appendTextPreview(card, attachment, url);
    }
  } else {
    // Never leave an attachment blank: proprietary, archive, document, and
    // other binary types still get a safe project-scoped open/download path.
    appendGenericFilePreview(card, attachment, url);
  }
}

function appendMessageAttachments(container, message) {
  const attachments = message.metadata?.attachments || [];
  if (!attachments.length) return;
  const list = document.createElement("div");
  list.className = "message-attachments";
  const referenceBubbles = document.createElement("div");
  referenceBubbles.className = "message-reference-bubbles";
  attachments.forEach((attachment) => {
    const url = attachmentUrl(attachment);
    if (attachment.role === "reference") {
      const bubble = document.createElement("a");
      bubble.className = "message-reference-bubble";
      bubble.href = url;
      bubble.target = "_blank";
      bubble.rel = "noopener";
      bubble.title = attachment.filename || "Source reference";
      bubble.textContent = `📎 ${attachment.filename || "source"}`;
      referenceBubbles.appendChild(bubble);
      return;
    }
    const card = document.createElement("div");
    card.className = "message-attachment-card";

    const chip = document.createElement("a");
    chip.className = "message-attachment";
    chip.href = url;
    chip.target = "_blank";
    chip.rel = "noopener";
    chip.textContent = `📎 ${attachment.filename} · ${formatBytes(attachment.size)}`;
    chip.title = resolveContentType(attachment) || "File attachment";
    card.appendChild(chip);

    appendPreviewMedia(card, attachment, url);

    list.appendChild(card);
  });
  if (list.childElementCount) container.appendChild(list);
  appendSourceReferences(container, referenceBubbles);
}

const VOICE_STATES = ["listening", "speaking", "thinking", "responding", "error"];

function setVoiceStatus(text, visible = true, voiceState_ = null) {
  const status = $("voiceStatus");
  const label = $("voiceStatusText");
  if (label) label.textContent = text;
  else status.textContent = text;
  status.hidden = !visible;
  const btn = $("voiceBtn");
  for (const name of VOICE_STATES) {
    status.classList.toggle(`is-${name}`, name === voiceState_);
    btn.classList.toggle(`is-${name}`, name === voiceState_);
  }
}

async function toggleVoice() {
  if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
    setVoiceStatus("Backend voice capture is unavailable in this browser.", true, "error");
    return;
  }
  if (voiceState.active) {
    stopContinuousVoice();
    return;
  }
  try {
    voiceState.stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
    voiceState.audioContext = new AudioContext();
    voiceState.analyser = voiceState.audioContext.createAnalyser();
    voiceState.analyser.fftSize = 1024;
    voiceState.audioContext.createMediaStreamSource(voiceState.stream).connect(voiceState.analyser);
    voiceState.active = true;
    voiceState.speaking = false;
    voiceState.discardNextRecording = false;
    voiceState.monitorFrame = requestAnimationFrame(monitorVoiceLevel);
    $("voiceBtn").textContent = "⏹";
    setVoiceStatus("Listening — speak naturally, I'll respond out loud", true, "listening");
  } catch (error) {
    setVoiceStatus("Microphone error: " + error.message, true, "error");
  }
}

function monitorVoiceLevel() {
  if (!voiceState.active || !voiceState.analyser) return;
  const samples = new Uint8Array(voiceState.analyser.fftSize);
  voiceState.analyser.getByteTimeDomainData(samples);
  const rms = Math.sqrt(samples.reduce((sum, sample) => {
    const normalized = (sample - 128) / 128;
    return sum + normalized * normalized;
  }, 0) / samples.length);
  const speakingNow = rms > 0.028;
  const now = performance.now();
  if (speakingNow) {
    voiceState.silenceSince = 0;
    if (!voiceState.speaking) {
      voiceState.speaking = true;
      voiceState.chunks = [];
      voiceState.discardNextRecording = false;
      if (voiceState.audio && !voiceState.audio.paused) {
        voiceState.audio.pause();
        voiceState.audio.currentTime = 0;
      }
      voiceState.recorder = new MediaRecorder(voiceState.stream);
      voiceState.recorder.ondataavailable = (event) => {
        if (event.data.size) voiceState.chunks.push(event.data);
      };
      voiceState.recorder.onstop = () => {
        const blob = new Blob(voiceState.chunks, { type: voiceState.recorder?.mimeType || "audio/webm" });
        voiceState.recorder = null;
        voiceState.chunks = [];
        if (!voiceState.discardNextRecording && blob.size) void submitVoiceBlob(blob);
      };
      voiceState.recorder.start();
      setVoiceStatus("Listening to you…", true, "speaking");
    }
  } else if (voiceState.speaking) {
    if (!voiceState.silenceSince) voiceState.silenceSince = now;
    if (now - voiceState.silenceSince >= 850) {
      voiceState.speaking = false;
      voiceState.recorder?.stop();
      setVoiceStatus("Thinking…", true, "thinking");
    }
  }
  voiceState.monitorFrame = requestAnimationFrame(monitorVoiceLevel);
}

function stopContinuousVoice() {
  voiceState.active = false;
  if (voiceState.monitorFrame) cancelAnimationFrame(voiceState.monitorFrame);
  voiceState.monitorFrame = null;
  voiceState.discardNextRecording = true;
  voiceState.recorder?.stop();
  voiceState.recorder = null;
  voiceState.speaking = false;
  voiceState.stream?.getTracks().forEach((track) => track.stop());
  voiceState.stream = null;
  voiceState.audioContext?.close();
  voiceState.audioContext = null;
  voiceState.analyser = null;
  voiceState.audio?.pause();
  $("voiceBtn").textContent = "🎤";
  setVoiceStatus("", false, null);
  if (voiceState.voiceChatId) void openChat(voiceState.voiceChatId);
}

async function submitVoiceBlob(blob) {
  if (voiceState.processing) {
    voiceState.queuedBlob = blob;
    return;
  }
  voiceState.processing = true;
  setVoiceStatus("Thinking…", true, "thinking");
  try {
    if (!voiceState.voiceChatId || !voiceState.sourceChatId) {
      if (!state.currentChatId) throw new Error("Select a chat before starting voice");
      const session = await api("/api/v1/voice/sessions", {
        method: "POST",
        body: JSON.stringify({ source_chat_id: state.currentChatId }),
      });
      voiceState.voiceChatId = session.chat_id;
      voiceState.sourceChatId = state.currentChatId;
    }
    const requestId = globalThis.crypto?.randomUUID?.() || `voice-${Date.now()}-${Math.random()}`;
    const response = await fetch("/api/v1/voice/turns", {
      method: "POST",
      headers: {
        "Content-Type": blob.type || "audio/webm",
        "X-Agent-Slack-Filename": "voice.webm",
        "X-Agent-Slack-Voice-Chat": voiceState.voiceChatId,
        "Idempotency-Key": requestId,
        ...(state.activeServerId ? { "X-Agent-Slack-Server": state.activeServerId } : {}),
      },
      body: blob,
    });
    if (!response.ok) throw new Error(await response.text() || "Backend voice turn failed");
    const events = (await response.text()).split("\n").filter(Boolean).map((line) => JSON.parse(line));
    for (const event of events) {
      if (event.type === "voice_transcript") setVoiceStatus("Thinking…", true, "thinking");
      if (event.type === "speech_ready" && event.url) {
        voiceState.audio?.pause();
        voiceState.audio = new Audio(new URL(event.url, window.location.origin));
        setVoiceStatus("Responding…", true, "responding");
        await voiceState.audio.play();
        await new Promise((resolve) => {
          voiceState.audio.addEventListener("ended", resolve, { once: true });
          voiceState.audio.addEventListener("error", resolve, { once: true });
        });
      }
      if (event.type === "agent_failed" || event.type === "error") throw new Error(event.message || event.text || "Backend voice turn failed");
    }
    if (voiceState.active) setVoiceStatus("Listening — speak naturally, I'll respond out loud", true, "listening");
  } catch (error) {
    setVoiceStatus("Backend voice error: " + error.message, true, "error");
  } finally {
    voiceState.processing = false;
    if (voiceState.queuedBlob && voiceState.active) {
      const queued = voiceState.queuedBlob;
      voiceState.queuedBlob = null;
      void submitVoiceBlob(queued);
    }
  }
}

function appendChoiceInteraction(container, message) {
  const choice = interactionHelpers.choiceForMessage(message);
  if (!choice) return;
  const selected = new Set(choice.selectedOptionIds);
  const card = document.createElement("section");
  card.className = `choice-card ${choice.status}`;
  card.setAttribute("aria-label", choice.prompt);

  const prompt = document.createElement("strong");
  prompt.className = "choice-prompt";
  prompt.textContent = choice.prompt;
  card.appendChild(prompt);

  const options = document.createElement("div");
  options.className = "choice-options";
  card.appendChild(options);

  const submit = document.createElement("button");
  submit.type = "button";
  submit.className = "choice-submit";
  submit.textContent = "Submit selection";

  const setBusy = (busy) => {
    card.querySelectorAll("button").forEach((button) => {
      button.disabled = busy || choice.status === "answered";
    });
    if (busy) submit.textContent = "Sending…";
  };

  const update = () => {
    options.querySelectorAll("[data-choice-option]").forEach((button) => {
      const isSelected = selected.has(button.dataset.choiceOption);
      button.classList.toggle("selected", isSelected);
      button.setAttribute("aria-pressed", String(isSelected));
    });
    submit.disabled = selected.size === 0 || state.answeringQuestionIds.has(choice.questionId);
  };

  choice.options.forEach((option) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "choice-option";
    button.dataset.choiceOption = option.id;
    const label = document.createElement("strong");
    label.textContent = option.label;
    button.appendChild(label);
    if (option.description) {
      const description = document.createElement("span");
      description.textContent = option.description;
      button.appendChild(description);
    }
    button.disabled = choice.status === "answered";
    button.onclick = async () => {
      const next = interactionHelpers.nextSelection(choice, [...selected], option.id);
      selected.clear();
      next.forEach((id) => selected.add(id));
      update();
      if (!choice.multiple) {
        setBusy(true);
        await answerAgentQuestion(choice, [...selected]);
      }
    };
    options.appendChild(button);
  });

  if (choice.multiple && choice.status === "pending") {
    submit.onclick = async () => {
      setBusy(true);
      await answerAgentQuestion(choice, [...selected]);
    };
    card.appendChild(submit);
  } else if (choice.status === "answered") {
    const status = document.createElement("small");
    status.className = "choice-status";
    status.textContent = "Answered";
    card.appendChild(status);
  }
  update();
  container.appendChild(card);
}

function createStreamingRow(taskId, agentId, agentLabel) {
  if (state.streamingRows.has(taskId)) return state.streamingRows.get(taskId);
  const row = document.createElement("article");
  row.className = "message agent streaming";
  row.innerHTML = `
    <div class="message-avatar">${initials(agentLabel)}</div>
    <div class="message-body">
      <header>
        <strong>${escapeHtml(agentLabel)}</strong>
        <time>working now</time>
        <button class="agent-stop-button" type="button">Stop</button>
      </header>
      <div class="message-content"></div>
    </div>
  `;
  $("messages").appendChild(row);
  $("messages").scrollTop = $("messages").scrollHeight;
  const stopButton = row.querySelector(".agent-stop-button");
  stopButton.onclick = () => stopAgentTask(taskId);
  const body = row.querySelector(".message-content");
  const progressPanel = document.createElement("details");
  progressPanel.className = "host-progress managed-progress";
  progressPanel.open = true;
  const elapsed = document.createElement("span");
  elapsed.className = "managed-progress-elapsed";
  elapsed.textContent = "0s";
  progressPanel.appendChild(createProgressHeader(elapsed));
  const progressList = document.createElement("ul");
  progressList.className = "progress-steps";
  progressPanel.appendChild(progressList);
  const messageBody = row.querySelector(".message-body");
  messageBody.insertBefore(progressPanel, body);
  const startedAt = Date.now();
  const elapsedTimer = setInterval(() => {
    elapsed.textContent = `${Math.floor((Date.now() - startedAt) / 1000)}s`;
  }, 1000);
  state.streamingRows.set(taskId, {
    row,
    body,
    progressPanel,
    progressList,
    progressSteps: new Map(),
    elapsedTimer,
    stopButton,
    agentId,
    rawText: "",
    started: false,
    messageId: null,
    uiStarted: false,
    uiSequence: 0,
    uiSpec: null,
    uiLoading: null,
  });
  return state.streamingRows.get(taskId);
}

function clearManagedProgress(stream) {
  if (!stream) return;
  if (stream.elapsedTimer) clearInterval(stream.elapsedTimer);
  stream.elapsedTimer = null;
  stream.progressPanel?.remove();
  stream.progressPanel = null;
}

function renderManagedProgress(stream) {
  if (!stream?.progressList) return;
  const steps = progressActions([...stream.progressSteps.values()]);
  const visible = stream.progressExpanded ? steps : steps.slice(-4);
  stream.progressList.replaceChildren(...visible.map(createHostProgressStep));
  stream.progressShowAll?.remove();
  stream.progressShowAll = null;
  if (!stream.progressExpanded && steps.length > visible.length) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "progress-more";
    button.textContent = `Show all ${steps.length} steps`;
    button.onclick = () => {
      stream.progressExpanded = true;
      renderManagedProgress(stream);
    };
    stream.progressPanel.appendChild(button);
    stream.progressShowAll = button;
  }
}

function applyActiveRun(run, chatId = state.currentChatId) {
  if (!run) {
    if (state.runInProgress && state.currentRunChatId && state.currentRunChatId !== chatId) return;
    state.streamingRows.forEach((stream) => {
      clearManagedProgress(stream);
      stream.row.remove();
    });
    state.streamingRows.clear();
    state.runInProgress = false;
    state.currentRunId = null;
    state.currentRunChatId = null;
    $("stopRunBtn").hidden = true;
    $("deleteChatBtn").disabled = !state.currentChatId;
    $("sendBtn").disabled = false;
    $("sendBtn").textContent = "Send";
    return;
  }

  state.runInProgress = true;
  if (chatId === state.currentChatId) { $("chatRunnerSettings").hidden = true; $("chatRunnerSelect").disabled = true; $("chatModelSelect").disabled = true; }
  if (chatId === state.currentChatId && state.currentChatSettings) {
    const selectedRunner = state.runnerOptions?.runners?.find((item) => item.id === (state.currentChatSettings.runner || state.runnerOptions.default_runner));
    $("chatMembers").textContent = `${selectedRunner?.label || state.currentChatSettings.runner || "Default"} · ${state.currentChatSettings.model || "Default"}`;
  }
  state.currentRunId = run.run_id;
  state.currentRunChatId = chatId;
  $("stopRunBtn").hidden = false;
  $("stopRunBtn").disabled = false;
  $("stopRunBtn").textContent = "Stop Run";
  $("deleteChatBtn").disabled = true;
  $("sendBtn").disabled = true;
  $("sendBtn").textContent = "Working...";

  const runningTasks = (run.tasks || []).filter((task) => task.status === "running");
  const runningTaskIds = new Set(runningTasks.map((task) => task.task_id));
  state.streamingRows.forEach((stream, taskId) => {
    if (!runningTaskIds.has(taskId)) {
      clearManagedProgress(stream);
      stream.row.remove();
      state.streamingRows.delete(taskId);
    }
  });
  runningTasks.forEach((task) => {
    const stream = createStreamingRow(task.task_id, task.agent_id, task.agent_label || task.agent_id);
    stream.row.querySelector("time").textContent = "Working…";
    stream.rawText = task.text || "";
    stream.started = Boolean(stream.rawText);
    const uiFence = stream.rawText.search(/```agent-ui(?:\s*\n|$)/i);
    stream.body.innerHTML = renderMarkdown(uiFence >= 0 ? stream.rawText.slice(0, uiFence) : stream.rawText);
    if (uiFence >= 0) {
      stream.uiStarted = true;
      if (!stream.uiLoading) {
        stream.uiLoading = document.createElement("div");
        stream.uiLoading.className = "agent-ui-loading";
        stream.uiLoading.setAttribute("role", "status");
        stream.uiLoading.textContent = "Preparing interactive view…";
      }
      stream.body.append(stream.uiLoading);
    }
  });
}

async function restoreActiveRun(chatId) {
  if (!chatId) return;
  const payload = await api(`/api/runs/active?chat_id=${encodeURIComponent(chatId)}`);
  if (chatId !== state.currentChatId) return;
  applyActiveRun(chatSync.activeRunForChat(payload.runs, chatId), chatId);
}

async function renderStreamEvent(event, streamChatId = state.currentChatId) {
  // The run continues in the background when the user navigates to another
  // chat/server. Persisted state will rebuild its rows when they return.
  if (streamChatId !== state.currentChatId) return;
  const taskId = event.task_id || event.agent_id || event.taskId || event.agentId;
  if (event.type === "run_started") {
    state.currentRunId = event.run_id;
    $("stopRunBtn").hidden = false;
    $("stopRunBtn").disabled = false;
    $("stopRunBtn").textContent = "Stop Run";
  } else if (event.type === "agent_started") {
    const stream = createStreamingRow(taskId, event.agent_id, event.agent_label);
    stream.messageId = event.message_id || null;
    stream.row.querySelector("time").textContent = "Working…";
  } else if (event.type === "agent_progress") {
    const stream = state.streamingRows.get(taskId);
    if (!stream || !stream.progressPanel) return;
    stream.progressSteps.set(event.step_id, {
      kind: event.kind,
      label: `${event.kind === "subagent" ? "Subagent: " : ""}${String(event.label || "Working")}`.slice(0, 120),
      state: event.state === "done" ? "done" : "running",
    });
    renderManagedProgress(stream);
  } else if (event.type === "ui.started" && state.generativeUiEnabled) {
    const stream = state.streamingRows.get(taskId);
    if (!stream) return;
    stream.messageId = event.messageId || stream.messageId;
    stream.uiStarted = true;
    stream.uiLoading = document.createElement("div");
    stream.uiLoading.className = "agent-ui-loading";
    stream.uiLoading.setAttribute("role", "status");
    stream.uiLoading.textContent = "Preparing interactive view…";
    stream.body.append(stream.uiLoading);
  } else if (event.type === "delta") {
    const stream = state.streamingRows.get(taskId);
    if (!stream) return;
    if (!stream.started) {
      stream.started = true;
    }
    const rendered = appendStreamMarkdown(stream.rawText, event.text);
    stream.rawText = rendered.text;
    const fenceIndex = stream.rawText.search(/```agent-ui\s*\n/i);
    stream.body.innerHTML = fenceIndex >= 0
      ? renderMarkdown(stream.rawText.slice(0, fenceIndex))
      : rendered.html;
    if (stream.uiLoading) stream.body.append(stream.uiLoading);
    $("messages").scrollTop = $("messages").scrollHeight;
    await new Promise((resolve) => setTimeout(resolve, 14));
  } else if (event.type === "ui.patch" && state.generativeUiEnabled) {
    const stream = state.streamingRows.get(taskId);
    if (!stream || Number(event.sequence) <= stream.uiSequence) return;
    stream.uiSequence = Number(event.sequence) || stream.uiSequence + 1;
    stream.messageId = event.messageId || stream.messageId;
    stream.uiSpec = event.snapshot;
    stream.uiLoading?.remove();
    stream.uiLoading = null;
    generativeUI.append(stream.body, stream.uiSpec, {
      onAction: (action, args, nodeId, control) => requestAgentUIAction(
        streamChatId, stream.messageId, action, args, nodeId, control,
      ),
      isActionPending: (_action, nodeId) => state.pendingUIActionKeys.has(
        uiActionKey(streamChatId, stream.messageId, nodeId),
      ),
    });
  } else if (event.type === "ui.completed") {
    const stream = state.streamingRows.get(taskId);
    if (stream && Number(event.sequence) >= stream.uiSequence) {
      stream.uiSequence = Number(event.sequence) || stream.uiSequence;
      stream.uiLoading?.remove();
      stream.uiLoading = null;
    }
  } else if (event.type === "ui.error") {
    const stream = state.streamingRows.get(taskId);
    if (stream) {
      stream.uiLoading?.remove();
      stream.uiLoading = null;
      const status = document.createElement("div");
      status.className = "agent-ui-error";
      status.setAttribute("role", "status");
      status.textContent = event.message || "Interactive view unavailable.";
      stream.body.append(status);
    }
  } else if (event.type === "agent_completed") {
    const stream = state.streamingRows.get(taskId);
    if (stream) {
      clearManagedProgress(stream);
      stream.body.innerHTML = renderMarkdown(event.body !== undefined ? event.body : stream.rawText);
      stream.uiLoading?.remove();
      const finalUi = event.ui || stream.uiSpec;
      if (finalUi && state.generativeUiEnabled) generativeUI.append(stream.body, finalUi, {
        onAction: (action, args, nodeId, control) => requestAgentUIAction(
          streamChatId, event.message_id || stream.messageId, action, args, nodeId, control,
        ),
        isActionPending: (_action, nodeId) => state.pendingUIActionKeys.has(
          uiActionKey(streamChatId, event.message_id || stream.messageId, nodeId),
        ),
      });
      stream.row.classList.remove("streaming");
      stream.row.querySelector("time").textContent = "just now";
      stream.stopButton.remove();
      state.streamingRows.delete(taskId);
    }
  } else if (event.type === "agent_failed") {
    let stream = state.streamingRows.get(taskId);
    if (!stream) {
      createStreamingRow(taskId, event.agent_id, event.agent_label || event.agent_id);
      stream = state.streamingRows.get(taskId);
    }
    if (stream) {
      clearManagedProgress(stream);
      stream.body.innerHTML = renderMarkdown(event.message || "**Agent run failed.**");
      stream.row.classList.remove("streaming");
      stream.row.classList.add("failed");
      stream.row.querySelector("time").textContent = "failed";
      stream.stopButton.remove();
      state.streamingRows.delete(taskId);
    }
  } else if (event.type === "agent_cancelled") {
    let stream = state.streamingRows.get(taskId);
    if (!stream) {
      createStreamingRow(taskId, event.agent_id, event.agent_label || event.agent_id);
      stream = state.streamingRows.get(taskId);
    }
    if (stream) {
      clearManagedProgress(stream);
      const stopped = event.message || "Stopped by user.";
      const visibleText = visibleStreamText(stream.rawText);
      const content = visibleText ? `${visibleText}\n\n_${stopped}_` : `_${stopped}_`;
      stream.body.innerHTML = renderMarkdown(content);
      stream.row.classList.remove("streaming");
      stream.row.classList.add("cancelled");
      stream.row.querySelector("time").textContent = "stopped";
      stream.stopButton.remove();
      state.streamingRows.delete(taskId);
      state.stoppingTaskIds.delete(taskId);
    }
  } else if (event.type === "run_cancelled") {
    state.streamingRows.forEach(clearManagedProgress);
    $("stopRunBtn").disabled = true;
    $("stopRunBtn").textContent = "Stopped";
  } else if (event.type === "run_completed") {
    state.streamingRows.forEach(clearManagedProgress);
  } else if (event.type === "error") {
    state.streamingRows.forEach(clearManagedProgress);
    const error = new Error(event.message || "Agent run failed");
    error.agentRunFailure = true;
    throw error;
  }
}

function visibleStreamText(text) {
  const index = String(text || "").search(/```agent-ui\s*\n/i);
  return (index >= 0 ? String(text).slice(0, index) : String(text || "")).trimEnd();
}

function runPayloadForMessage(currentChat, body, mentionedAgentIdsOverride = null) {
  const memberIds = currentChat?.member_ids || [];
  const mentionedAgentIds = mentionedAgentIdsOverride
    ?? mentionHelpers.resolveAgentIds(body, state.agents);
  const leadAgentId = state.orchestratorIds.find((agentId) => memberIds.includes(agentId));
  if (currentChat?.kind !== "direct" && mentionedAgentIds.length > 0) {
    const targetAgentId = mentionedAgentIds[0];
    return {
      mode: "respond",
      agent_ids: [targetAgentId],
      lead_agent_id: targetAgentId,
      objective: body,
    };
  }
  if (currentChat?.kind === "direct" && memberIds.length === 1) {
    const directAgentId = memberIds[0];
    return state.orchestratorIds.includes(directAgentId)
      ? { mode: "auto_meeting", lead_agent_id: directAgentId, objective: body }
      : { mode: "respond", agent_ids: [directAgentId], objective: body };
  }
  if (!memberIds.length) return null;
  return leadAgentId
    ? { mode: "meeting", lead_agent_id: leadAgentId, participant_ids: memberIds, objective: body }
    : { mode: "respond", agent_ids: memberIds, objective: body };
}

async function answerAgentQuestion(choice, optionIds) {
  if (
    !state.currentChatId
    || state.runInProgress
    || state.answeringQuestionIds.has(choice.questionId)
    || optionIds.length === 0
  ) return;
  state.answeringQuestionIds.add(choice.questionId);
  const chatId = state.currentChatId;
  try {
    const chat = await api(
      `/api/chats/${encodeURIComponent(chatId)}/questions/${encodeURIComponent(choice.questionId)}/answer`,
      {
        method: "POST",
        body: JSON.stringify({ option_ids: optionIds }),
      },
    );
    if (state.currentChatId !== chatId) return;
    renderChat(chat);
    await loadChats();
    const currentChat = state.chats.find((item) => item.chat_id === chatId);
    const objective = interactionHelpers.answerObjective(choice, optionIds);
    const payload = runPayloadForMessage(currentChat, objective, []);
    if (payload) await runChatStream(payload, chatId);
  } catch (error) {
    console.error(error);
    alert(`Unable to answer agent question: ${error.message}`);
    if (state.currentChatId === chatId) await openChat(chatId);
  } finally {
    state.answeringQuestionIds.delete(choice.questionId);
  }
}

async function stopAgentTask(taskId) {
  if (!state.currentRunId || state.stoppingTaskIds.has(taskId)) return;
  const stream = state.streamingRows.get(taskId);
  state.stoppingTaskIds.add(taskId);
  if (stream) {
    stream.stopButton.disabled = true;
    stream.stopButton.textContent = "Stopping...";
  }
  try {
    await api(
      `/api/runs/${encodeURIComponent(state.currentRunId)}/tasks/${encodeURIComponent(taskId)}/cancel`,
      { method: "POST", body: "{}" },
    );
  } catch (error) {
    state.stoppingTaskIds.delete(taskId);
    if (stream) {
      stream.stopButton.disabled = false;
      stream.stopButton.textContent = "Stop";
    }
    alert(`Unable to stop agent: ${error.message}`);
  }
}

async function stopCurrentRun() {
  if (state.hostSessionId && state.hostResumes.has(state.hostSessionId)) {
    $("stopRunBtn").disabled = true;
    $("stopRunBtn").textContent = "Stopping...";
    try {
      await api(`/api/host-sessions/${encodeURIComponent(state.hostSessionId)}/resume/cancel`, { method: "POST", body: "{}" });
    } catch (error) {
      console.warn("Unable to stop the resume", error);
      $("stopRunBtn").disabled = false;
      $("stopRunBtn").textContent = "Stop";
    }
    return;
  }
  if (!state.currentRunId || !state.runInProgress) return;
  $("stopRunBtn").disabled = true;
  $("stopRunBtn").textContent = "Stopping...";
  try {
    await api(`/api/runs/${encodeURIComponent(state.currentRunId)}/cancel`, {
      method: "POST",
      body: "{}",
    });
  } catch (error) {
    $("stopRunBtn").disabled = false;
    $("stopRunBtn").textContent = "Stop Run";
    alert(`Unable to stop run: ${error.message}`);
  }
}

async function runChatStream(payload, chatId = state.currentChatId) {
  if (!chatId || state.runInProgress) return;
  let resultChatId = chatId;
  state.runInProgress = true;
  state.streamAttached = true;
  state.currentRunChatId = chatId;
  $("deleteChatBtn").disabled = true;
  $("sendBtn").disabled = true;
  $("sendBtn").textContent = "Working...";
  try {
    await streamApi(`/api/chats/${chatId}/run-stream`, payload, async (event) => {
      if ((event.type === "agent_completed" || event.type === "run_completed") && chatId === state.currentChatId) {
        try { renderChat(await api(`/api/chats/${encodeURIComponent(chatId)}`)); } catch (_) { /* the final reload retries */ }
      }
      if (event.type === "meeting_created") {
        resultChatId = event.chat_id;
        await loadChats();
        await openChat(resultChatId);
      } else if (event.type === "run_completed" && event.chat_id) {
        resultChatId = event.chat_id;
      }
      await renderStreamEvent(event, chatId);
    });
  } catch (error) {
    console.warn("Agent Slack stream detached", error);
    state.streamingRows.forEach(clearManagedProgress);
    alert(chatSync.streamErrorMessage(error));
  } finally {
    state.streamAttached = false;
    state.runInProgress = false;
    $("chatRunnerSelect").disabled = false;
    $("chatModelSelect").disabled = false;
    state.currentRunId = null;
    state.currentRunChatId = null;
    state.stoppingTaskIds.clear();
    state.streamingRows.forEach(clearManagedProgress);
    state.streamingRows.clear();
    $("stopRunBtn").hidden = true;
    $("deleteChatBtn").disabled = !state.currentChatId;
    $("sendBtn").disabled = false;
    $("sendBtn").textContent = "Send";
    if (state.currentChatId === chatId || state.currentChatId === resultChatId) {
      await openChat(resultChatId);
    } else {
      await loadChats();
    }
  }
}

function renderDialogs() {
  renderPeoplePicker("chat");
  renderPeoplePicker("meetingLead");
  renderPeoplePicker("meetingParticipants");
}

const peoplePickerConfig = {
  chat: { search: "chatPeopleSearch", chips: "chatPeopleChips", menu: "chatPeopleMenu", single: false },
  meetingLead: { search: "meetingLeadSearch", chips: "meetingLeadChips", menu: "meetingLeadMenu", single: true },
  meetingParticipants: {
    search: "meetingPeopleSearch",
    chips: "meetingPeopleChips",
    menu: "meetingPeopleMenu",
    single: false,
  },
};

function renderPeoplePicker(key, showMenu = false) {
  const config = peoplePickerConfig[key];
  const selected = state.peoplePickers[key];
  const search = $(config.search);
  const query = search.value.trim().toLocaleLowerCase();
  $(config.chips).innerHTML = [...selected].map((agentId) => {
    const agent = state.agents.find((item) => item.agent_id === agentId);
    if (!agent) return "";
    return `
      <span class="agent-chip">
        <span class="agent-chip-avatar">${initials(agent.title)}</span>
        ${escapeHtml(agent.title)}
        <button type="button" data-remove-agent="${escapeHtml(agentId)}" aria-label="Remove ${escapeHtml(agent.title)}">&times;</button>
      </span>
    `;
  }).join("");
  $(config.chips).querySelectorAll("[data-remove-agent]").forEach((button) => {
    button.onclick = () => {
      selected.delete(button.dataset.removeAgent);
      renderPeoplePicker(key, true);
      search.focus();
    };
  });

  const matches = state.agents.filter((agent) => {
    if (selected.has(agent.agent_id)) return false;
    const searchable = `${agent.title} ${agent.name} ${agent.summary}`.toLocaleLowerCase();
    return !query || searchable.includes(query);
  });
  const menu = $(config.menu);
  menu.innerHTML = matches.length
    ? matches.map((agent) => `
        <button type="button" data-agent-id="${escapeHtml(agent.agent_id)}">
          <span class="agent-menu-avatar">${initials(agent.title)}</span>
          <span><strong>${escapeHtml(agent.title)}</strong><small>${escapeHtml(agent.summary || agent.agent_id)}</small></span>
        </button>
      `).join("")
    : '<p class="agent-picker-empty">No matching people</p>';
  menu.querySelectorAll("[data-agent-id]").forEach((button) => {
    button.onclick = () => {
      if (config.single) selected.clear();
      selected.add(button.dataset.agentId);
      search.value = "";
      renderPeoplePicker(key, !config.single);
      if (!config.single) search.focus();
    };
  });
  menu.hidden = !showMenu || (config.single && selected.size > 0);
}

function resetPeoplePicker(key) {
  const config = peoplePickerConfig[key];
  state.peoplePickers[key].clear();
  $(config.search).value = "";
  renderPeoplePicker(key);
}

function openServerDialog() {
  state.serverDialogMode = "create";
  $("serverForm").reset();
  $("serverDialogTitle").textContent = "Add Agent System";
  $("serverSubmitBtn").textContent = "Add Server";
  $("serverPathLabel").hidden = false;
  $("serverPathPicker").hidden = false;
  $("serverPath").disabled = false;
  $("serverFormError").hidden = true;
  $("openChannelSettingsBtn").hidden = true;
  $("serverDialog").showModal();
}

function openServerSettings() {
  const server = state.servers.find((item) => item.server_id === state.activeServerId);
  if (!server) return;
  state.serverDialogMode = "edit";
  $("serverForm").reset();
  $("serverDialogTitle").textContent = "Server Settings";
  $("serverSubmitBtn").textContent = "Save Changes";
  $("serverName").value = server.name;
  $("serverPath").value = server.project_root;
  $("serverPath").disabled = true;
  $("serverRunner").value = server.runner || "claude";
  $("serverModel").value = server.model || "";
  $("serverPathLabel").hidden = true;
  $("serverPathPicker").hidden = true;
  $("serverFormError").hidden = true;
  $("openChannelSettingsBtn").hidden = false;
  updateTunnelSummary();
  updateClaudeMcpSummary();
  updateClaudeTunnelSummary();
  $("serverDialog").showModal();
}

async function openChannelSettings() {
  if (!state.activeServerId) return;
  const status = $("channelSettingsStatus");
  status.hidden = true;
  try {
    const settings = await api("/api/v1/channel-settings");
    $("slackConnectorEnabled").checked = settings.enabled;
    $("slackChannelId").value = settings.channel_id || "";
    $("channelSettingsDialog").showModal();
  } catch (error) {
    status.textContent = error.message;
    status.hidden = false;
    $("channelSettingsDialog").showModal();
  }
}

async function saveChannelSettings() {
  const status = $("channelSettingsStatus");
  status.hidden = true;
  try {
    await api("/api/v1/channel-settings", {
      method: "POST",
      body: JSON.stringify({
        enabled: $("slackConnectorEnabled").checked,
        channel_id: $("slackChannelId").value.trim(),
      }),
    });
    $("channelSettingsDialog").close();
  } catch (error) {
    status.textContent = error.message;
    status.hidden = false;
  }
}

async function updateClaudeMcpSummary() {
  if (!window.agentSlack?.claudeMcpInfo) return;
  const info = await window.agentSlack.claudeMcpInfo();
  const ready = info.installed && info.backendAvailable;
  $("claudeMcpSummary").textContent = ready
    ? `Ready · bundled backend · ${info.configPath}`
    : info.installed
      ? `Configured, but backend is unavailable · ${info.configPath}`
      : `Not installed · ${info.configPath}`;
  $("installClaudeMcpBtn").textContent = ready ? "Installed" : "Install";
  $("installClaudeMcpBtn").title = ready
    ? "Reinstall the AgentSlack MCP entry if Claude's configuration needs repair"
    : "Install the bundled AgentSlack MCP backend";
}

async function installClaudeMcp() {
  if (!window.agentSlack?.installClaudeMcp) {
    $("serverFormError").textContent = "Claude MCP installation is available in the macOS app window.";
    $("serverFormError").hidden = false;
    return;
  }
  const info = await window.agentSlack.installClaudeMcp();
  $("claudeMcpSummary").textContent = info.backendAvailable
    ? `Ready · bundled backend · ${info.configPath}`
    : `Configured, but backend is unavailable · ${info.configPath}`;
  $("installClaudeMcpBtn").textContent = info.backendAvailable ? "Installed" : "Install";
}

async function updateTunnelSummary() {
  if (!window.agentSlack?.tunnelInfo) return;
  const info = await window.agentSlack.tunnelInfo();
  const status = info.status?.state || "stopped";
  $("tunnelSummary").textContent = info.tunnelId
    ? `Tunnel ${info.tunnelId} · ${status}${info.runtimeKeyConfigured ? " · key saved" : " · key missing"}`
    : "Not configured";
}

async function updateClaudeTunnelSummary() {
  if (!window.agentSlack?.claudeTunnelInfo) return;
  const info = await window.agentSlack.claudeTunnelInfo();
  $("claudeTunnelSummary").textContent = info.url
    ? `${info.enabled ? "Enabled" : "Stopped"} · ${info.url}${info.tokenConfigured ? " · token saved" : " · token missing"}`
    : "Not configured";
}

async function openClaudeTunnelDialog() {
  const info = await window.agentSlack.claudeTunnelInfo();
  $("claudeTunnelEnabled").checked = Boolean(info.enabled);
  $("claudeTunnelDomain").value = info.domain || "";
  $("claudeTunnelToken").value = "";
  $("claudeTunnelSubdomain").value = info.subdomain || "agent-slack";
  $("claudeTunnelPath").value = info.routePath || "mcp";
  $("claudeTunnelStatus").textContent = info.filesReady
    ? `Files ready at ${info.root}. Upload data/ca.crt in Claude Console before starting.`
    : "Save & Start will prepare the local tunnel files.";
  $("claudeTunnelStatus").hidden = false;
  $("claudeTunnelDialog").showModal();
}

async function submitClaudeTunnel() {
  const info = await window.agentSlack.saveClaudeTunnel({
    enabled: $("claudeTunnelEnabled").checked,
    domain: $("claudeTunnelDomain").value,
    token: $("claudeTunnelToken").value,
    subdomain: $("claudeTunnelSubdomain").value,
    routePath: $("claudeTunnelPath").value,
  });
  $("claudeTunnelDialog").close();
  await updateClaudeTunnelSummary();
  if (info.url) {
    $("serverFormError").textContent = `Claude tunnel URL: ${info.url}. Register ${info.root}/data/ca.crt in Claude Console.`;
    $("serverFormError").hidden = false;
  }
}

async function openTunnelDialog() {
  if (!window.agentSlack?.tunnelInfo) {
    $("serverFormError").textContent = "OpenAI tunnel settings are available in the macOS app window.";
    $("serverFormError").hidden = false;
    return;
  }
  const info = await window.agentSlack.tunnelInfo();
  $("tunnelEnabled").checked = Boolean(info.enabled);
  $("tunnelId").value = info.tunnelId || "";
  $("tunnelRuntimeKey").value = "";
  $("tunnelProfile").value = info.profile || "agent-slack";
  $("tunnelServerUrl").value = info.serverUrl || "";
  $("tunnelExecutablePath").value = info.executablePath || "";
  $("tunnelStatus").textContent = info.status?.message || (info.status?.state === "running" ? "Running" : "");
  $("tunnelStatus").hidden = !$("tunnelStatus").textContent;
  $("tunnelDialog").showModal();
}

async function downloadTunnelClient() {
  if (!window.agentSlack?.downloadTunnelClient) return;
  $("tunnelStatus").textContent = "Downloading the official OpenAI tunnel-client…";
  $("tunnelStatus").hidden = false;
  const info = await window.agentSlack.downloadTunnelClient();
  $("tunnelExecutablePath").value = info.executablePath || "";
  $("tunnelStatus").textContent = info.status?.message || "Tunnel-client is ready.";
}

async function browseTunnelClient() {
  const selected = await window.agentSlack.selectTunnelClient();
  if (selected) $("tunnelExecutablePath").value = selected;
}

async function submitTunnel() {
  const result = await window.agentSlack.saveTunnel({
    enabled: $("tunnelEnabled").checked,
    tunnelId: $("tunnelId").value,
    runtimeKey: $("tunnelRuntimeKey").value,
    profile: $("tunnelProfile").value,
    executablePath: $("tunnelExecutablePath").value,
  });
  $("tunnelDialog").close();
  await updateTunnelSummary();
  if (result.status?.state === "error") {
    $("serverFormError").textContent = result.status.message;
    $("serverFormError").hidden = false;
  }
}

async function browseServerPath() {
  if (window.agentSlack?.selectFolder) {
    const selected = await window.agentSlack.selectFolder();
    if (selected) $("serverPath").value = selected;
    return;
  }
  const selected = window.prompt("Absolute path to the agent-system folder", $("serverPath").value);
  if (selected) $("serverPath").value = selected;
}

async function browseServerLogo() {
  if (window.agentSlack?.selectImage) {
    const selected = await window.agentSlack.selectImage();
    if (selected) $("serverLogoPath").value = selected;
    return;
  }
  const selected = window.prompt("Absolute path to the server logo", $("serverLogoPath").value);
  if (selected) $("serverLogoPath").value = selected;
}

async function submitServer() {
  const projectRoot = $("serverPath").value.trim();
  const name = $("serverName").value.trim();
  const logoPath = $("serverLogoPath").value.trim();
  const runtime = {
    runner: $("serverRunner").value,
    model: $("serverModel").value.trim(),
  };
  if (state.serverDialogMode === "create" && !projectRoot) return;
  try {
    if (state.serverDialogMode === "edit") {
      await api(`/api/servers/${state.activeServerId}`, {
        method: "PATCH",
        body: JSON.stringify({ name, logo_path: logoPath || undefined, ...runtime }),
      });
    } else {
      await api("/api/servers", {
        method: "POST",
        body: JSON.stringify({ project_root: projectRoot, name, logo_path: logoPath || undefined, ...runtime }),
      });
    }
    $("serverDialog").close();
    $("serverForm").reset();
    state.currentChatId = null;
    await loadServers();
    await loadActiveServer();
  } catch (error) {
    $("serverFormError").textContent = error.message;
    $("serverFormError").hidden = false;
  }
}

async function attachPathToComposer(rawPath) {
  const path = rawPath.trim();
  if (!path) return;
  try {
    const response = await fetch(`/api/v1/files?path=${encodeURIComponent(path)}`, {
      headers: state.activeServerId ? { "X-Agent-Slack-Server": state.activeServerId } : {},
    });
    if (!response.ok) throw new Error(await response.text() || `HTTP ${response.status}`);
    const blob = await response.blob();
    const filename = path.split("/").pop() || path;
    selectAttachments([new File([blob], filename, { type: blob.type })]);
  } catch (error) {
    alert(`Could not attach "${path}": ${error.message}`);
  }
}

const SLASH_PATH_COMMAND = /^\/path\s+(.+)$/;

async function sendMessage() {
  if (!state.currentChatId) return;
  const body = $("messageInput").value.trim();
  if (!body && !state.pendingAttachments.length) return;
  $("uploadError").hidden = true;
  const pathCommand = body.match(SLASH_PATH_COMMAND);
  if (state.hostSessionId) {
    if (pathCommand || state.pendingAttachments.length) {
      alert("Attachments are not available when continuing a native host session.");
      return;
    }
    const sessionId = state.hostSessionId;
    const session = state.hostSessions.find((item) => item.session_id === sessionId);
    if (!session?.available || state.hostResumes.has(sessionId) || session.resuming) return;
    const requestId = appendHostSessionPrompt(sessionId, body);
    $("messageInput").value = "";
    state.hostResumes.set(sessionId, true);
    renderUnonboardedHostSessions();
    renderHostSessionDetails(session);
    syncHostComposer(session);
    void resumeHostSessionInBackground(sessionId, body, { requestId });
    return;
  }
  if (state.runInProgress) return;
  if (pathCommand) {
    $("messageInput").value = "";
    await attachPathToComposer(pathCommand[1]);
    return;
  }
  const currentChat = state.chats.find((chat) => chat.chat_id === state.currentChatId);
  $("sendBtn").disabled = true;
  $("sendBtn").textContent = state.pendingAttachments.length ? "Uploading..." : "Sending...";
  const attachmentNote = state.pendingAttachments.length
    ? `📎 ${state.pendingAttachments.map((file) => file.name).join(", ")}` : "";
  const pendingRow = appendPendingUserRow(body, attachmentNote);
  $("messageInput").value = "";
  let stored = false;
  try {
    const uploads = await Promise.all(state.pendingAttachments.map(uploadAttachment));
    await api(`/api/chats/${state.currentChatId}/messages`, {
      method: "POST",
      body: JSON.stringify({
        body,
        attachment_ids: uploads.map((attachment) => attachment.attachment_id),
      }),
    });
    stored = true;
    clearPendingAttachments();
    await openChat(state.currentChatId);
    const runPayload = runPayloadForMessage(currentChat, body);
    if (runPayload) {
      await runChatStream(runPayload, state.currentChatId);
    } else {
      $("sendBtn").disabled = false;
      $("sendBtn").textContent = "Send";
    }
  } catch (error) {
    if (!stored) {
      // Not saved: take the bubble back and return the text to the box so nothing is lost or double-sent.
      pendingRow.remove();
      if (!$("messageInput").value) $("messageInput").value = body;
    }
    $("sendBtn").disabled = false;
    $("sendBtn").textContent = "Send";
    showAttachmentError(`Unable to send message: ${error.message}`);
  }
}

async function uploadAttachment(file) {
  const response = await fetch("/api/v1/uploads", {
    method: "POST",
    headers: {
      "Content-Type": file.type || "application/octet-stream",
      "X-Agent-Slack-Filename": encodeURIComponent(file.name),
      ...(state.activeServerId ? { "X-Agent-Slack-Server": state.activeServerId } : {}),
    },
    body: file,
  });
  if (!response.ok) throw new Error(await response.text() || `HTTP ${response.status}`);
  return response.json();
}

function openAttachmentPicker() {
  $("attachInput").click();
}

function handleAttachmentInputChange(event) {
  selectAttachments(event.target.files);
  event.target.value = "";
}

function handleMessagePaste(event) {
  const files = Array.from(event.clipboardData?.files || []);
  if (!files.length) return;
  const named = files.map((file, index) => {
    if (file.name) return file;
    const image = file.type.startsWith("image/");
    const extension = ({ "image/jpeg": "jpg", "image/gif": "gif", "image/webp": "webp", "image/png": "png" })[file.type] || (image ? "img" : "bin");
    return new File([file], `${image ? "pasted-image" : "pasted-file"}-${Date.now()}-${index + 1}.${extension}`, { type: file.type || (image ? "image/png" : "application/octet-stream") });
  });
  selectAttachments(named);
  event.preventDefault();
}

function selectAttachments(files) {
  $("uploadError").hidden = true;
  let rejectedLargeFile = false;
  let rejectedCount = false;
  const existing = new Set(
    state.pendingAttachments.map((file) => `${file.name}:${file.size}:${file.lastModified}`)
  );
  for (const file of files) {
    if (file.size > 50 * 1024 * 1024) {
      rejectedLargeFile = true;
      continue;
    }
    const key = `${file.name}:${file.size}:${file.lastModified}`;
    if (!existing.has(key) && state.pendingAttachments.length < 10) {
      state.pendingAttachments.push(file);
      existing.add(key);
    } else if (!existing.has(key)) {
      rejectedCount = true;
    }
  }
  renderPendingAttachments();
  if (rejectedLargeFile) showAttachmentError("Attachments must be 50 MB or smaller.");
  else if (rejectedCount) showAttachmentError("You can attach up to 10 items per message.");
}

function renderPendingAttachments() {
  const container = $("pendingAttachments");
  container.querySelectorAll("img[data-preview-url]").forEach((image) => URL.revokeObjectURL(image.dataset.previewUrl));
  container.innerHTML = "";
  state.pendingAttachments.forEach((file, index) => {
    const chip = document.createElement("span");
    chip.className = "pending-attachment";
    const previewUrl = file.type.startsWith("image/") ? URL.createObjectURL(file) : null;
    if (previewUrl) {
      const image = document.createElement("img");
      image.src = previewUrl;
      image.alt = "";
      image.dataset.previewUrl = previewUrl;
      chip.appendChild(image);
    }
    chip.append(document.createTextNode(`${file.name} · ${formatBytes(file.size)}`));
    const remove = document.createElement("button");
    remove.type = "button";
    remove.textContent = "×";
    remove.setAttribute("aria-label", `Remove ${file.name}`);
    remove.onclick = () => {
      state.pendingAttachments.splice(index, 1);
      renderPendingAttachments();
    };
    chip.appendChild(remove);
    container.appendChild(chip);
  });
}

function clearPendingAttachments() {
  document.querySelectorAll("#pendingAttachments img[data-preview-url]").forEach((image) => URL.revokeObjectURL(image.dataset.previewUrl));
  state.pendingAttachments = [];
  $("uploadError").hidden = true;
  renderPendingAttachments();
}

function showAttachmentError(message) {
  const error = $("uploadError");
  error.textContent = message;
  error.hidden = false;
}

function formatBytes(value) {
  const bytes = Number(value) || 0;
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function hideMentionMenu() {
  $("mentionMenu").hidden = true;
  $("mentionMenu").innerHTML = "";
}

function insertAgentMention(agentId, context) {
  const input = $("messageInput");
  input.value = mentionHelpers.insertMention(input.value, context, agentId);
  const cursor = context.start + agentId.length + 2;
  input.setSelectionRange(cursor, cursor);
  hideMentionMenu();
  input.focus();
}

function renderMentionMenu() {
  const input = $("messageInput");
  const context = mentionHelpers.queryAtCursor(input.value, input.selectionStart);
  const menu = $("mentionMenu");
  if (!context) {
    hideMentionMenu();
    return;
  }
  const matches = mentionHelpers.matchingAgents(state.agents, context.query).slice(0, 8);
  if (!matches.length) {
    hideMentionMenu();
    return;
  }
  menu.innerHTML = matches.map((agent) => `
    <button type="button" role="option" data-mention-agent="${escapeHtml(agent.agent_id)}">
      <span class="agent-menu-avatar">${initials(agent.title)}</span>
      <span><strong>${escapeHtml(agent.title)}</strong><small>@${escapeHtml(agent.agent_id)}</small></span>
    </button>
  `).join("");
  menu.querySelectorAll("[data-mention-agent]").forEach((button) => {
    button.onmousedown = (event) => {
      event.preventDefault();
      insertAgentMention(button.dataset.mentionAgent, context);
    };
  });
  menu.hidden = false;
}

async function deleteCurrentChat() {
  if (!state.currentChatId || state.runInProgress) return;
  const chatId = state.currentChatId;
  const chat = state.chats.find((item) => item.chat_id === chatId);
  const title = chat?.title || "this chat";
  if (!window.confirm(`Delete “${title}”? This permanently removes its messages.`)) return;

  $("deleteChatBtn").disabled = true;
  try {
    await api(`/api/chats/${chatId}`, { method: "DELETE" });
    state.currentChatId = null;
    state.currentChatUpdatedAt = null;
    state.streamingRows.forEach(clearManagedProgress);
    state.streamingRows.clear();
    await loadChats();
  } catch (error) {
    console.error(error);
    alert(`Unable to delete chat: ${error.message}`);
    $("deleteChatBtn").disabled = false;
  }
}

async function runAgents() {
  if (!state.currentChatId) return;
  const objective = $("objectiveInput").value.trim();
  await runChatStream({ mode: "respond", objective });
}

async function autoMeeting() {
  if (!state.currentChatId) return;
  const currentChat = state.chats.find((chat) => chat.chat_id === state.currentChatId);
  const defaultLead = state.orchestratorIds.find((agentId) => currentChat?.member_ids.includes(agentId))
    || state.orchestratorIds[0]
    || currentChat?.member_ids[0]
    || "";
  const lead = prompt("Lead agent id", defaultLead);
  const objective = $("objectiveInput").value.trim() || prompt("Meeting objective");
  if (!lead || !objective) return;
  await runChatStream({ mode: "auto_meeting", lead_agent_id: lead, objective });
}

async function submitNewChat() {
  const title = $("chatFormTitle").value.trim();
  const memberIds = [...state.peoplePickers.chat];
  if (!title || !memberIds.length) return;
  const chat = await api("/api/chats", {
    method: "POST",
    body: JSON.stringify({ title, member_ids: memberIds, kind: memberIds.length > 1 ? "group" : "direct", runner: $("newChatRunnerSelect").value || null, model: $("newChatModelSelect").value || null }),
  });
  $("chatDialog").close();
  $("chatForm").reset();
  await loadChats();
  await openChat(chat.chat_id);
}

async function submitMeeting() {
  if (!state.currentChatId) return;
  const leadAgentId = [...state.peoplePickers.meetingLead][0];
  const participantIds = [...state.peoplePickers.meetingParticipants];
  const objective = $("meetingObjective").value.trim();
  if (!leadAgentId || !participantIds.length || !objective) return;
  $("meetingDialog").close();
  $("meetingForm").reset();
  await runChatStream({
    mode: "meeting",
    lead_agent_id: leadAgentId,
    participant_ids: participantIds,
    objective,
  });
}

function setPeopleExpanded(expanded) {
  state.peopleExpanded = expanded;
  $("peoplePanel").classList.toggle("expanded", expanded);
  $("peoplePanel").classList.toggle("collapsed", !expanded);
  $("peopleToggleBtn").setAttribute("aria-expanded", String(expanded));
  $("agentListWrap").hidden = !expanded;
  if (!expanded) {
    hidePeopleTooltip();
  }
}

function setMobileSidebarOpen(open) {
  const enabled = Boolean(open) && (window.AgentSlackMobile?.isDrawerViewport(window) ?? window.matchMedia("(max-width: 639px)").matches);
  state.mobileSidebarOpen = enabled;
  $("mobilePeopleBtn").setAttribute("aria-expanded", String(enabled));
  $("mobileSidebarBackdrop").hidden = !enabled;
  $("mobileSidebarBackdrop").classList.toggle("visible", enabled);
  $("peoplePanel").closest(".sidebar-primary").classList.toggle("mobile-open", enabled);
  if (enabled) setPeopleExpanded(true);
}

function showPeopleTooltip(target) {
  const tooltip = $("peopleTooltip");
  const description = target?.dataset.description?.trim();
  const title = target?.dataset.title?.trim() || "Agent";
  if (!description) {
    return;
  }
  tooltip.innerHTML = `
    <span class="people-tooltip-title">${escapeHtml(title)}</span>
    <div class="people-tooltip-body">${escapeHtml(description)}</div>
  `;
  tooltip.hidden = false;
  const rect = target.getBoundingClientRect();
  const offset = 12;
  const maxLeft = window.innerWidth - tooltip.offsetWidth - 12;
  const maxTop = window.innerHeight - tooltip.offsetHeight - 12;
  const left = Math.min(rect.right + offset, Math.max(12, maxLeft));
  const top = Math.min(rect.top, Math.max(12, maxTop));
  tooltip.style.left = `${left}px`;
  tooltip.style.top = `${top}px`;
}

function hidePeopleTooltip() {
  const tooltip = $("peopleTooltip");
  tooltip.hidden = true;
}

function initials(label) {
  return String(label || "?")
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .replace(/[_-]+/g, " ")
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0].toUpperCase())
    .join("");
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function formatTime(value) {
  try {
    return new Date(value).toLocaleString();
  } catch {
    return value || "";
  }
}

$("sendBtn").onclick = sendMessage;
$("attachBtn").onclick = openAttachmentPicker;
$("attachInput").addEventListener("change", handleAttachmentInputChange);
$("voiceBtn").onclick = toggleVoice;
const messageInputWrap = $("messageInput").closest(".message-input-wrap");
["dragenter", "dragover"].forEach((eventName) => messageInputWrap.addEventListener(eventName, (event) => {
  event.preventDefault();
  if (event.dataTransfer?.types?.includes("Files")) messageInputWrap.classList.add("drag-active");
}));
messageInputWrap.addEventListener("dragleave", (event) => {
  if (!messageInputWrap.contains(event.relatedTarget)) messageInputWrap.classList.remove("drag-active");
});
messageInputWrap.addEventListener("drop", (event) => {
  event.preventDefault();
  messageInputWrap.classList.remove("drag-active");
  if (event.dataTransfer?.files?.length) selectAttachments(event.dataTransfer.files);
});
$("messageInput").addEventListener("keydown", (event) => {
  const firstMention = $("mentionMenu").querySelector("[data-mention-agent]");
  if (!$("mentionMenu").hidden && firstMention && ["Enter", "Tab"].includes(event.key)) {
    event.preventDefault();
    firstMention.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
    return;
  }
  if (event.key === "Escape" && !$("mentionMenu").hidden) {
    event.preventDefault();
    hideMentionMenu();
    return;
  }
  if (!composerKeys.shouldSendOnKeydown(event)) return;
  event.preventDefault();
  sendMessage();
});
$("messageInput").addEventListener("paste", (event) => {
  handleMessagePaste(event);
});
$("messageInput").addEventListener("input", renderMentionMenu);
$("messageInput").addEventListener("click", renderMentionMenu);
$("messageInput").addEventListener("blur", () => setTimeout(hideMentionMenu, 100));
$("runAgentsBtn").onclick = runAgents;
$("autoMeetingBtn").onclick = autoMeeting;
$("deleteChatBtn").onclick = deleteCurrentChat;
$("chatInfoBtn").onclick = () => {
  if (state.hostSessionId) {
    const session = state.hostSessions.find((item) => item.session_id === state.hostSessionId);
    if (session) window.alert(`Session ID: ${session.session_id}\nAgent: ${session.agent_type}\nFolder: ${session.project_name}`);
    return;
  }
  const chat = state.chats.find((item) => item.chat_id === state.currentChatId);
  if (chat) window.alert(`Chat ID: ${chat.chat_id}\nProject: ${state.servers.find((server) => server.server_id === state.activeServerId)?.project_root || ""}`);
};
$("stopRunBtn").onclick = stopCurrentRun;
$("chatRenameBtn").onclick = startTitleRename;
$("chatTitle").ondblclick = startTitleRename;
$("refreshAgentsBtn").onclick = async () => {
  if (state.runInProgress) return;
  await api("/api/agents/discover", { method: "POST", body: "{}" });
  await loadAgents();
};
$("newChatBtnHeader").onclick = () => {
  resetPeoplePicker("chat");
  loadRunnerOptions().then(() => fillRunnerSelect($("newChatRunnerSelect"), $("newChatModelSelect"), null, null)).catch((error) => console.warn(error));
  $("chatDialog").showModal();
};
$("chatRunnerSelect").addEventListener("change", async () => {
  if (!state.currentChatId || state.runInProgress) return;
  fillRunnerSelect($("chatRunnerSelect"), $("chatModelSelect"), $("chatRunnerSelect").value, "");
  try { renderChat(await api(`/api/chats/${encodeURIComponent(state.currentChatId)}`, { method: "PATCH", body: JSON.stringify({ runner: $("chatRunnerSelect").value }) })); }
  catch (error) { if (error.status === 409) await openChat(state.currentChatId); else alert(error.message); }
});
$("chatModelSelect").addEventListener("change", async () => {
  if (!state.currentChatId || state.runInProgress) return;
  try { renderChat(await api(`/api/chats/${encodeURIComponent(state.currentChatId)}`, { method: "PATCH", body: JSON.stringify({ model: $("chatModelSelect").value || null }) })); }
  catch (error) { if (error.status === 409) await openChat(state.currentChatId); else alert(error.message); }
});
$("newChatRunnerSelect").addEventListener("change", () => fillRunnerSelect($("newChatRunnerSelect"), $("newChatModelSelect"), $("newChatRunnerSelect").value, ""));
$("addServerBtn").onclick = openServerDialog;
$("hostSessionsServerBtn").onclick = openHostSessionsServer;
$("serverSettingsBtn").onclick = openServerSettings;
$("openChannelSettingsBtn").onclick = openChannelSettings;
$("cancelChannelSettingsBtn").onclick = () => $("channelSettingsDialog").close();
$("changePasswordBtn").onclick = async () => {
  const status = $("passwordSettingsStatus");
  try {
    await api("/api/v1/auth/password", { method: "POST", body: JSON.stringify({
      current_password: $("currentPassword").value, new_password: $("newPassword").value,
    }) });
    $("currentPassword").value = ""; $("newPassword").value = "";
    status.textContent = "Password changed.";
  } catch (error) { status.textContent = error.message; }
};
$("revokeDevicesBtn").onclick = async () => {
  const status = $("passwordSettingsStatus");
  try {
    const result = await api("/api/v1/auth/revoke-devices", { method: "POST" });
    status.textContent = `Signed out ${result.revoked} iOS device${result.revoked === 1 ? "" : "s"}.`;
  } catch (error) { status.textContent = error.message; }
};
$("configureTunnelBtn").onclick = openTunnelDialog;
$("configureClaudeTunnelBtn").onclick = () => openClaudeTunnelDialog().catch((error) => {
  $("serverFormError").textContent = error.message;
  $("serverFormError").hidden = false;
});
$("installClaudeMcpBtn").onclick = () => installClaudeMcp().catch((error) => {
  $("serverFormError").textContent = error.message;
  $("serverFormError").hidden = false;
});
$("downloadTunnelClientBtn").onclick = () => downloadTunnelClient().catch((error) => {
  $("tunnelStatus").textContent = error.message;
  $("tunnelStatus").hidden = false;
});
$("openTunnelDocsBtn").onclick = () => window.agentSlack.openTunnelDocs();
$("openClaudeTunnelsBtn").onclick = () => window.agentSlack.openClaudeTunnels();
$("cancelTunnelBtn").onclick = () => $("tunnelDialog").close();
$("cancelClaudeTunnelBtn").onclick = () => $("claudeTunnelDialog").close();
$("stopClaudeTunnelBtn").onclick = async () => {
  await window.agentSlack.stopClaudeTunnel();
  $("claudeTunnelStatus").textContent = "Stopped";
  $("claudeTunnelStatus").hidden = false;
  await updateClaudeTunnelSummary();
};
$("stopTunnelBtn").onclick = async () => {
  await window.agentSlack.stopTunnel();
  $("tunnelStatus").textContent = "Stopped";
  $("tunnelStatus").hidden = false;
};
$("browseServerPathBtn").onclick = browseServerPath;
$("browseServerLogoBtn").onclick = browseServerLogo;
$("cancelChatBtn").onclick = () => $("chatDialog").close();
$("cancelServerBtn").onclick = () => $("serverDialog").close();
$("cancelMeetingBtn").onclick = () => $("meetingDialog").close();
$("manualMeetingBtn").onclick = () => {
  resetPeoplePicker("meetingLead");
  resetPeoplePicker("meetingParticipants");
  $("meetingDialog").showModal();
};
$("peopleToggleBtn").onclick = () => setPeopleExpanded(!state.peopleExpanded);
$("mobilePeopleBtn").onclick = () => setMobileSidebarOpen(!state.mobileSidebarOpen);
$("mobileSidebarBackdrop").onclick = () => setMobileSidebarOpen(false);
$("chatForm").addEventListener("submit", (event) => {
  event.preventDefault();
  submitNewChat();
});
$("meetingForm").addEventListener("submit", (event) => {
  event.preventDefault();
  submitMeeting();
});
$("serverForm").addEventListener("submit", (event) => {
  event.preventDefault();
  submitServer();
});
$("channelSettingsForm").addEventListener("submit", (event) => {
  event.preventDefault();
  saveChannelSettings();
});
$("tunnelForm").addEventListener("submit", (event) => {
  event.preventDefault();
  submitTunnel().catch((error) => {
    $("tunnelStatus").textContent = error.message;
    $("tunnelStatus").hidden = false;
  });
});
$("claudeTunnelForm").addEventListener("submit", (event) => {
  event.preventDefault();
  submitClaudeTunnel().catch((error) => {
    $("claudeTunnelStatus").textContent = error.message;
    $("claudeTunnelStatus").hidden = false;
  });
});
Object.entries(peoplePickerConfig).forEach(([key, config]) => {
  const search = $(config.search);
  search.addEventListener("focus", () => renderPeoplePicker(key, true));
  search.addEventListener("input", () => renderPeoplePicker(key, true));
  search.addEventListener("keydown", (event) => {
    if (event.key === "Escape") $(config.menu).hidden = true;
  });
});
document.addEventListener("click", (event) => {
  Object.values(peoplePickerConfig).forEach((config) => {
    const menu = $(config.menu);
    if (!menu.parentElement.contains(event.target)) menu.hidden = true;
  });
});
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) { syncChatState(); loadPermissions(); if (state.hostSessionsView || state.activeServerId) loadAllSessions(); }
});
window.addEventListener("focus", syncChatState);
window.addEventListener("hashchange", handleDeepLink);
window.addEventListener("resize", () => {
  if (!(window.AgentSlackMobile?.isDrawerViewport(window) ?? window.matchMedia("(max-width: 639px)").matches)) setMobileSidebarOpen(false);
});
window.setInterval(syncChatState, 2000);
window.setInterval(() => {
  if (!document.hidden && (state.hostSessionsView || state.activeServerId)) loadAllSessions();
}, 5000);
window.setInterval(() => { if (!document.hidden) loadPermissions(); }, 3000);

initializeAuth().then((ready) => ready && loadServers().then(loadActiveServer).then(handleDeepLink)).catch((error) => {
  console.error(error);
  alert(`Failed to initialize Agent Slack: ${error.message}`);
});

setPeopleExpanded(false);

window.AgentSlackChatMode?.init({
  api, streamApi, renderMarkdown, $,
  uploadAttachment, appendMessageAttachments, formatBytes,
});
