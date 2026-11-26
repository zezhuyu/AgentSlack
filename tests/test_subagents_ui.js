const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");

const source = fs.readFileSync(path.join(__dirname, "..", "static", "app.js"), "utf8");
function section(start, end) { return source.slice(source.indexOf(start), source.indexOf(end, source.indexOf(start))); }

class Element {
  constructor(tag = "div") { this.tagName = tag; this.children = []; this.className = ""; this.textContent = ""; this.dataset = {}; this.classList = { toggle() {}, add() {}, remove() {} }; }
  append(...children) { this.children.push(...children); }
  appendChild(child) { this.children.push(child); return child; }
  replaceChildren(...children) { this.children = children; }
  setAttribute() {}
  get innerText() { return [this.textContent, ...this.children.map((child) => typeof child === "string" ? child : child.innerText || child.textContent)].join(" "); }
}
function harness(api = async () => ({})) {
  const messages = new Element("main");
  const input = new Element("textarea"), send = new Element("button");
  const state = { hostSessionId: null, hostSubagentId: null, hostResumes: new Set(), activeServerId: "server", hostSessionsView: false };
  const document = { createElement: (tag) => new Element(tag), createTextNode: (text) => String(text) };
  const context = { state, document, api, console, encodeURIComponent,
    $: (id) => ({ messages, messageInput: input, sendBtn: send }[id]),
    openHostSession: async () => {}, renderHostProgress: () => {} };
  const definitions = section("function renderHostSessionRow(", "function compareHostSessions(") +
    section("function renderHostSubagents(", "function appendHostSessionPrompt(");
  vm.createContext(context);
  vm.runInContext(`${definitions}\nthis.renderHostSessionRow = renderHostSessionRow; this.renderHostSubagents = renderHostSubagents; this.openHostSubagent = openHostSubagent;`, context);
  return { context, messages, input, send };
}

test("host session chip uses counts and running pulse state", () => {
  const { context } = harness();
  const row = context.renderHostSessionRow({ session_id: "p", title: "Parent", agent_type: "codex", project_name: "repo", subagents_total: 3, subagents_running: 1 });
  assert.match(row.innerText, /3 agents · 1 running/);
  const chip = row.children.flatMap((node) => node.children || []).find((node) => String(node.className || "").includes("subagent-chip"));
  assert.match(chip.className, /is-running/);
});

test("agent cards expand while running and collapse after completion", () => {
  const { context, messages } = harness();
  context.renderHostSubagents({ session_id: "p", subagents: [{ id: "c", nickname: "Scout", role: "explore", status: "running", task: "find code", current_step: "Searching" }] });
  assert.equal(messages.children[0].open, true);
  assert.match(messages.innerText, /Scout · explore/);
  assert.match(messages.innerText, /Searching/);
  context.renderHostSubagents({ session_id: "p", subagents: [{ id: "c", nickname: "Scout", role: "explore", status: "done", task: "find code", result_preview: "<img onerror=bad>" }] });
  assert.equal(messages.children[1].open, false);
  assert.match(messages.children[1].innerText, /<img onerror=bad>/);
});

test("sub-agent drill-in is read-only and renders transcript via textContent", async () => {
  const { context, messages, input, send } = harness(async () => ({ nickname: "Scout", role: "explore", status: "done", messages: [{ author_type: "assistant", body: "<script>alert(1)</script>" }] }));
  await context.openHostSubagent("parent", "child");
  assert.equal(input.disabled, true);
  assert.equal(send.disabled, true);
  const transcriptRow = messages.children.find((node) => node.className === "message assistant");
  assert.equal(transcriptRow.children[0].textContent, "<script>alert(1)</script>");
  assert.equal(Object.hasOwn(transcriptRow.children[0], "innerHTML"), false);
  assert.equal(messages.children[0].textContent, "← Back to parent");
  assert.match(source, /if \(state\.hostSubagentId\) \{\s*await openHostSubagent\(activeSession\.session_id, state\.hostSubagentId\);/);
});
