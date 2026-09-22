(function initAgentSlackChatMode(root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.AgentSlackChatMode = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function createAgentSlackChatMode() {
  let deps = null; // { api, streamApi, renderMarkdown, uploadAttachment, appendMessageAttachments, formatBytes, $ }
  const SLASH_PATH_COMMAND = /^\/path\s+(.+)$/;
  const state = {
    mode: "agent",
    sessions: [],
    currentSessionId: null,
    currentResponseId: null,
    streamingText: "",
    pendingAttachments: [], // { kind: "upload"|"path", value, filename, size, contentType }
    voice: { ws: null, stream: null, recorder: null, audioContext: null, analyser: null, monitorFrame: null, speaking: false, silenceSince: 0, active: false },
  };

  function $(id) { return deps.$(id); }

  function init(options) {
    deps = options;
    $("chatModeTabBtn").addEventListener("click", () => setMode("chat"));
    $("agentModeTabBtn").addEventListener("click", () => setMode("agent"));
    $("chatNewSessionBtn").addEventListener("click", () => void createSession());
    $("chatIndexBuildBtn").addEventListener("click", () => void buildIndex());
    $("chatSendBtn").addEventListener("click", () => void sendMessage());
    $("chatCancelBtn").addEventListener("click", () => cancelResponse());
    $("chatModeInput").addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        void sendMessage();
      }
    });
    $("chatVoiceBtn").addEventListener("click", () => void toggleVoice());
    wireDragAndDrop();
  }

  function wireDragAndDrop() {
    const wrap = $("chatModeInput").closest(".message-input-wrap");
    ["dragenter", "dragover"].forEach((eventName) => wrap.addEventListener(eventName, (event) => {
      event.preventDefault();
      if (event.dataTransfer?.types?.includes("Files")) wrap.classList.add("drag-active");
    }));
    wrap.addEventListener("dragleave", (event) => {
      if (!wrap.contains(event.relatedTarget)) wrap.classList.remove("drag-active");
    });
    wrap.addEventListener("drop", (event) => {
      event.preventDefault();
      wrap.classList.remove("drag-active");
      if (event.dataTransfer?.files?.length) void attachUploadedFiles(event.dataTransfer.files);
    });
  }

  async function attachUploadedFiles(files) {
    for (const file of files) {
      if (file.size > 50 * 1024 * 1024) {
        alert(`"${file.name}" is over the 50 MB attachment limit.`);
        continue;
      }
      try {
        const uploaded = await deps.uploadAttachment(file);
        state.pendingAttachments.push({
          kind: "upload", value: uploaded.attachment_id,
          filename: uploaded.filename, size: uploaded.size, contentType: uploaded.content_type,
        });
      } catch (error) {
        alert(`Could not attach "${file.name}": ${error.message}`);
      }
    }
    renderPendingAttachments();
  }

  async function attachPath(rawPath) {
    const path = rawPath.trim();
    if (!path) return;
    // Chat mode already exposes any in-project file at /api/v1/files?path=...
    // (the same endpoint citation previews use), so a path attachment doesn't
    // need a separate upload round-trip - the backend resolves it directly.
    state.pendingAttachments.push({ kind: "path", value: path, filename: path.split("/").pop() || path });
    renderPendingAttachments();
  }

  function renderPendingAttachments() {
    let container = $("chatModePendingAttachments");
    if (!container) {
      container = document.createElement("div");
      container.id = "chatModePendingAttachments";
      container.className = "pending-attachments";
      $("chatModeInput").closest(".composer").prepend(container);
    }
    container.innerHTML = "";
    state.pendingAttachments.forEach((attachment, index) => {
      const chip = document.createElement("span");
      chip.className = "pending-attachment";
      const sizeLabel = attachment.size ? ` · ${deps.formatBytes(attachment.size)}` : "";
      chip.append(document.createTextNode(`📎 ${attachment.filename}${sizeLabel}`));
      const remove = document.createElement("button");
      remove.type = "button";
      remove.textContent = "×";
      remove.setAttribute("aria-label", `Remove ${attachment.filename}`);
      remove.onclick = () => {
        state.pendingAttachments.splice(index, 1);
        renderPendingAttachments();
      };
      chip.appendChild(remove);
      container.appendChild(chip);
    });
  }

  function setMode(mode) {
    state.mode = mode;
    $("chatModeTabBtn").classList.toggle("is-active", mode === "chat");
    $("agentModeTabBtn").classList.toggle("is-active", mode === "agent");
    $("chatModeTabBtn").setAttribute("aria-selected", String(mode === "chat"));
    $("agentModeTabBtn").setAttribute("aria-selected", String(mode === "agent"));
    $("chatModePane").hidden = mode !== "chat";
    $("agentModePane").hidden = mode === "chat";
    if (mode === "chat" && !state.sessions.length) void bootstrapChatMode();
  }

  async function bootstrapChatMode() {
    await Promise.all([loadSessions(), loadIndexStatus()]);
    if (!state.currentSessionId && state.sessions.length) selectSession(state.sessions[0].id);
  }

  async function loadIndexStatus() {
    try {
      const status = await deps.api("/api/v1/chat/index/status");
      $("chatIndexStatus").textContent = `Index: ${status.status || "not_indexed"}`;
    } catch (_error) {
      $("chatIndexStatus").textContent = "";
    }
  }

  async function buildIndex() {
    $("chatIndexStatus").textContent = "Indexing…";
    try {
      const result = await deps.api("/api/v1/chat/index/build", { method: "POST", body: JSON.stringify({}) });
      $("chatIndexStatus").textContent = `Index: ready (${result.files_indexed} files)`;
    } catch (error) {
      $("chatIndexStatus").textContent = "Index build failed";
    }
  }

  async function loadSessions() {
    const payload = await deps.api("/api/v1/chat/sessions");
    state.sessions = payload.sessions || [];
    renderSessionList();
  }

  async function createSession() {
    const session = await deps.api("/api/v1/chat/sessions", { method: "POST", body: JSON.stringify({}) });
    state.sessions.unshift(session);
    renderSessionList();
    selectSession(session.id);
  }

  function renderSessionList() {
    const container = $("chatModeSessions");
    container.innerHTML = "";
    state.sessions.forEach((session) => {
      const item = document.createElement("button");
      item.type = "button";
      item.className = "chatmode-session-item" + (session.id === state.currentSessionId ? " is-active" : "");
      item.textContent = session.title || "New chat";
      item.addEventListener("click", () => selectSession(session.id));
      container.appendChild(item);
    });
  }

  async function selectSession(sessionId) {
    state.currentSessionId = sessionId;
    renderSessionList();
    const session = await deps.api(`/api/v1/chat/sessions/${sessionId}`);
    renderMessages(session.messages || []);
  }

  function renderMessages(messages) {
    const container = $("chatModeMessages");
    container.innerHTML = "";
    messages.forEach((message) => appendMessageRow(message.role, message.text, message.sources, message.attachments));
    container.scrollTop = container.scrollHeight;
  }

  function appendMessageRow(role, text, sources, attachments) {
    const container = $("chatModeMessages");
    const row = document.createElement("article");
    row.className = `message ${role === "user" ? "user" : "agent"}`;
    row.innerHTML = `
      <div class="message-avatar">${role === "user" ? "Y" : "AI"}</div>
      <div class="message-body">
        <header><strong>${role === "user" ? "You" : "Chat"}</strong></header>
        <div class="message-content"></div>
      </div>
    `;
    const content = row.querySelector(".message-content");
    content.innerHTML = deps.renderMarkdown(text || "");
    if (attachments && attachments.length) {
      deps.appendMessageAttachments(row.querySelector(".message-body"), { metadata: { attachments } });
    }
    if (sources && sources.length) {
      const list = document.createElement("div");
      list.className = "chatmode-sources";
      sources.forEach((source) => {
        const chip = document.createElement("span");
        chip.className = "chatmode-source-chip";
        chip.textContent = `📄 ${source.path}:${source.start_line}-${source.end_line}`;
        list.appendChild(chip);
      });
      row.querySelector(".message-body").appendChild(list);
    }
    container.appendChild(row);
    container.scrollTop = container.scrollHeight;
    return row;
  }

  function pendingAttachmentSpecs() {
    return state.pendingAttachments.map((attachment) => ({ kind: attachment.kind, value: attachment.value }));
  }

  function clearPendingAttachments() {
    state.pendingAttachments = [];
    const container = $("chatModePendingAttachments");
    if (container) container.innerHTML = "";
  }

  async function sendMessage() {
    const input = $("chatModeInput");
    const text = input.value.trim();
    if (!text) return;
    const pathCommand = parsePathCommand(text);
    if (pathCommand) {
      input.value = "";
      await attachPath(pathCommand);
      return;
    }
    if (!state.currentSessionId) await createSession();
    input.value = "";
    const attachmentSpecs = pendingAttachmentSpecs();
    appendMessageRow("user", text, null, state.pendingAttachments.map((attachment) => ({
      filename: attachment.filename, content_type: attachment.contentType || "application/octet-stream",
      size: attachment.size || 0, url: attachment.kind === "path" ? `/api/v1/files?path=${encodeURIComponent(attachment.value)}` : `/api/uploads/${attachment.value}`,
    })));
    clearPendingAttachments();
    await streamTurn(text, "text", attachmentSpecs);
  }

  async function streamTurn(text, modality, attachments) {
    const assistantRow = appendMessageRow("assistant", "", null);
    const contentEl = assistantRow.querySelector(".message-content");
    let assistantText = "";
    $("chatCancelBtn").hidden = false;
    try {
      await deps.streamApi(`/api/v1/chat/sessions/${state.currentSessionId}/messages`, { text, modality, attachments: attachments || [] }, (event) => {
        if (event.type === "response_started") state.currentResponseId = event.response_id;
        if (event.type === "assistant_text_delta") {
          assistantText += event.text;
          contentEl.innerHTML = deps.renderMarkdown(assistantText);
          $("chatModeMessages").scrollTop = $("chatModeMessages").scrollHeight;
        }
        if (event.type === "assistant_completed" && event.sources && event.sources.length) {
          const list = document.createElement("div");
          list.className = "chatmode-sources";
          event.sources.forEach((source) => {
            const chip = document.createElement("span");
            chip.className = "chatmode-source-chip";
            chip.textContent = `📄 ${source.path}:${source.start_line}-${source.end_line}`;
            list.appendChild(chip);
          });
          assistantRow.querySelector(".message-body").appendChild(list);
        }
        if (event.type === "error") {
          contentEl.innerHTML = deps.renderMarkdown(`_Chat error: ${event.message || "unknown error"}_`);
        }
      });
    } catch (error) {
      contentEl.innerHTML = deps.renderMarkdown(`_Chat error: ${error.message}_`);
    } finally {
      $("chatCancelBtn").hidden = true;
      state.currentResponseId = null;
    }
  }

  function cancelResponse() {
    if (!state.currentResponseId || !state.currentSessionId) return;
    void deps.api(`/api/v1/chat/sessions/${state.currentSessionId}/cancel`, {
      method: "POST",
      body: JSON.stringify({ response_id: state.currentResponseId }),
    });
  }

  // --- voice (full-duplex over WebSocket) ---------------------------------

  function setChatVoiceStatus(text, visible, cssState) {
    const status = $("chatVoiceStatus");
    $("chatVoiceStatusText").textContent = text;
    status.hidden = !visible;
    const btn = $("chatVoiceBtn");
    for (const name of ["listening", "speaking", "thinking", "responding", "error"]) {
      status.classList.toggle(`is-${name}`, name === cssState);
      btn.classList.toggle(`is-${name}`, name === cssState);
    }
  }

  async function toggleVoice() {
    if (state.voice.active) {
      stopVoice();
      return;
    }
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
      setChatVoiceStatus("Voice capture is unavailable in this browser.", true, "error");
      return;
    }
    try {
      state.voice.stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
      const wsProtocol = window.location.protocol === "https:" ? "wss:" : "ws:";
      const ws = new WebSocket(`${wsProtocol}//${window.location.host}/api/v1/chat/voice`);
      ws.binaryType = "arraybuffer";
      state.voice.ws = ws;
      ws.onopen = () => ws.send(JSON.stringify({ type: "session.start" }));
      ws.onmessage = (event) => handleVoiceServerEvent(event);
      ws.onerror = () => setChatVoiceStatus("Voice connection error", true, "error");
      ws.onclose = () => stopVoice();

      state.voice.audioContext = new AudioContext();
      state.voice.analyser = state.voice.audioContext.createAnalyser();
      state.voice.analyser.fftSize = 1024;
      state.voice.audioContext.createMediaStreamSource(state.voice.stream).connect(state.voice.analyser);
      state.voice.active = true;
      state.voice.speaking = false;
      $("chatVoiceBtn").textContent = "⏹";
      setChatVoiceStatus("Listening — speak naturally", true, "listening");
      monitorVoiceLevel();
    } catch (error) {
      setChatVoiceStatus("Microphone error: " + error.message, true, "error");
    }
  }

  function monitorVoiceLevel() {
    if (!state.voice.active || !state.voice.analyser) return;
    const samples = new Uint8Array(state.voice.analyser.fftSize);
    state.voice.analyser.getByteTimeDomainData(samples);
    const rms = Math.sqrt(samples.reduce((sum, sample) => {
      const normalized = (sample - 128) / 128;
      return sum + normalized * normalized;
    }, 0) / samples.length);
    const speakingNow = rms > 0.028;
    const now = performance.now();
    if (speakingNow) {
      state.voice.silenceSince = 0;
      if (!state.voice.speaking) {
        state.voice.speaking = true;
        state.voice.recorder = new MediaRecorder(state.voice.stream);
        state.voice.recorderChunks = [];
        state.voice.recorder.ondataavailable = (event) => {
          if (event.data.size) state.voice.recorderChunks.push(event.data);
        };
        state.voice.recorder.onstop = async () => {
          const blob = new Blob(state.voice.recorderChunks, { type: state.voice.recorder?.mimeType || "audio/webm" });
          state.voice.recorderChunks = [];
          if (blob.size && state.voice.ws?.readyState === WebSocket.OPEN) {
            state.voice.ws.send(await blob.arrayBuffer());
            state.voice.ws.send(JSON.stringify({ type: "audio.commit" }));
          }
        };
        state.voice.recorder.start();
        setChatVoiceStatus("Listening to you…", true, "speaking");
      }
    } else if (state.voice.speaking) {
      if (!state.voice.silenceSince) state.voice.silenceSince = now;
      if (now - state.voice.silenceSince >= 850) {
        state.voice.speaking = false;
        state.voice.recorder?.stop();
        setChatVoiceStatus("Thinking…", true, "thinking");
      }
    }
    state.voice.monitorFrame = requestAnimationFrame(monitorVoiceLevel);
  }

  function handleVoiceServerEvent(event) {
    if (typeof event.data !== "string") {
      // Binary assistant audio.
      const blob = new Blob([event.data], { type: "audio/wav" });
      state.voice.audio?.pause();
      state.voice.audio = new Audio(URL.createObjectURL(blob));
      setChatVoiceStatus("Responding…", true, "responding");
      void state.voice.audio.play();
      return;
    }
    const payload = JSON.parse(event.data);
    if (payload.type === "session.ready") state.currentSessionId = payload.session_id;
    if (payload.type === "user.transcript_final") appendMessageRow("user", payload.text, null);
    if (payload.type === "assistant.text_delta") {
      state.streamingText = (state.streamingText || "") + payload.text;
    }
    if (payload.type === "assistant.started") state.streamingText = "";
    if (payload.type === "assistant.completed") {
      if (state.streamingText) appendMessageRow("assistant", state.streamingText, null);
      state.streamingText = "";
      if (state.voice.active) setChatVoiceStatus("Listening — speak naturally", true, "listening");
    }
    if (payload.type === "assistant.interrupted") {
      state.streamingText = "";
    }
    if (payload.type === "error") {
      setChatVoiceStatus("Voice error: " + (payload.message || ""), true, "error");
    }
  }

  function stopVoice() {
    state.voice.active = false;
    if (state.voice.monitorFrame) cancelAnimationFrame(state.voice.monitorFrame);
    state.voice.monitorFrame = null;
    state.voice.recorder?.stop();
    state.voice.recorder = null;
    state.voice.stream?.getTracks().forEach((track) => track.stop());
    state.voice.stream = null;
    state.voice.audioContext?.close();
    state.voice.audioContext = null;
    state.voice.audio?.pause();
    try {
      state.voice.ws?.send(JSON.stringify({ type: "session.end" }));
      state.voice.ws?.close();
    } catch (_error) {
      // Already closed.
    }
    state.voice.ws = null;
    $("chatVoiceBtn").textContent = "🎤";
    setChatVoiceStatus("", false, null);
    if (state.currentSessionId) void selectSession(state.currentSessionId);
  }

  function parsePathCommand(text) {
    const match = String(text || "").trim().match(SLASH_PATH_COMMAND);
    return match ? match[1].trim() : null;
  }

  return { init, setMode, parsePathCommand };
});
