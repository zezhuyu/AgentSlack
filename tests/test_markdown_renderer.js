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

test("renders arbitrary agent-system JSON as a readable card", () => {
  const output = markdown.render(JSON.stringify({
    workerId: "release-reviewer",
    status: "success",
    summary: "The release candidate passed validation.",
    changed_files: ["src/server.ts", "tests/server.test.ts"],
    verification: {
      testCount: 42,
      passed: true,
      notes: {},
    },
  }));

  assert.match(output, /class="json-card"/);
  assert.match(output, /class="json-field json-summary"/);
  assert.match(output, /Worker Id/);
  assert.match(output, /Changed Files/);
  assert.match(output, /Test Count/);
  assert.match(output, /The release candidate passed validation\./);
  assert.match(output, /<ul class="json-list">/);
  assert.match(output, />Yes</);
  assert.match(output, /No fields/);
});

test("renders a fenced top-level JSON array from any agent system", () => {
  const output = markdown.render([
    "```json",
    '[{"agent-name":"researcher","result":"[Report](https://example.com/report)"}]',
    "```",
  ].join("\n"));

  assert.match(output, /class="json-card"/);
  assert.match(output, /Agent Name/);
  assert.match(output, /release-reviewer|researcher/);
  assert.match(output, /href="https:\/\/example.com\/report"/);
});

test("keeps malformed JSON as escaped chat text", () => {
  const output = markdown.render('{"summary": "unfinished"');

  assert.doesNotMatch(output, /class="json-card"/);
  assert.match(output, /&quot;summary&quot;/);
});

test("renders markdown incrementally while an agent is streaming", () => {
  let stream = markdown.appendStream("", "Launching **Stage");
  assert.equal(stream.text, "Launching **Stage");
  assert.doesNotMatch(stream.html, /<strong>/);

  stream = markdown.appendStream(stream.text, " 1** specialists\n\n- ETF evidence");
  assert.equal(stream.text, "Launching **Stage 1** specialists\n\n- ETF evidence");
  assert.match(stream.html, /<strong>Stage 1<\/strong>/);
  assert.match(stream.html, /<ul><li>ETF evidence<\/li><\/ul>/);
});
