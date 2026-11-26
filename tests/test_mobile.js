const test = require("node:test");
const assert = require("node:assert/strict");
const mobile = require("../static/mobile.js");

test("keyboard is detected only when the visible viewport shrinks substantially", () => {
  assert.equal(mobile.isKeyboardOpen(844, 844), false);
  assert.equal(mobile.isKeyboardOpen(844, 800), false);
  assert.equal(mobile.isKeyboardOpen(844, 500), true);
});

test("the conversation drawer is limited to the single-pane phone breakpoint", () => {
  assert.equal(mobile.isDrawerViewport(382), true);
  assert.equal(mobile.isDrawerViewport(639), true);
  assert.equal(mobile.isDrawerViewport(640), false);
  assert.equal(mobile.isDrawerViewport({ innerWidth: 951 }), false);
});

test("textarea height grows with content but stops at its maximum", () => {
  assert.equal(mobile.fitHeight(44, 132), 44);
  assert.equal(mobile.fitHeight(300, 132), 132);
  assert.equal(mobile.fitHeight(-5, 132), 0);
});

test("near-bottom check keeps the newest message pinned", () => {
  assert.equal(mobile.isNearBottom({ scrollHeight: 1000, scrollTop: 700, clientHeight: 280 }), true);
  assert.equal(mobile.isNearBottom({ scrollHeight: 1000, scrollTop: 100, clientHeight: 280 }), false);
});
