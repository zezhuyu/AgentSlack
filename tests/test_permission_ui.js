const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");

const app = fs.readFileSync("static/app.js", "utf8");
const html = fs.readFileSync("static/index.html", "utf8");
const css = fs.readFileSync("static/styles.css", "utf8");

test("permission cards poll while visible and expose the pending count", () => {
  assert.match(app, /window\.setInterval\(\(\) => \{ if \(!document\.hidden\) loadPermissions\(\); \}, 3000\)/);
  assert.match(app, /const payload = await api\("\/api\/permissions"\)/);
  assert.match(html, /id="permissionCount"/);
  assert.match(html, /id="permissionCards"/);
});

test("permission content is rendered as text and supports approve and deny", () => {
  assert.match(app, /summary\.textContent = permission\.status === "resolved"/);
  assert.match(app, /cwd\.textContent = permission\.cwd/);
  assert.match(app, /\[\["Approve", "allow", "permission-approve"\], \["Deny", "deny", "permission-deny"\]\]/);
  assert.match(app, /method: "POST", body: JSON\.stringify\(\{ decision \}\)/);
  assert.doesNotMatch(app.match(/function renderPermissions\(\) \{([\s\S]*?)\n\}/)?.[1] || "", /innerHTML/);
  assert.match(app, /\[404, 409\]\.includes\(error\.status\)/);
});

test("permission hash links focus the card immediately and survive hash changes", () => {
  assert.match(app, /params\.has\("permission"\)[\s\S]*?await loadPermissions\(\)/);
  assert.match(app, /permission\.request_id === focusId/);
  assert.match(app, /window\.addEventListener\("hashchange", handleDeepLink\)/);
  assert.match(css, /\.permission-card\.is-focused/);
});

test("AskUserQuestion renders options plus free text and sends answers", () => {
  assert.match(app, /function renderQuestionForm/);
  assert.match(app, /permission\.tool_name === "AskUserQuestion" && permission\.tool_input\?\.questions/);
  assert.match(app, /Or type your own answer/);
  assert.match(app, /decision: "allow", answers/);
  assert.doesNotMatch(app.match(/function renderQuestionForm[\s\S]*?\n\}/)?.[0] || "", /innerHTML/);
});

test("polling does not rebuild unchanged permission cards (keeps typed answers)", () => {
  const body = app.match(/function renderPermissions\(\) \{([\s\S]*?)\n\}/)?.[1] || "";
  assert.match(body, /signature === state\.permissionSignature\) return;/);
  assert.ok(body.indexOf("permissionSignature) return") < body.indexOf("replaceChildren"));
});
