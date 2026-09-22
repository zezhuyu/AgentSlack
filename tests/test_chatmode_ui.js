const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const chatMode = require("../static/chatmode.js");

const staticRoot = path.join(__dirname, "..", "static");
const html = fs.readFileSync(path.join(staticRoot, "index.html"), "utf8");
const appSource = fs.readFileSync(path.join(staticRoot, "app.js"), "utf8");
const stylesSource = fs.readFileSync(path.join(staticRoot, "styles.css"), "utf8");

test("index.html has a Chat/Agent mode switch and a Chat mode pane", () => {
  assert.match(html, /id="chatModeTabBtn"/);
  assert.match(html, /id="agentModeTabBtn"/);
  assert.match(html, /id="chatModePane"/);
  assert.match(html, /id="agentModePane"/);
  assert.match(html, /id="chatModeSessions"/);
  assert.match(html, /id="chatModeMessages"/);
  assert.match(html, /id="chatVoiceBtn"/);
});

test("index.html loads chatmode.js before app.js", () => {
  const chatModeIndex = html.indexOf('src="/chatmode.js"');
  const appIndex = html.indexOf('src="/app.js"');
  assert.ok(chatModeIndex > -1 && appIndex > -1 && chatModeIndex < appIndex);
});

test("app.js wires AgentSlackChatMode.init with the shared helpers", () => {
  assert.match(appSource, /AgentSlackChatMode\?\.init\(\{[^}]*api[^}]*streamApi[^}]*renderMarkdown[^}]*\}\)/);
});

test("chatmode module exposes init and setMode", () => {
  assert.equal(typeof chatMode.init, "function");
  assert.equal(typeof chatMode.setMode, "function");
});

test("chatmode module never references write/execution endpoints", () => {
  const source = fs.readFileSync(path.join(staticRoot, "chatmode.js"), "utf8");
  assert.doesNotMatch(source, /run-stream|\/api\/v1\/chats\//);
});

test("hidden mode panes actually collapse: [hidden] beats the unconditional flex rule", () => {
  // .mode-pane sets display:flex unconditionally; without an explicit [hidden]
  // override, the `hidden` attribute (display:none) loses the cascade and both
  // the Chat and Agent panes render on top of each other at once.
  assert.match(stylesSource, /\.mode-pane\[hidden\]\s*\{[^}]*display:\s*none/);
});

test("chatmode's /path command parses a trailing path and rejects everything else", () => {
  assert.equal(chatMode.parsePathCommand("/path src/app.js"), "src/app.js");
  assert.equal(chatMode.parsePathCommand("/path  ./notes.md  "), "./notes.md");
  assert.equal(chatMode.parsePathCommand("what about /path here"), null);
  assert.equal(chatMode.parsePathCommand("/pathological"), null);
  assert.equal(chatMode.parsePathCommand(""), null);
});

test("chatmode.js wires drag-and-drop attachment handling on the composer", () => {
  const source = fs.readFileSync(path.join(staticRoot, "chatmode.js"), "utf8");
  assert.match(source, /addEventListener\(["']drop["']/);
  assert.match(source, /dataTransfer\?\.files/);
  assert.match(source, /uploadAttachment/);
});

test("index.html wires the Agent mode /path attachment hint", () => {
  assert.match(html, /\/path/);
});

test("app.js supports a /path slash command that attaches a file instead of sending it", () => {
  assert.match(appSource, /SLASH_PATH_COMMAND\s*=\s*\/\^\\\/path/);
  assert.match(appSource, /attachPathToComposer/);
  assert.match(appSource, /api\/v1\/files\?path=/);
});
