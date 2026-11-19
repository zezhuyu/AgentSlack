const test = require("node:test");
const assert = require("node:assert/strict");
const { addPrefix, derivePrefix } = require("../static/base-path.js");

const location = new URL("https://example.test/agent_slack/");
const scoped = (value) => addPrefix(value, "/agent_slack", location);

test("derives a mount prefix from the script URL and leaves root deployments alone", () => {
  assert.equal(derivePrefix("https://example.test/tools/agent_slack/base-path.js"), "/tools/agent_slack");
  assert.equal(derivePrefix("https://example.test/base-path.js"), "");
  assert.equal(addPrefix("/api/health", "", location), "/api/health");
});

test("prefixes root-relative HTTP and WebSocket URLs once", () => {
  assert.equal(scoped("/api/health"), "/agent_slack/api/health");
  assert.equal(scoped("wss://example.test/api/v1/chat/voice"), "wss://example.test/agent_slack/api/v1/chat/voice");
  assert.equal(addPrefix("ws://example.test/api/v1/chat/voice", "/agent_slack", new URL("http://example.test/")), "ws://example.test/agent_slack/api/v1/chat/voice");
  assert.equal(scoped("/agent_slack/api/health"), "/agent_slack/api/health");
  assert.equal(scoped("https://other.test/api/health"), "https://other.test/api/health");
});

test("does not rewrite opaque or same-origin non-root URL values", () => {
  for (const value of ["data:text/plain,hi", "blob:https://example.test/id", "#section", "//cdn.test/app.js"]) {
    assert.equal(scoped(value), value);
  }
  assert.equal(scoped("https://example.test/agent_slack/api/health"), "https://example.test/agent_slack/api/health");
});
