(function initComposer(root, factory) {
  const api = factory();
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (root) root.AgentSlackComposer = api;
}(typeof window !== "undefined" ? window : globalThis, function createComposerHelpers() {
  function shouldSendOnKeydown(event) {
    return event?.key === "Enter" && !event.shiftKey && !event.isComposing;
  }

  return { shouldSendOnKeydown };
}));
