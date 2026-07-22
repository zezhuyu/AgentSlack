const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const composer = require("../static/composer.js");

const staticRoot = path.join(__dirname, "..", "static");
const html = fs.readFileSync(path.join(staticRoot, "index.html"), "utf8");
const styles = fs.readFileSync(path.join(staticRoot, "styles.css"), "utf8");
const appSource = fs.readFileSync(path.join(staticRoot, "app.js"), "utf8");

test("Return sends while Shift+Return and IME composition keep editing", () => {
  assert.equal(composer.shouldSendOnKeydown({ key: "Enter", shiftKey: false, isComposing: false }), true);
  assert.equal(composer.shouldSendOnKeydown({ key: "Enter", shiftKey: true, isComposing: false }), false);
  assert.equal(composer.shouldSendOnKeydown({ key: "Enter", shiftKey: false, isComposing: true }), false);
  assert.equal(composer.shouldSendOnKeydown({ key: "a", shiftKey: false, isComposing: false }), false);
  assert.match(appSource, /messageInput[\s\S]*?keydown[\s\S]*?shouldSendOnKeydown[\s\S]*?preventDefault\(\)[\s\S]*?sendMessage\(\)/);
});

test("mobile layout keeps People and Send available inside the viewport", () => {
  assert.match(html, /id="mobilePeopleBtn"/);
  assert.match(html, /id="mobileSidebarBackdrop"/);
  assert.match(html, /src="\/composer\.js"/);
  assert.match(styles, /height: 100dvh/);
  assert.match(styles, /safe-area-inset-bottom/);
  assert.match(styles, /\.sidebar-primary\.mobile-open/);
  assert.match(styles, /#sendBtn[\s\S]*?min-height: 44px/);
  assert.match(styles, /\.composer-actions input[\s\S]*?display: none/);
});

test("active runs and individual agent rows expose stop controls", () => {
  assert.match(html, /id="stopRunBtn"[\s\S]*?>Stop Run</);
  assert.match(appSource, /class="agent-stop-button"/);
  assert.match(appSource, /stopAgentTask\(taskId\)/);
  assert.match(appSource, /\/tasks\/\$\{encodeURIComponent\(taskId\)\}\/cancel/);
  assert.match(appSource, /stopCurrentRun/);
  assert.match(appSource, /event\.type === "agent_cancelled"/);
  assert.match(styles, /:not\(#stopRunBtn\)/);
});

test("user messages are aligned on the right", () => {
  assert.match(styles, /\.message\.user\s*\{[\s\S]*?grid-template-columns:\s*minmax\(0,\s*1fr\)\s+44px/);
  assert.match(styles, /\.message\.user \.message-avatar\s*\{[\s\S]*?grid-column:\s*2/);
  assert.match(styles, /\.message\.user \.message-body\s*\{[\s\S]*?justify-self:\s*end/);
  assert.match(styles, /\.message\.user \.message-body header\s*\{[\s\S]*?justify-content:\s*flex-end/);
});

test("plain projects can choose a CLI provider and model without API keys", () => {
  assert.match(html, /id="serverRunner"/);
  assert.match(html, /value="claude"[^>]*>Claude Code CLI/);
  assert.match(html, /value="codex"[^>]*>OpenAI Codex CLI/);
  assert.match(html, /id="serverModel"/);
  assert.doesNotMatch(html, /api[ -]?key/i);
  assert.match(appSource, /runner:\s*\$\("serverRunner"\)\.value/);
  assert.match(appSource, /model:\s*\$\("serverModel"\)\.value\.trim\(\)/);
});
