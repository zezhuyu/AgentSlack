const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");

const app = fs.readFileSync("static/app.js", "utf8");
const html = fs.readFileSync("static/index.html", "utf8");

test("project sidebar renders host sessions with the shared row and five-second poll", () => {
  assert.match(html, /id="projectHostSessionsSection"/);
  assert.match(app, /function renderHostSessionRow\(session\)/);
  assert.match(app, /list\.replaceChildren\(\.\.\.sessions\.map\(renderHostSessionRow\)\)/);
  assert.match(app, /mostSpecificServer\(session\.project_root\)\?\.server_id === server\.server_id/);
  assert.match(app, /!document\.hidden && \(state\.hostSessionsView \|\| state\.activeServerId\)\) loadAllSessions\(\)/);
});

test("project-root matching preserves case, handles separators, nesting, and overlapping roots", () => {
  const normalize = app.match(/function normalizeFolder\(folder\) \{([\s\S]*?)\n\}/)?.[1] || "";
  const contains = app.match(/function folderIsOnboarded\(folder, roots\) \{([\s\S]*?)\n\}/)?.[1] || "";
  assert.match(normalize, /replace\(\/\\\\\/g, "\/"\)/);
  assert.match(normalize, /replace\(\/\\\/\+\$\/, ""\)/);
  assert.match(contains, /candidate === root/);
  assert.match(contains, /candidate\.startsWith/);
  assert.match(app, /sort\(\(a, b\) => normalizeFolder\(b\.project_root\)\.length - normalizeFolder\(a\.project_root\)\.length\)/);
  assert.doesNotMatch(normalize, /toLocaleLowerCase/);
});

test("host hash links open sessions and switch to the owning project or global view", () => {
  assert.match(app, /params\.get\("host"\)/);
  assert.match(app, /await switchServer\(owner\.server_id\)/);
  assert.match(app, /await openHostSessionsServer\(\)/);
  assert.match(app, /window\.addEventListener\("hashchange", handleDeepLink\)/);
});
