(function initAgentSlackInteractions(root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.AgentSlackInteractions = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function createAgentSlackInteractions() {
  function choiceForMessage(message) {
    const value = message?.metadata?.interaction;
    if (
      value?.kind !== "choice"
      || typeof value.question_id !== "string"
      || !value.question_id
      || typeof value.prompt !== "string"
      || !value.prompt.trim()
      || !Array.isArray(value.options)
      || value.options.length < 2
    ) {
      return null;
    }
    const options = value.options.map((option) => ({
      id: typeof option?.id === "string" ? option.id : "",
      label: typeof option?.label === "string" ? option.label : "",
      description: typeof option?.description === "string" ? option.description : "",
    }));
    if (options.some((option) => !option.id || !option.label)) return null;
    return {
      kind: "choice",
      questionId: value.question_id,
      prompt: value.prompt,
      multiple: Boolean(value.multiple),
      status: value.status === "answered" ? "answered" : "pending",
      selectedOptionIds: Array.isArray(value.selected_option_ids)
        ? value.selected_option_ids.filter((id) => typeof id === "string")
        : [],
      options,
    };
  }

  function nextSelection(choice, currentIds, optionId) {
    if (!choice?.multiple) return [optionId];
    const selected = new Set(currentIds || []);
    if (selected.has(optionId)) selected.delete(optionId);
    else selected.add(optionId);
    return [...selected];
  }

  function answerObjective(choice, optionIds) {
    const labels = (optionIds || []).map(
      (id) => choice.options.find((option) => option.id === id)?.label || id,
    );
    return `Response to "${choice.prompt}": ${labels.join(", ")}`;
  }

  return { choiceForMessage, nextSelection, answerObjective };
});
