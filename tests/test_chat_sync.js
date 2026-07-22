const test = require("node:test");
const assert = require("node:assert/strict");
const sync = require("../static/sync.js");

test("chat list signature changes only for visible sidebar state", () => {
  const chat = {
    chat_id: "chat-1",
    updated_at: "2026-07-15T01:00:00Z",
    message_count: 2,
    title: "Research",
    kind: "direct",
  };
  const signature = sync.chatListSignature([chat]);

  assert.equal(sync.chatListSignature([{ ...chat }]), signature);
  assert.notEqual(sync.chatListSignature([{ ...chat, message_count: 3 }]), signature);
  assert.notEqual(sync.chatListSignature([{ ...chat, updated_at: "2026-07-15T01:01:00Z" }]), signature);
});

test("open chat refreshes only when its persisted revision changes", () => {
  const summary = { chat_id: "chat-1", updated_at: "new-revision" };

  assert.equal(sync.shouldRefreshChat(summary, "old-revision"), true);
  assert.equal(sync.shouldRefreshChat(summary, "new-revision"), false);
  assert.equal(sync.shouldRefreshChat(null, "old-revision"), false);
});

test("reconnecting clients select the active run for the open chat", () => {
  const runs = [
    { run_id: "run-other", chat_id: "chat-2", status: "running" },
    { run_id: "run-current", chat_id: "chat-1", status: "running" },
  ];

  assert.equal(sync.activeRunForChat(runs, "chat-1").run_id, "run-current");
  assert.equal(sync.activeRunForChat(runs, "chat-3"), null);
});

test("transport loss is not presented as an agent failure", () => {
  const message = sync.streamErrorMessage(new Error("network connection was lost"));

  assert.match(message, /connection/i);
  assert.match(message, /continues in the background/i);
  assert.doesNotMatch(message, /agent run failed/i);
});

test("a backend error event remains an agent failure", () => {
  const error = new Error("runner exited");
  error.agentRunFailure = true;

  assert.match(sync.streamErrorMessage(error), /agent run failed/i);
});

test("system messages stay internal and are excluded from visible chat history", () => {
  const messages = [
    { message_id: "system", author_type: "system", body: "Meeting scheduled" },
    { message_id: "user", author_type: "user", body: "Run it" },
    { message_id: "agent", author_type: "agent", body: "Done" },
  ];

  assert.deepEqual(sync.visibleMessages(messages).map((message) => message.message_id), [
    "user",
    "agent",
  ]);
});
