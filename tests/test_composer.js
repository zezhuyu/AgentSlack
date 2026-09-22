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

test("attachment previews recognize glTF/glb 3D models", () => {
  assert.match(appSource, /glb:\s*"model\/gltf-binary"/);
  assert.match(appSource, /gltf:\s*"model\/gltf\+json"/);
  assert.match(appSource, /model-viewer/);
});

test("Blender model links preview their sibling OBJ export", () => {
  assert.match(appSource, /blend:\s*"model\/blender"/);
  assert.ok(appSource.includes('rawPath.replace(/\\.blend$/i, ".obj")'));
  assert.match(appSource, /OBJLoader/);
  assert.match(appSource, /OrbitControls/);
  assert.match(appSource, /message-reference-bubbles/);
  assert.match(appSource, /Show sources/);
  assert.match(appSource, /referenceBubbles\.hidden = true/);
  assert.match(styles, /\.message-reference-bubbles\[hidden\]\s*\{\s*display:\s*none/);
  assert.match(appSource, /three@0\.180\.0/);
  assert.match(appSource, /attachment\.role === "reference"/);
  assert.match(styles, /\.attachment-preview-model[\s\S]*?background: #111827/);
  assert.match(styles, /\.attachment-preview-obj[\s\S]*?touch-action: none/);
});

test("local playable media renders as previews instead of source bubbles", () => {
  assert.match(appSource, /contentType\.startsWith\("audio\/"\)/);
  assert.match(appSource, /contentType\.startsWith\("video\/"\)/);
  assert.match(appSource, /contentType\.startsWith\("image\/"\)/);
  assert.match(appSource, /supportsInlinePreview[\s\S]*?appendPreviewMedia/);
  assert.match(appSource, /supportsInlinePreview[\s\S]*?else \{[\s\S]*?message-reference-bubble/);
});

test("mobile layout keeps People and Send available inside the viewport", () => {
  assert.match(html, /id="mobilePeopleBtn"/);
  assert.match(html, /id="mobileSidebarBackdrop"/);
  assert.match(html, /src="\/composer\.js"/);
  assert.match(styles, /height: 100dvh/);
  assert.match(styles, /safe-area-inset-bottom/);
  assert.match(styles, /\.sidebar-primary\.mobile-open/);
  assert.match(styles, /\.send-button[\s\S]*?min-height: 44px/);
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

test("navigation stays available while an agent run streams", () => {
  assert.doesNotMatch(appSource, /button\.disabled = !server\.available \|\| state\.runInProgress/);
  assert.doesNotMatch(appSource, /if \(serverId === state\.activeServerId \|\| state\.runInProgress\) return/);
  assert.match(appSource, /renderStreamEvent\(event, chatId\)/);
  assert.match(appSource, /currentRunChatId/);
});

test("user messages are aligned on the right", () => {
  assert.match(styles, /\.message\.user\s*\{[\s\S]*?grid-template-columns:\s*minmax\(0,\s*1fr\)\s+44px/);
  assert.match(styles, /\.message\.user \.message-avatar\s*\{[\s\S]*?grid-column:\s*2/);
  assert.match(styles, /\.message\.user \.message-body\s*\{[\s\S]*?justify-self:\s*end/);
  assert.match(styles, /\.message\.user \.message-body header\s*\{[\s\S]*?justify-content:\s*flex-end/);
});

test("server logos fit inside the workspace button without cropping", () => {
  assert.match(styles, /\.server-btn img\s*\{[\s\S]*?object-fit:\s*contain/);
});

test("plain projects can choose a CLI provider and model without API keys", () => {
  assert.match(html, /id="serverRunner"/);
  assert.match(html, /value="claude"[^>]*>Claude Code CLI/);
  assert.match(html, /value="codex"[^>]*>OpenAI Codex CLI/);
  assert.match(html, /id="serverModel"/);
  assert.doesNotMatch(appSource, /api[ -]?key/i);
  assert.match(appSource, /runner:\s*\$\("serverRunner"\)\.value/);
  assert.match(appSource, /model:\s*\$\("serverModel"\)\.value\.trim\(\)/);
});

test("composer uploads selected files before posting attachment ids", () => {
  assert.doesNotMatch(html, /id="attachBtn"/);
  assert.match(html, /id="pendingAttachments"/);
  assert.match(appSource, /\/api\/v1\/uploads/);
  assert.match(appSource, /X-Agent-Slack-Filename/);
  assert.match(appSource, /attachment_ids:\s*uploads\.map/);
  assert.match(appSource, /message\.metadata\?\.attachments/);
  assert.match(appSource, /dataTransfer\?\.files/);
  assert.match(appSource, /selectAttachments\(event\.dataTransfer\.files\)/);
  assert.match(styles, /message-input-wrap\.drag-active/);
  assert.match(styles, /\.pending-attachments/);
  assert.match(styles, /\.message-attachments/);
});

test("created files and voice input have client actions", () => {
  assert.match(html, /id="voiceBtn"/);
  assert.match(html, /class="message-input-row"[\s\S]*id="voiceBtn"[\s\S]*id="messageInput"/);
  assert.match(appSource, /voice\/turns/);
  assert.match(appSource, /X-Agent-Slack-Voice-Chat/);
  assert.match(appSource, /speech_ready/);
  assert.match(appSource, /getUserMedia\(\{\s*audio:/);
  assert.match(appSource, /requestAnimationFrame\(monitorVoiceLevel\)/);
  assert.match(appSource, /getByteTimeDomainData/);
  assert.match(appSource, /stopContinuousVoice\(\)/);
  assert.doesNotMatch(appSource, /messageInput\"\)\.value = result\.text/);
  assert.doesNotMatch(appSource, /SpeechRecognition/);
  assert.match(appSource, /attachment\.url \|\|/);
});
