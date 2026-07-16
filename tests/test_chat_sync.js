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
