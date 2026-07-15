const test = require("node:test");
const assert = require("node:assert/strict");
const markdown = require("../static/markdown.js");

test("renders common chat markdown", () => {
  const output = markdown.render([
    "## Market stage",
    "",
    "**Late expansion** with `moderate` confidence.",
    "",
    "- Earnings are growing",
    "- Breadth is narrowing",
  ].join("\n"));

  assert.match(output, /<h2>Market stage<\/h2>/);
  assert.match(output, /<strong>Late expansion<\/strong>/);
  assert.match(output, /<code>moderate<\/code>/);
  assert.match(output, /<ul><li>Earnings are growing<\/li><li>Breadth is narrowing<\/li><\/ul>/);
});

test("renders fenced code and financial tables", () => {
  const output = markdown.render([
    "| Signal | Value |",
    "|:---|---:|",
    "| GDP | 2.1% |",
    "",
    "```python",
    "stage = 'late-cycle'",
    "```",
  ].join("\n"));

  assert.match(output, /<table>/);
  assert.match(output, /text-align:right">Value/);
  assert.match(output, /<pre><code class="language-python">stage = &#39;late-cycle&#39;<\/code><\/pre>/);
});

test("escapes html and rejects unsafe links", () => {
  const output = markdown.render("<script>alert(1)</script> [click](javascript:alert(1)) [safe](https://example.com)");

  assert.doesNotMatch(output, /<script>/);
  assert.doesNotMatch(output, /href="javascript:/);
  assert.match(output, /&lt;script&gt;alert\(1\)&lt;\/script&gt;/);
  assert.match(output, /href="https:\/\/example.com"/);
});
