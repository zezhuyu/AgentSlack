(function initAgentSlackSync(root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.AgentSlackSync = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function createAgentSlackSync() {
  function chatListSignature(chats) {
    return JSON.stringify((chats || []).map((chat) => [
      chat.chat_id,
      chat.updated_at || "",
      chat.message_count || 0,
      chat.title || "",
      chat.kind || "",
    ]));
  }

  function shouldRefreshChat(summary, renderedUpdatedAt) {
    return Boolean(summary && summary.updated_at && summary.updated_at !== renderedUpdatedAt);
  }

  function activeRunForChat(runs, chatId) {
    return (runs || []).find((run) => run.chat_id === chatId && run.status === "running") || null;
  }

  function connectionLostMessage(error) {
    const detail = error?.message ? ` (${error.message})` : "";
    return `The live connection was lost${detail}. The agent continues in the background; status will reconnect automatically.`;
  }

  function streamErrorMessage(error) {
    if (error?.agentRunFailure) return `Agent run failed: ${error.message || "unknown error"}`;
    return connectionLostMessage(error);
  }

  function visibleMessages(messages) {
    return (messages || []).filter((message) => message.author_type !== "system");
  }

  return {
    chatListSignature,
    shouldRefreshChat,
    activeRunForChat,
    connectionLostMessage,
    streamErrorMessage,
    visibleMessages,
  };
});
