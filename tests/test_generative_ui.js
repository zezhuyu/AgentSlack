const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const renderer = require("../static/generative-ui.js");

const staticRoot = path.join(__dirname, "..", "static");
const appSource = fs.readFileSync(path.join(staticRoot, "app.js"), "utf8");
const html = fs.readFileSync(path.join(staticRoot, "index.html"), "utf8");
const styles = fs.readFileSync(path.join(staticRoot, "styles.css"), "utf8");

function stubDocument() {
  return {
    createElement(tag) {
      return {
        tag, children: [], attributes: {}, style: {}, listeners: {},
        append(...items) { this.children.push(...items); },
        setAttribute(name, value) { this.attributes[name] = value; },
        addEventListener(name, listener) { this.listeners[name] = listener; },
        textContent: "", className: "", value: "", min: "", max: "",
      };
    },
    createElementNS(_namespace, tag) { return this.createElement(tag); },
  };
}

test("validates versioned declarative component trees and enforces limits", () => {
  assert.equal(renderer.validate({
    schemaVersion: "1",
    root: { type: "Card", props: {}, children: [{ type: "Text", props: { text: "Safe" } }] },
  }).schemaVersion, "1");
  assert.throws(() => renderer.validate({ schemaVersion: "2", root: { type: "Text", props: {} } }));
  assert.throws(() => renderer.validate({
    schemaVersion: "1", root: { type: "Button", props: { label: "Run", onClick: "alert(1)" } },
  }), /unsupported properties/);
  assert.throws(() => renderer.validate({
    schemaVersion: "1", root: { type: "Text", props: { text: "x".repeat(70000) } },
  }), /large/);
});

test("renders only text nodes and safely degrades unknown components", () => {
  const previousDocument = global.document;
  global.document = stubDocument();
  try {
    const container = global.document.createElement("div");
    const ok = renderer.append(container, {
      schemaVersion: "1",
      root: { type: "Stack", props: {}, children: [
        { type: "Text", props: { text: "<img src=x onerror=alert(1)>" } },
        { type: "InjectedScript", props: { code: "alert(1)" } },
      ] },
    });
    assert.equal(ok, true);
    const region = container.children[0];
    assert.equal(region.children[0].children[0].textContent, "<img src=x onerror=alert(1)>");
    assert.match(region.children[0].children[1].textContent, /Unsupported component/);
    assert.equal(region.children[0].children[1].tag, "div");
  } finally {
    global.document = previousDocument;
  }
});

test("renders a bounded accessible spinner and non-button dashboard components", () => {
  const previousDocument = global.document;
  global.document = stubDocument();
  try {
    const container = global.document.createElement("div");
    assert.equal(renderer.append(container, {
      schemaVersion: "1",
      root: { type: "Grid", props: { columns: 2 }, children: [
        { type: "Spinner", props: { label: "Refreshing portfolio" } },
        { type: "PieChart", props: { title: "Mock allocation", data: [{ label: "Cash", value: 25 }, { label: "Equities", value: 75 }] } },
        { type: "Form", props: { submitLabel: "Refresh", action: { type: "submit_form" } }, children: [
          { type: "Select", props: { label: "Period", name: "period", options: [{ label: "One year", value: "1y" }] } },
        ] },
        { type: "Tabs", props: { tabs: [{ label: "Overview" }] }, children: [
          { type: "Text", props: { text: "Historical snapshot" } },
        ] },
      ] },
    }), true);
    const grid = container.children[0].children[0];
    assert.equal(grid.children[0].attributes.role, "status");
    assert.equal(grid.children[0].attributes["aria-label"], "Refreshing portfolio");
    assert.equal(grid.children[0].children[0].className, "agent-ui-spinner-mark");
    assert.match(styles, /@keyframes agent-ui-spin/);
    assert.match(styles, /prefers-reduced-motion:\s*reduce/);
    assert.equal(grid.children[1].className, "agent-ui-chart agent-ui-piechart");
    assert.equal(grid.children[2].tag, "form");
    assert.equal(grid.children[2].children[0].tag, "label");
    assert.equal(grid.children[3].children[0].children[0].textContent, "Overview");
  } finally {
    global.document = previousDocument;
  }
});

test("chart motion is declarative, bounded, and reduced-motion aware", () => {
  const previousDocument = global.document;
  global.document = stubDocument();
  try {
    const container = global.document.createElement("div");
    assert.equal(renderer.append(container, {
      schemaVersion: "1",
      root: { type: "LineChart", props: {
        title: "Demo trend", animation: "draw", data: [{ label: "Jan", value: 2 }, { label: "Feb", value: 3 }],
      } },
    }), true);
    const chart = container.children[0].children[0];
    const polyline = chart.children[1].children[0];
    assert.match(chart.className, /agent-ui-animation-draw/);
    assert.equal(polyline.attributes.class, "agent-ui-line-path");
    assert.equal(polyline.attributes.pathLength, "1");
    assert.match(styles, /agent-ui-draw-line/);
    assert.match(styles, /agent-ui-grow-bar/);
    assert.match(styles, /agent-ui-reveal-pie/);
    assert.match(styles, /prefers-reduced-motion:\s*reduce/);
    assert.throws(() => renderer.validate({
      schemaVersion: "1", root: { type: "LineChart", props: { animation: "grow" } },
    }), /chart animation/);
  } finally {
    global.document = previousDocument;
  }
});

test("completed and pending actions have persistent, clear status labels", () => {
  const previousDocument = global.document;
  global.document = stubDocument();
  try {
    const spec = { schemaVersion: "1", root: { type: "Button", props: {
      id: "node-1", label: "Send confirmation", action: { type: "run_analysis" },
    } } };
    const completed = global.document.createElement("div");
    renderer.append(completed, spec, {
      onAction() {},
      getActionStatus: () => "completed",
    });
    const buttonWrap = completed.children[0].children[0];
    assert.equal(buttonWrap.children[0].textContent, "Send confirmation · Run again");
    assert.equal(buttonWrap.children[1].textContent, "Completed · select to run again");

    const pending = global.document.createElement("div");
    renderer.append(pending, spec, { onAction() {}, isActionPending: () => true });
    assert.equal(pending.children[0].children[0].children[0].disabled, true);
    assert.equal(pending.children[0].children[0].children[0].textContent, "Send confirmation · Working…");
  } finally {
    global.document = previousDocument;
  }
});

test("historical messages and streamed agent completions render persisted UI", () => {
  assert.match(html, /src="\/generative-ui\.js"/);
  assert.match(appSource, /message\.metadata\?\.agent_ui\?\.content\?\.ui/);
  assert.match(appSource, /const finalUi = event\.ui \|\| stream\.uiSpec/);
  assert.match(appSource, /event\.type === "ui\.patch"/);
  assert.match(appSource, /event\.type === "ui\.completed"/);
  assert.match(appSource, /actionStatusForMessage/);
  assert.match(appSource, /const uiFence = stream\.rawText\.search/);
  assert.match(appSource, /Preparing interactive view/);
});
