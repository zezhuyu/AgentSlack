const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const mainSource = fs.readFileSync(path.join(__dirname, "..", "macos", "main.js"), "utf8");

test("closing the macOS window destroys it instead of intercepting close", () => {
  const closeHandler = mainSource.match(/mainWindow\.on\('close',[\s\S]*?\n  \}\);/);

  assert.ok(closeHandler, "window close handler must exist");
  assert.doesNotMatch(closeHandler[0], /preventDefault|mainWindow\.hide/);
  assert.match(closeHandler[0], /app\.dock\.hide/);
  assert.match(mainSource, /mainWindow\.on\('closed', \(\) => \{ mainWindow = null; \}\)/);

  const allClosedHandler = mainSource.match(/app\.on\('window-all-closed',[\s\S]*?\n\}\);/);
  assert.ok(allClosedHandler, "window-all-closed must keep the background service alive");
  assert.doesNotMatch(allClosedHandler[0], /app\.quit/);
  assert.match(allClosedHandler[0], /background API server/);
});

test("application and background menus provide a full quit action", () => {
  assert.match(mainSource, /accelerator: 'CommandOrControl\+Q'/);
  assert.match(mainSource, /label: 'Quit Agent Slack Entirely'/);
  assert.match(mainSource, /function quitEntirely\(\)[\s\S]*?isQuitting = true;[\s\S]*?app\.quit\(\)/);
  assert.match(mainSource, /app\.on\('before-quit'[\s\S]*?backend\.kill\('SIGTERM'\)/);
});
