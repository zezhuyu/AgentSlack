const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const root = path.join(__dirname, "..");
const app = fs.readFileSync(path.join(root, "static", "app.js"), "utf8");
const styles = fs.readFileSync(path.join(root, "static", "styles.css"), "utf8");

test("host session list refreshes every five seconds while visible", () => {
  assert.match(app, /state\.hostSessionsView && !document\.hidden\) loadAllSessions\(\);[\s\S]*?\}, 5000\)/);
});

test("unavailable host sessions remain readable and explain why they cannot resume", () => {
  assert.doesNotMatch(app, /button\.disabled = !session\.available/);
  assert.match(app, /button\.classList\.toggle\("unavailable", !session\.available\)/);
  assert.match(app, /Folder unavailable; this session is read-only/);
  assert.match(app, /const isRunning = session\.status === "Running" \|\| isResuming/);
});

test("host resume state is tracked independently by session id", () => {
  assert.match(app, /hostResumes: new Map\(\)/);
  assert.match(app, /hostResumes\.set\(sessionId, true\)/);
  assert.match(app, /hostResumes\.delete\(sessionId\)/);
  const sendBranch = app.match(/async function sendMessage\(\) \{([\s\S]*?)\n  if \(state\.runInProgress\)/)?.[1] || "";
  assert.doesNotMatch(sendBranch, /await waitForHostSessionResume\(sessionId\)/);
  assert.match(app, /void resumeHostSessionInBackground\(sessionId, body\)/);
});

test("resume status updates immediately in the list and open session header", () => {
  const sendBranch = app.match(/async function sendMessage\(\) \{([\s\S]*?)\n  if \(state\.runInProgress\)/)?.[1] || "";
  assert.match(sendBranch, /state\.hostResumes\.set\(sessionId, true\);\s*renderUnonboardedHostSessions\(\);\s*renderHostSessionDetails\(session\)/);
  assert.match(app, /const isResuming = session\.resuming \|\| state\.hostResumes\.has\(session\.session_id\);\s*const status = isResuming \? "Resuming…"/);
  assert.match(app, /state\.hostResumes\.delete\(sessionId\);\s*if \(state\.hostSessionsView\) renderUnonboardedHostSessions\(\);\s*await loadAllSessions\(\)/);
});

test("running sessions have an accessible pulsing dot with reduced motion support", () => {
  assert.match(app, /host-session-dot/);
  assert.match(styles, /\.host-session-dot[\s\S]*?animation: host-session-pulse/);
  assert.match(styles, /@media \(prefers-reduced-motion: reduce\)[\s\S]*?\.host-session-dot \{ animation: none; \}/);
});

test("host composer follows the open session availability and resume state", () => {
  assert.match(app, /function syncHostComposer\(session\)/);
  assert.match(app, /state\.hostResumes\.has\(session\.session_id\) \|\| session\.resuming/);
  assert.match(app, /!available \|\| Boolean\(resuming\)/);
  assert.match(app, /syncHostComposer\(session\)/);
});

test("host session polling reloads changed messages while preserving reading position", () => {
  assert.match(app, /state\.currentChatUpdatedAt !== \(activeSession\.updated_at \|\| null\)/);
  assert.match(app, /statusChanged = \(state\.hostMessagesStatus \|\| "History"\) !== \(activeSession\.status \|\| "History"\)/);
  assert.match(app, /openHostSession\(activeSession\.session_id, \{ preserveScroll: true \}\)/);
  assert.match(app, /messages\.scrollTop = preserveScroll && !followLatest \? previousScrollTop : messages\.scrollHeight/);
  assert.match(app, /if \(requestId !== state\.hostSessionOpenRequest \|\| !state\.hostSessionsView\) return/);
});

test("failed host session reloads are non-blocking and deduplicated until success", () => {
  assert.match(app, /hostSessionLastError: new Map\(\)/);
  const openSession = app.match(/async function openHostSession\(sessionId, \{ preserveScroll = false \} = \{\}\) \{([\s\S]*?)\n\}\n\nfunction appendHostSessionPrompt/)?.[1] || "";
  const catchBranch = openSession.match(/\} catch \(error\) \{([\s\S]*?)\n  \}/)?.[1] || "";
  assert.doesNotMatch(catchBranch, /alert\s*\(/);
  assert.match(catchBranch, /state\.hostSessionLastError\.get\(sessionId\) !== message/);
  assert.match(catchBranch, /state\.hostSessionLastError\.set\(sessionId, message\)/);
  assert.match(openSession, /state\.hostSessionLastError\.delete\(sessionId\)/);
  assert.match(catchBranch, /row\.setAttribute\("role", "status"\)/);
  assert.match(catchBranch, /item\.dataset\.hostSessionError === sessionId/);
  assert.doesNotMatch(catchBranch, /messages\.innerHTML\s*=/);
  assert.match(catchBranch, /console\.warn\("Unable to load host session"/);
});
