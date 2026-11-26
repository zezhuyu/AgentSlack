const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const root = path.join(__dirname, "..");
const staticRoot = path.join(root, "static");
const html = fs.readFileSync(path.join(staticRoot, "index.html"), "utf8");
const css = fs.readFileSync(path.join(staticRoot, "theme.css"), "utf8");
const mobile = fs.readFileSync(path.join(staticRoot, "mobile.js"), "utf8");
const app = fs.readFileSync(path.join(staticRoot, "app.js"), "utf8");
const manifest = JSON.parse(fs.readFileSync(path.join(staticRoot, "site.webmanifest"), "utf8"));

test("theme stylesheet follows the base stylesheet and contains both palettes", () => {
  const base = html.indexOf('href="./styles.css"');
  const theme = html.indexOf('href="./theme.css"');
  assert.ok(base >= 0 && theme > base);
  for (const token of ["#faf9f5", "#ffffff", "#f0eee6", "#e5e2d8", "#1f1e1d", "#6b6a64", "#c6613f", "#ede9dd", "#f3f1ea", "#262624", "#30302e", "#1f1e1d", "#3e3d3a", "#f3f2ec", "#a9a79e", "#d97757", "#3a3936"]) {
    assert.ok(css.toLowerCase().includes(token), `missing palette token ${token}`);
  }
  assert.match(css, /@media \(prefers-color-scheme: dark\)/);
});

test("adaptive UI surfaces and muted text follow the dark palette", () => {
  assert.match(css, /\.agent-ui-renderer\s*\{[^}]*--ui-border:\s*var\(--line\);[^}]*--ui-muted:\s*var\(--muted\);[^}]*--ui-surface:\s*var\(--surface\);[^}]*--ui-soft:\s*var\(--code-bg\);/s);
  assert.match(css, /\.agent-ui-table tbody tr:hover\s*\{\s*background:\s*var\(--hover-bg\);/);
  assert.match(css, /\.agent-ui-chart-track\s*\{\s*background:\s*var\(--line\);/);
  assert.match(css, /\.agent-ui-impact\s*\{\s*background:\s*color-mix\(/);
});

test("home-screen metadata and manifest use relative working icons", () => {
  assert.match(html, /name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover/);
  assert.match(html, /name="color-scheme" content="light dark"/);
  assert.match(html, /name="apple-mobile-web-app-capable" content="yes"/);
  assert.match(html, /name="mobile-web-app-capable" content="yes"/);
  assert.match(html, /name="apple-mobile-web-app-title" content="Agent Slack"/);
  assert.match(html, /name="apple-mobile-web-app-status-bar-style" content="default"/);
  assert.equal(manifest.name, "Agent Slack");
  assert.equal(manifest.short_name, "Agent Slack");
  assert.equal(manifest.display, "standalone");
  assert.deepEqual(manifest.display_override, ["standalone", "minimal-ui"]);
  assert.equal(manifest.orientation, "any");
  assert.equal(manifest.theme_color, "#faf9f5");
  assert.equal(manifest.background_color, "#faf9f5");
  for (const icon of manifest.icons) assert.ok(fs.existsSync(path.join(staticRoot, icon.src)), `missing ${icon.src}`);
  assert.ok(fs.existsSync(path.join(staticRoot, "icons/apple-touch-icon.png")));
});

test("responsive drawer, type, sheets, and reduced motion have the required contracts", () => {
  assert.match(mobile, /const DRAWER_MAX = 639/);
  assert.match(mobile, /function isDrawerViewport/);
  assert.match(css, /@media \(max-width: 639px\)/);
  assert.match(css, /width:\s*min\(88vw, 400px\)/);
  assert.match(css, /\.message-content\s*\{[^}]*font-size:\s*17px;\s*line-height:\s*1\.55/);
  assert.match(css, /\.composer textarea\s*\{[^}]*font-size:\s*17px/);
  assert.match(css, /dialog:not\(\.auth-dialog\)\s*\{[^}]*border-radius:\s*20px 20px 0 0/);
  assert.match(css, /@media \(prefers-reduced-motion: reduce\)/);
  const inputRules = [...css.matchAll(/(?:input|textarea|select)[^{]*\{([^}]*)\}/g)].map((m) => m[1]);
  assert.ok(inputRules.some((rule) => /font-size:\s*17px/.test(rule)));
});

test("agent names appear only when the visible author changes", () => {
  assert.match(app, /const showAuthor = !\["user", "system"\]\.includes\(message\.author_type\) && author !== previousAuthor;/);
  assert.match(css, /\.message:not\(\.show-author\) \.message-body header strong\s*\{\s*display:\s*none;/);
  assert.match(css, /\.message\.show-author \.message-body header strong/);
});
