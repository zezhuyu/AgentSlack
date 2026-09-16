const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const interactions = require("../static/interactions.js");

const staticRoot = path.join(__dirname, "..", "static");
const html = fs.readFileSync(path.join(staticRoot, "index.html"), "utf8");
const appSource = fs.readFileSync(path.join(staticRoot, "app.js"), "utf8");
const styles = fs.readFileSync(path.join(staticRoot, "styles.css"), "utf8");

test("normalizes dynamic choices from message metadata without built-in labels", () => {
  const message = {
    metadata: {
      interaction: {
        kind: "choice",
        question_id: "deploy",
        prompt: "Choose a deployment window",
        multiple: false,
        status: "pending",
        options: [
          { id: "tonight", label: "Tonight", description: "Deploy after 8 PM." },
          { id: "tomorrow", label: "Tomorrow morning" },
        ],
      },
    },
  };

  assert.deepEqual(interactions.choiceForMessage(message), {
    kind: "choice",
    questionId: "deploy",
    prompt: "Choose a deployment window",
    multiple: false,
    status: "pending",
    selectedOptionIds: [],
    options: [
      { id: "tonight", label: "Tonight", description: "Deploy after 8 PM." },
      { id: "tomorrow", label: "Tomorrow morning", description: "" },
    ],
  });
});

test("rejects malformed choices instead of inventing fallback options", () => {
  assert.equal(interactions.choiceForMessage({ metadata: {} }), null);
  assert.equal(interactions.choiceForMessage({
    metadata: {
      interaction: {
        kind: "choice",
        question_id: "bad",
        prompt: "Choose",
        options: [],
      },
    },
  }), null);
});

test("single and multiple selections use backend-provided option ids", () => {
  const choice = {
    multiple: false,
    options: [{ id: "a" }, { id: "b" }],
  };
  assert.deepEqual(interactions.nextSelection(choice, [], "b"), ["b"]);
  assert.deepEqual(interactions.nextSelection({ ...choice, multiple: true }, ["a"], "b"), ["a", "b"]);
  assert.deepEqual(interactions.nextSelection({ ...choice, multiple: true }, ["a", "b"], "a"), ["b"]);
});

test("desktop chat renders and submits backend-driven question cards", () => {
  assert.match(html, /src="\/interactions\.js"/);
  assert.match(appSource, /appendChoiceInteraction/);
  assert.match(appSource, /questions\/\$\{encodeURIComponent\(choice\.questionId\)\}\/answer/);
  assert.match(appSource, /option_ids:\s*optionIds/);
  assert.match(appSource, /runPayloadForMessage\(currentChat,\s*objective,\s*\[\]\)/);
  assert.match(styles, /\.choice-card/);
  assert.doesNotMatch(appSource, /Approve this task|Request changes/);
});
