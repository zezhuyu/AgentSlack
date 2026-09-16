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
  currentRunId: null,
  stoppingTaskIds: new Set(),
  syncInProgress: false,
  streamAttached: false,
  chatListSignature: "",
  streamingRows: new Map(),
  answeringQuestionIds: new Set(),
  pendingAttachments: [],
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
const mentionHelpers = window.AgentSlackMentions;
const interactionHelpers = window.AgentSlackInteractions;
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
  await restoreActiveRun(chatId);
  await loadChats({ openFirst: false });
  renderChats();
  renderAgents();
  if (state.mobileSidebarOpen) setMobileSidebarOpen(false);
}

async function syncChatState() {
  if (!state.activeServerId || state.streamAttached || state.syncInProgress || document.hidden) return;
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

function renderChat(chat) {
  const messages = $("messages");
  const followLatest = messages.scrollHeight - messages.scrollTop - messages.clientHeight < 80;
  const previousScrollTop = messages.scrollTop;
  $("chatTitle").textContent = chat.title;
  $("chatMembers").textContent = chat.member_ids.join(", ");
  state.currentChatUpdatedAt = chat.updated_at || null;
  state.streamingRows.clear();
  messages.innerHTML = "";
  chatSync.visibleMessages(chat.messages).forEach((message) => {
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
    appendMessageAttachments(row.querySelector(".message-body"), message);
    appendChoiceInteraction(row.querySelector(".message-body"), message);
    messages.appendChild(row);
  });
  messages.scrollTop = followLatest ? messages.scrollHeight : previousScrollTop;
}

function appendMessageAttachments(container, message) {
  const attachments = message.metadata?.attachments || [];
  if (!attachments.length) return;
  const list = document.createElement("div");
  list.className = "message-attachments";
  attachments.forEach((attachment) => {
    const item = document.createElement("a");
    item.className = "message-attachment";
    item.href = attachment.url || `/api/uploads/${encodeURIComponent(attachment.attachment_id)}`;
    item.target = "_blank";
    item.rel = "noopener";
    item.textContent = `📎 ${attachment.filename} · ${formatBytes(attachment.size)}`;
    item.title = attachment.content_type || "File attachment";
    list.appendChild(item);
  });
  container.appendChild(list);
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
      if (event.type === "voice_transcript") setVoiceStatus(`Heard: "${event.text || ""}"`, true, "thinking");
      if (event.type === "speech_ready" && event.url) {
        voiceState.audio?.pause();
        voiceState.audio = new Audio(new URL(event.url, window.location.origin));
        setVoiceStatus(event.text ? `"${event.text}"` : "Responding…", true, "responding");
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
      <div class="message-content"><span class="typing-dots"><i></i><i></i><i></i></span></div>
    </div>
  `;
  $("messages").appendChild(row);
  $("messages").scrollTop = $("messages").scrollHeight;
  const stopButton = row.querySelector(".agent-stop-button");
  stopButton.onclick = () => stopAgentTask(taskId);
  state.streamingRows.set(taskId, {
    row,
    body: row.querySelector(".message-content"),
    stopButton,
    agentId,
    rawText: "",
    started: false,
  });
  return state.streamingRows.get(taskId);
}

function applyActiveRun(run) {
  if (!run) {
    if (state.streamAttached) return;
    state.streamingRows.forEach((stream) => stream.row.remove());
    state.streamingRows.clear();
    state.runInProgress = false;
    state.currentRunId = null;
    $("stopRunBtn").hidden = true;
    $("deleteChatBtn").disabled = !state.currentChatId;
    $("sendBtn").disabled = false;
    $("sendBtn").textContent = "Send";
    return;
  }

  state.runInProgress = true;
  state.currentRunId = run.run_id;
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
      stream.row.remove();
      state.streamingRows.delete(taskId);
    }
  });
  runningTasks.forEach((task) => {
    const stream = createStreamingRow(task.task_id, task.agent_id, task.agent_label || task.agent_id);
    stream.rawText = task.text || "";
    stream.started = Boolean(stream.rawText);
    if (stream.rawText) stream.body.innerHTML = renderMarkdown(stream.rawText);
  });
}

async function restoreActiveRun(chatId) {
  if (!chatId || state.streamAttached) return;
  const payload = await api(`/api/runs/active?chat_id=${encodeURIComponent(chatId)}`);
  if (chatId !== state.currentChatId || state.streamAttached) return;
  applyActiveRun(chatSync.activeRunForChat(payload.runs, chatId));
}

async function renderStreamEvent(event) {
  const taskId = event.task_id || event.agent_id;
  if (event.type === "run_started") {
    state.currentRunId = event.run_id;
    $("stopRunBtn").hidden = false;
    $("stopRunBtn").disabled = false;
    $("stopRunBtn").textContent = "Stop Run";
  } else if (event.type === "agent_started") {
    createStreamingRow(taskId, event.agent_id, event.agent_label);
  } else if (event.type === "delta") {
    const stream = state.streamingRows.get(taskId);
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
    const stream = state.streamingRows.get(taskId);
    if (stream) {
      stream.body.innerHTML = renderMarkdown(event.body || stream.rawText);
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
      const stopped = event.message || "Stopped by user.";
      const content = stream.rawText ? `${stream.rawText}\n\n_${stopped}_` : `_${stopped}_`;
      stream.body.innerHTML = renderMarkdown(content);
      stream.row.classList.remove("streaming");
      stream.row.classList.add("cancelled");
      stream.row.querySelector("time").textContent = "stopped";
      stream.stopButton.remove();
      state.streamingRows.delete(taskId);
      state.stoppingTaskIds.delete(taskId);
    }
  } else if (event.type === "run_cancelled") {
    $("stopRunBtn").disabled = true;
    $("stopRunBtn").textContent = "Stopped";
  } else if (event.type === "error") {
    const error = new Error(event.message || "Agent run failed");
    error.agentRunFailure = true;
    throw error;
  }
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
    console.warn("Agent Slack stream detached", error);
    alert(chatSync.streamErrorMessage(error));
  } finally {
    state.streamAttached = false;
    state.runInProgress = false;
    state.currentRunId = null;
    state.stoppingTaskIds.clear();
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

async function sendMessage() {
  if (!state.currentChatId || state.runInProgress) return;
  const body = $("messageInput").value.trim();
  if (!body) return;
  const currentChat = state.chats.find((chat) => chat.chat_id === state.currentChatId);
  $("sendBtn").disabled = true;
  $("sendBtn").textContent = state.pendingAttachments.length ? "Uploading..." : "Sending...";
  try {
    const uploads = await Promise.all(state.pendingAttachments.map(uploadAttachment));
    await api(`/api/chats/${state.currentChatId}/messages`, {
      method: "POST",
      body: JSON.stringify({
        body,
        attachment_ids: uploads.map((attachment) => attachment.attachment_id),
      }),
    });
    $("messageInput").value = "";
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
    $("sendBtn").disabled = false;
    $("sendBtn").textContent = "Send";
    alert(`Unable to send message: ${error.message}`);
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

function selectAttachments(files) {
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
  if (rejectedLargeFile) alert("Attachments must be 50 MB or smaller.");
  if (rejectedCount) alert("You can attach up to 10 items per message.");
}

function renderPendingAttachments() {
  const container = $("pendingAttachments");
  container.innerHTML = "";
  state.pendingAttachments.forEach((file, index) => {
    const chip = document.createElement("span");
    chip.className = "pending-attachment";
    chip.append(document.createTextNode(`${file.type.startsWith("image/") ? "🖼️" : "📄"} ${file.name} · ${formatBytes(file.size)}`));
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
  state.pendingAttachments = [];
  renderPendingAttachments();
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
$("messageInput").addEventListener("input", renderMentionMenu);
$("messageInput").addEventListener("click", renderMentionMenu);
$("messageInput").addEventListener("blur", () => setTimeout(hideMentionMenu, 100));
$("runAgentsBtn").onclick = runAgents;
$("autoMeetingBtn").onclick = autoMeeting;
$("deleteChatBtn").onclick = deleteCurrentChat;
$("stopRunBtn").onclick = stopCurrentRun;
$("refreshAgentsBtn").onclick = async () => {
  if (state.runInProgress) return;
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
