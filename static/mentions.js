(function initMentions(root, factory) {
  const api = factory();
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (root) root.AgentSlackMentions = api;
}(typeof window !== "undefined" ? window : globalThis, function createMentionHelpers() {
  const mentionToken = /(^|\s)@([A-Za-z0-9_.-]+)/g;

  function queryAtCursor(value, cursor = value.length) {
    const prefix = String(value || "").slice(0, cursor);
    const match = prefix.match(/(^|\s)@([A-Za-z0-9_.-]*)$/);
    if (!match) return null;
    const query = match[2] || "";
    return {
      query,
      start: cursor - query.length - 1,
      end: cursor,
    };
  }

  function matchingAgents(agents, query) {
    const needle = String(query || "").toLocaleLowerCase();
    return (agents || []).filter((agent) => {
      const searchable = `${agent.agent_id} ${agent.name || ""} ${agent.title || ""}`.toLocaleLowerCase();
      return !needle || searchable.includes(needle);
    });
  }

  function insertMention(value, context, agentId) {
    const text = String(value || "");
    return `${text.slice(0, context.start)}@${agentId} ${text.slice(context.end)}`;
  }

  function resolveAgentIds(value, agents) {
    const available = new Set((agents || []).map((agent) => agent.agent_id));
    const resolved = [];
    for (const match of String(value || "").matchAll(mentionToken)) {
      const agentId = match[2];
      if (available.has(agentId) && !resolved.includes(agentId)) resolved.push(agentId);
    }
    return resolved;
  }

  return { queryAtCursor, matchingAgents, insertMention, resolveAgentIds };
}));
