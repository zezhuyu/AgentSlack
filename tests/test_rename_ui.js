const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const app = fs.readFileSync('static/app.js', 'utf8');
const html = fs.readFileSync('static/index.html', 'utf8');

function extractFunction(name) {
  const start = app.indexOf(`function ${name}(`);
  assert.notEqual(start, -1, `${name} exists`);
  let depth = 0;
  for (let index = app.indexOf('{', start); index < app.length; index += 1) {
    if (app[index] === '{') depth += 1;
    if (app[index] === '}' && --depth === 0) return app.slice(start, index + 1);
  }
  throw new Error(`unterminated ${name}`);
}

function harness(stateOverrides) {
  const listeners = {};
  const input = {
    value: '', maxLength: 0, className: '', attrs: {},
    setAttribute(k, v) { this.attrs[k] = v; }, focus() {}, select() {},
    addEventListener(type, fn) { listeners[type] = fn; },
  };
  const heading = {
    textContent: 'Old name', children: [],
    querySelector: (sel) => (sel === 'input' && heading.children.includes(input) ? input : null),
    replaceChildren(...nodes) { heading.children = nodes; },
  };
  const calls = [];
  const reloads = [];
  const context = {
    state: { hostSessionId: null, currentChatId: null, ...stateOverrides },
    $: (id) => (id === 'chatTitle' ? heading : { hidden: true }),
    document: { createElement: () => input },
    encodeURIComponent, JSON, console,
    api: async (path, options) => { calls.push([path, options]); return { title: JSON.parse(options.body).title }; },
    loadAllSessions: async () => reloads.push('sessions'),
    loadChats: async (opts) => reloads.push(['chats', opts]),
  };
  vm.runInNewContext(`${extractFunction('startTitleRename')}\nglobalThis.start = startTitleRename;`, context);
  return { context, heading, input, listeners, calls, reloads };
}

test('renaming a host session PATCHes its title route and refreshes the session list', async () => {
  const h = harness({ hostSessionId: 'abc 1' });
  h.context.start();
  assert.equal(h.input.value, 'Old name');
  assert.equal(h.input.maxLength, 60);
  h.input.value = '  Dark mode work ';
  h.listeners.keydown({ key: 'Enter', preventDefault() {} });
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(h.calls[0][0], '/api/host-sessions/abc%201/title');
  assert.equal(h.calls[0][1].method, 'PATCH');
  assert.deepEqual(JSON.parse(h.calls[0][1].body), { title: 'Dark mode work' });
  assert.equal(h.heading.textContent, 'Dark mode work');
  assert.equal(JSON.stringify(h.reloads), JSON.stringify(['sessions']));
});

test('renaming a chat uses the chat route; Escape, unchanged and empty names change nothing', async () => {
  const h = harness({ currentChatId: 'c1' });
  h.context.start();
  h.input.value = 'Quarterly review';
  h.listeners.blur();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(h.calls[0][0], '/api/chats/c1');
  assert.equal(JSON.stringify(h.reloads), JSON.stringify([['chats', { openFirst: false }]]));

  for (const [value, key] of [['Something else', 'Escape'], ['Old name', 'Enter'], ['   ', 'Enter']]) {
    const g = harness({ currentChatId: 'c1' });
    g.context.start();
    g.input.value = value;
    g.listeners.keydown({ key, preventDefault() {} });
    await new Promise((resolve) => setImmediate(resolve));
    assert.equal(g.calls.length, 0, `${key}/${JSON.stringify(value)} makes no request`);
    assert.equal(g.heading.textContent, 'Old name');
  }
});

test('the header has a rename button and titles go through setChatTitle', () => {
  assert.match(html, /<div class="chat-title-row"><h2 id="chatTitle">[^<]*<\/h2><button id="chatRenameBtn"/);
  assert.match(app, /\$\("chatRenameBtn"\)\.onclick = startTitleRename/);
  assert.match(app, /\$\("chatTitle"\)\.ondblclick = startTitleRename/);
  assert.doesNotMatch(app.replace(/function setChatTitle[\s\S]*?\n}\n/, ''), /\$\("chatTitle"\)\.textContent =/);
});
