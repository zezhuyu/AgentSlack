const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const app = fs.readFileSync('static/app.js', 'utf8');
const html = fs.readFileSync('static/index.html', 'utf8');

function extractFunction(name) {
  const start = app.indexOf(`function ${name}(`);
  assert.notEqual(start, -1);
  let depth = 0;
  for (let index = app.indexOf('{', start); index < app.length; index += 1) {
    if (app[index] === '{') depth += 1;
    if (app[index] === '}' && --depth === 0) return app.slice(start, index + 1);
  }
  throw new Error(`unterminated ${name}`);
}

function select() {
  return {
    options: [], value: '', replaceChildren() { this.options = []; },
    appendChild(option) { this.options.push(option); if (option.selected) this.value = option.value; },
  };
}

function harness() {
  const context = {
    state: { runnerOptions: {
      default_runner: 'codex', runners: [
        { id: 'codex', label: 'Codex', available: true, default_model: 'gpt-default', models: [{ id: 'gpt-x', label: '<Model>', description: 'safe description' }] },
        { id: 'claude', label: 'Claude Code', available: false, default_model: null, models: [{ id: 'sonnet', label: 'Sonnet', description: 'Claude model' }] },
      ],
    } },
    document: { createElement: () => ({ value: '', textContent: '', title: '', disabled: false, selected: false }) },
  };
  vm.runInNewContext(`${extractFunction('fillRunnerSelect')}\nglobalThis.fill = fillRunnerSelect;`, context);
  const agent = select(); const model = select();
  return { context, agent, model };
}

test('model options rebuild when the selected agent changes and unavailable agents are disabled', () => {
  const h = harness();
  h.context.fill(h.agent, h.model, 'codex', 'gpt-x');
  assert.deepEqual(h.model.options.map((option) => option.value), ['', 'gpt-x']);
  h.context.fill(h.agent, h.model, 'claude', 'sonnet');
  assert.deepEqual(h.model.options.map((option) => option.value), ['', 'sonnet']);
  assert.equal(h.agent.options[1].disabled, true);
  assert.equal(h.agent.options[1].textContent, 'Claude Code (not installed)');
  assert.equal(h.model.options[1].textContent, 'Sonnet');
  assert.equal(h.model.options[1].title, 'Claude model');
});

test('runner selectors exist for the composer and new chat dialog', () => {
  assert.match(html, /id="chatRunnerSelect"/);
  assert.match(html, /id="chatModelSelect"/);
  assert.match(html, /id="newChatRunnerSelect"/);
  assert.match(html, /id="newChatModelSelect"/);
});

test('chat choices persist immediately, refresh after a lock conflict, and lock with the header chip', () => {
  assert.match(app, /JSON\.stringify\(\{ runner: \$\("chatRunnerSelect"\)\.value \}\)/);
  assert.match(app, /JSON\.stringify\(\{ model: \$\("chatModelSelect"\)\.value \|\| null \}\)/);
  assert.match(app, /error\.status === 409\) await openChat\(state\.currentChatId\)/);
  assert.match(app, /const locked = Boolean\(chat\.settings_locked \|\| state\.runInProgress\)/);
  assert.match(app, /if \(locked\) \$\("chatMembers"\)\.textContent = `\$\{runnerInfo\?\.label[^\n]+chat\.model \|\| "Default"/);
  assert.match(app, /model: \$\("newChatModelSelect"\)\.value \|\| null/);
});
