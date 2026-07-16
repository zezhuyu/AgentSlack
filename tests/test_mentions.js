const test = require("node:test");
const assert = require("node:assert/strict");
const mentions = require("../static/mentions.js");

const agents = [
  { agent_id: "market_regime", name: "market_regime", title: "Market Regime Agent" },
  { agent_id: "risk-gate", name: "risk-gate", title: "Risk Gate" },
];

test("finds a mention query at the cursor and filters installed people", () => {
  const context = mentions.queryAtCursor("Please ask @mark", 16);
  assert.deepEqual(context, { query: "mark", start: 11, end: 16 });
  assert.deepEqual(
    mentions.matchingAgents(agents, context.query).map((agent) => agent.agent_id),
    ["market_regime"],
  );
});

test("inserts a stable agent id and resolves only installed agents", () => {
  const context = mentions.queryAtCursor("Ask @risk", 9);
  const message = mentions.insertMention("Ask @risk", context, "risk-gate");
  assert.equal(message, "Ask @risk-gate ");
  assert.deepEqual(
    mentions.resolveAgentIds(`${message}and @missing`, agents),
    ["risk-gate"],
  );
});

test("deduplicates repeated mentions in message order", () => {
  assert.deepEqual(
    mentions.resolveAgentIds("@market_regime compare with @risk-gate then @market_regime", agents),
    ["market_regime", "risk-gate"],
  );
});
