const state = {
  servers: [],
  activeServerId: null,
  agents: [],
  chats: [],
  currentChatId: null,
  currentChatUpdatedAt: null,
  orchestratorIds: [],
  peopleExpanded: false,
  mobileSidebarOpen: false,
  openingDmAgentIds: new Set(),
  runInProgress: false,
  syncInProgress: false,
  chatListSignature: "",
  streamingRows: new Map(),
  serverDialogMode: "create",
  peoplePickers: {
    chat: new Set(),
    meetingLead: new Set(),
    meetingParticipants: new Set(),
  },
};

const $ = (id) => document.getElementById(id);
const renderMarkdown = (value) => window.AgentSlackMarkdown.render(value);
const appendStreamMarkdown = (currentText, delta) => window.AgentSlackMarkdown.appendStream(currentText, delta);
const chatSync = window.AgentSlackSync;
const composerKeys = window.AgentSlackComposer;

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
    const text = await response.text();
    throw new Error(text || `HTTP ${response.status}`);
  }
  return response.json();
}

async function streamApi(path, payload, onEvent) {
  const headers = {
    "Content-Type": "application/json",
    ...(state.activeServerId ? { "X-Agent-Slack-Server": state.activeServerId } : {}),
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
  $("serverList").innerHTML = "";
  state.servers.forEach((server) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `server-btn ${server.server_id === state.activeServerId ? "active" : ""}`;
    if (server.logo_url) {
      const image = document.createElement("img");
      image.src = `${server.logo_url}?v=${encodeURIComponent(server.logo_revision || "1")}`;
      image.alt = "";
      button.appendChild(image);
    } else {
      button.textContent = initials(server.project_name || server.name);
    }
    button.title = server.available ? server.name : `${server.name} (folder unavailable)`;
    button.disabled = !server.available || state.runInProgress;
    button.setAttribute("aria-label", `Switch to ${server.name}`);
    button.setAttribute("aria-current", server.server_id === state.activeServerId ? "true" : "false");
    button.onclick = () => switchServer(server.server_id);
    $("serverList").appendChild(button);
  });
}

async function switchServer(serverId) {
  if (serverId === state.activeServerId || state.runInProgress) return;
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
  if (!state.activeServerId) {
    renderEmptyServerState();
    return;
  }
  setWorkspaceAvailable(true);
  await loadHealth();
  await loadAgents();
  await loadChats();
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
  $("chatTitle").textContent = "Add an agent system";
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
  if (openFirst && !state.currentChatId && state.chats.length) {
    await openChat(state.chats[0].chat_id);
  } else if (!state.chats.length) {
    renderNoChatState();
  }
}

function renderNoChatState() {
  state.currentChatId = null;
  state.currentChatUpdatedAt = null;
  $("chatTitle").textContent = "Select a chat";
  $("chatMembers").textContent = "";
  $("messages").innerHTML = '<div class="empty-server"><strong>No chats yet</strong><p>Create a chat or open a person to begin.</p></div>';
  $("deleteChatBtn").disabled = true;
}

async function openChat(chatId) {
  const chat = await api(`/api/chats/${chatId}`);
  state.currentChatId = chatId;
  $("deleteChatBtn").disabled = state.runInProgress;
  renderChat(chat);
  await loadChats({ openFirst: false });
  renderChats();
  renderAgents();
  if (state.mobileSidebarOpen) setMobileSidebarOpen(false);
}

async function syncChatState() {
  if (!state.activeServerId || state.runInProgress || state.syncInProgress || document.hidden) return;
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
    if (!chatSync.shouldRefreshChat(summary, state.currentChatUpdatedAt)) return;

    const chatId = state.currentChatId;
    const chat = await api(`/api/chats/${chatId}`);
    if (serverId === state.activeServerId && chatId === state.currentChatId && !state.runInProgress) {
      renderChat(chat);
    }
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

function renderChat(chat) {
  const messages = $("messages");
  const followLatest = messages.scrollHeight - messages.scrollTop - messages.clientHeight < 80;
  const previousScrollTop = messages.scrollTop;
  $("chatTitle").textContent = chat.title;
  $("chatMembers").textContent = chat.member_ids.join(", ");
  state.currentChatUpdatedAt = chat.updated_at || null;
  messages.innerHTML = "";
  (chat.messages || []).forEach((message) => {
    const row = document.createElement("article");
    row.className = `message ${message.author_type}`;
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
    messages.appendChild(row);
  });
  messages.scrollTop = followLatest ? messages.scrollHeight : previousScrollTop;
}

function createStreamingRow(agentId, agentLabel) {
  const row = document.createElement("article");
  row.className = "message agent streaming";
  row.innerHTML = `
    <div class="message-avatar">${initials(agentLabel)}</div>
    <div class="message-body">
      <header>
        <strong>${escapeHtml(agentLabel)}</strong>
        <time>working now</time>
      </header>
      <div class="message-content"><span class="typing-dots"><i></i><i></i><i></i></span></div>
    </div>
  `;
  $("messages").appendChild(row);
  $("messages").scrollTop = $("messages").scrollHeight;
  state.streamingRows.set(agentId, {
    row,
    body: row.querySelector(".message-content"),
    rawText: "",
    started: false,
  });
}

async function renderStreamEvent(event) {
  if (event.type === "agent_started") {
    createStreamingRow(event.agent_id, event.agent_label);
  } else if (event.type === "delta") {
    const stream = state.streamingRows.get(event.agent_id);
    if (!stream) return;
    if (!stream.started) {
      stream.started = true;
    }
    const rendered = appendStreamMarkdown(stream.rawText, event.text);
    stream.rawText = rendered.text;
    stream.body.innerHTML = rendered.html;
    $("messages").scrollTop = $("messages").scrollHeight;
    await new Promise((resolve) => setTimeout(resolve, 14));
  } else if (event.type === "agent_completed") {
    const stream = state.streamingRows.get(event.agent_id);
    if (stream) {
      stream.body.innerHTML = renderMarkdown(stream.rawText);
      stream.row.classList.remove("streaming");
      stream.row.querySelector("time").textContent = "just now";
      state.streamingRows.delete(event.agent_id);
    }
  } else if (event.type === "agent_failed") {
    let stream = state.streamingRows.get(event.agent_id);
    if (!stream) {
      createStreamingRow(event.agent_id, event.agent_label || event.agent_id);
      stream = state.streamingRows.get(event.agent_id);
    }
    if (stream) {
      stream.body.innerHTML = renderMarkdown(event.message || "**Agent run failed.**");
      stream.row.classList.remove("streaming");
      stream.row.classList.add("failed");
      stream.row.querySelector("time").textContent = "failed";
      state.streamingRows.delete(event.agent_id);
    }
  } else if (event.type === "error") {
    throw new Error(event.message || "Agent run failed");
  }
}

async function runChatStream(payload, chatId = state.currentChatId) {
  if (!chatId || state.runInProgress) return;
  let resultChatId = chatId;
  state.runInProgress = true;
  $("deleteChatBtn").disabled = true;
  $("sendBtn").disabled = true;
  $("sendBtn").textContent = "Working...";
  try {
    await streamApi(`/api/chats/${chatId}/run-stream`, payload, async (event) => {
      if (event.type === "meeting_created") {
        resultChatId = event.chat_id;
        await loadChats();
        await openChat(resultChatId);
      } else if (event.type === "run_completed" && event.chat_id) {
        resultChatId = event.chat_id;
      }
      await renderStreamEvent(event);
    });
  } catch (error) {
    console.error(error);
    alert(`Agent run failed: ${error.message}`);
  } finally {
    state.runInProgress = false;
    state.streamingRows.clear();
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
  $("serverPathLabel").hidden = true;
  $("serverPathPicker").hidden = true;
  $("serverFormError").hidden = true;
  $("serverDialog").showModal();
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
  if (state.serverDialogMode === "create" && !projectRoot) return;
  try {
    if (state.serverDialogMode === "edit") {
      await api(`/api/servers/${state.activeServerId}`, {
        method: "PATCH",
        body: JSON.stringify({ name, logo_path: logoPath || undefined }),
      });
    } else {
      await api("/api/servers", {
        method: "POST",
        body: JSON.stringify({ project_root: projectRoot, name, logo_path: logoPath || undefined }),
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

async function sendMessage() {
  if (!state.currentChatId || state.runInProgress) return;
  const body = $("messageInput").value.trim();
  if (!body) return;
  const currentChat = state.chats.find((chat) => chat.chat_id === state.currentChatId);
  await api(`/api/chats/${state.currentChatId}/messages`, {
    method: "POST",
    body: JSON.stringify({ body }),
  });
  $("messageInput").value = "";
  await openChat(state.currentChatId);
  const memberIds = currentChat?.member_ids || [];
  const leadAgentId = state.orchestratorIds.find((agentId) => memberIds.includes(agentId));
  let runPayload = null;
  if (currentChat?.kind === "direct" && memberIds.length === 1) {
    const directAgentId = memberIds[0];
    if (state.orchestratorIds.includes(directAgentId)) {
      runPayload = { mode: "auto_meeting", lead_agent_id: directAgentId, objective: body };
    } else {
      runPayload = { mode: "respond", agent_ids: [directAgentId], objective: body };
    }
  } else if (memberIds.length > 0) {
    if (leadAgentId) {
      runPayload = { mode: "meeting", lead_agent_id: leadAgentId, participant_ids: memberIds, objective: body };
    } else {
      runPayload = { mode: "respond", agent_ids: memberIds, objective: body };
    }
  }
  if (runPayload) await runChatStream(runPayload, state.currentChatId);
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
    body: JSON.stringify({ title, member_ids: memberIds, kind: memberIds.length > 1 ? "group" : "direct" }),
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
  const enabled = Boolean(open) && window.matchMedia("(max-width: 900px)").matches;
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
$("messageInput").addEventListener("keydown", (event) => {
  if (!composerKeys.shouldSendOnKeydown(event)) return;
  event.preventDefault();
  sendMessage();
});
$("runAgentsBtn").onclick = runAgents;
$("autoMeetingBtn").onclick = autoMeeting;
$("deleteChatBtn").onclick = deleteCurrentChat;
$("refreshAgentsBtn").onclick = async () => {
  await api("/api/agents/discover", { method: "POST", body: "{}" });
  await loadAgents();
};
$("newChatBtnHeader").onclick = () => {
  resetPeoplePicker("chat");
  $("chatDialog").showModal();
};
$("addServerBtn").onclick = openServerDialog;
$("serverSettingsBtn").onclick = openServerSettings;
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
  if (!document.hidden) syncChatState();
});
window.addEventListener("focus", syncChatState);
window.addEventListener("resize", () => {
  if (!window.matchMedia("(max-width: 900px)").matches) setMobileSidebarOpen(false);
});
window.setInterval(syncChatState, 2000);

loadServers().then(loadActiveServer).catch((error) => {
  console.error(error);
  alert(`Failed to initialize Agent Slack: ${error.message}`);
});

setPeopleExpanded(false);
