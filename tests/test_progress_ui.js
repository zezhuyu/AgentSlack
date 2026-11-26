const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const app = fs.readFileSync("static/app.js", "utf8");

function extractFunction(name) {
  const start = app.indexOf(`function ${name}(`);
  assert.notEqual(start, -1, `${name} exists`);
  const open = app.indexOf("{", start);
  let depth = 0;
  for (let index = open; index < app.length; index += 1) {
    if (app[index] === "{") depth += 1;
    else if (app[index] === "}" && --depth === 0) return app.slice(start, index + 1);
  }
  assert.fail(`Could not find end of ${name}`);
}

function progressDom() {
  const cleared = [];
  class Element {
    constructor(tagName) { this.tagName = tagName; this.children = []; this.removed = false; }
    append(...children) { this.children.push(...children); }
    appendChild(child) { this.children.push(child); return child; }
    replaceChildren(...children) { this.children = children; }
    remove() { this.removed = true; }
    setAttribute() {}
  }
  const context = {
    document: { createElement: (tagName) => new Element(tagName) },
    clearInterval: (timer) => cleared.push(timer),
  };
  vm.runInNewContext([
    extractFunction("progressActions"),
    extractFunction("createHostProgressStep"),
    extractFunction("renderManagedProgress"),
    extractFunction("clearManagedProgress"),
    "globalThis.progress = { renderManagedProgress, clearManagedProgress };",
  ].join("\n"), context);
  return { ...context.progress, Element, cleared };
}

test("host session progress renders only for a running session and is rebuilt away on finish", () => {
  assert.match(app, /session\.status === "Running" && \(session\.progress\?\.length \|\| session\.subagents\?\.length\)\) renderHostProgress/);
  assert.match(app, /function renderHostProgress\(steps, subagents = \[\]\)/);
  assert.match(app, /\$\("messages"\)\.appendChild\(details\)/);
  assert.match(app, /const visible = allSteps\.slice\(-4\)/);
  assert.match(app, /function createHostProgressStep\(step\)/);
  assert.match(app, /progress-spinner/);
});

test("managed agent rows announce work and leave only final body on completion", () => {
  const start = app.match(/event\.type === "agent_started"\) \{([\s\S]*?)\n  \} else if/)?.[1] || "";
  const complete = app.match(/event\.type === "agent_completed"\) \{([\s\S]*?)\n  \} else if/)?.[1] || "";
  assert.match(start, /Working…/);
  assert.match(complete, /renderMarkdown\(event\.body !== undefined \? event\.body : stream\.rawText\)/);
  assert.match(complete, /state\.streamingRows\.delete\(taskId\)/);
});

test("managed progress uses bounded text-safe steps and clears elapsed timers", () => {
  assert.match(app, /event\.type === "agent_progress"/);
  assert.match(app, /stream\.progressSteps\.set\(event\.step_id/);
  assert.match(app, /\$\{String\(event\.label \|\| "Working"\)\}`\.slice\(0, 120\)/);
  assert.match(app, /function renderManagedProgress\(stream\)/);
  assert.match(app, /steps\.slice\(-4\)/);
  assert.match(app, /Show all \$\{steps\.length\} steps/);
  assert.match(app, /label\.textContent = step\.label \|\| "Working"/);
  assert.match(app, /function clearManagedProgress\(stream\)/);
  assert.match(app, /clearInterval\(stream\.elapsedTimer\)/);
  assert.match(app, /state\.streamingRows\.forEach\(clearManagedProgress\)/);
});

test("managed progress panel is separate from the answer draft and removed on terminal events", () => {
  assert.match(app, /messageBody\.insertBefore\(progressPanel, body\)/);
  for (const event of ["agent_completed", "agent_failed", "agent_cancelled"]) {
    const branch = app.match(new RegExp(`event\\.type === "${event}"\\) \\{([\\s\\S]*?)\\n  \\} else if`))?.[1] || "";
    assert.match(branch, /clearManagedProgress\(stream\)/);
  }
  assert.match(app, /catch \(error\) \{[\s\S]*?state\.streamingRows\.forEach\(clearManagedProgress\)/);
  assert.match(app, /event\.type === "run_completed"[\s\S]*?state\.streamingRows\.forEach\(clearManagedProgress\)/);
});

test("progress renderer shows the latest four, expands safely, and clears its timer", () => {
  const { renderManagedProgress, clearManagedProgress, Element, cleared } = progressDom();
  const hostile = '<img src=x onerror="alert(1)">';
  const stream = {
    progressList: new Element("ul"),
    progressPanel: new Element("details"),
    progressSteps: new Map(Array.from({ length: 6 }, (_, index) => [String(index), {
      kind: "tool", label: index === 5 ? hostile : `Step ${index}`, state: "done",
    }])),
    elapsedTimer: 17,
  };

  renderManagedProgress(stream);
  assert.equal(stream.progressList.children.length, 4);
  assert.equal(stream.progressShowAll.textContent, "Show all 6 steps");
  const renderedHostileLabel = stream.progressList.children[3].children.find((node) => node?.textContent === hostile);
  assert.ok(renderedHostileLabel, "hostile label is kept as a text node");
  assert.equal(renderedHostileLabel.innerHTML, undefined);
  stream.progressShowAll.onclick();
  assert.equal(stream.progressList.children.length, 6);
  assert.equal(stream.progressExpanded, true);

  clearManagedProgress(stream);
  assert.deepEqual(cleared, [17]);
  assert.equal(stream.elapsedTimer, null);
  assert.equal(stream.progressPanel, null);
});
