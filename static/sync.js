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

  return { chatListSignature, shouldRefreshChat };
});
