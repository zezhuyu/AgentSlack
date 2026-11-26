const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const app = fs.readFileSync('static/app.js', 'utf8');

function extractFunction(name) {
  let start = app.indexOf(`function ${name}(`);
  if (start < 0) start = app.indexOf(`async function ${name}(`);
  assert.notEqual(start, -1, `${name} exists`);
  let depth = 0;
  for (let index = app.indexOf(') {', start) + 2; index < app.length; index += 1) {
    if (app[index] === '{') depth += 1;
    if (app[index] === '}' && --depth === 0) return app.slice(start, index + 1);
  }
  throw new Error(`unterminated ${name}`);
}

function harness() {
  class Element {
    constructor(tag) { this.tagName = tag; this.children = []; this.dataset = {}; this.className = ''; this.textContent = ''; this._html = ''; this.classList = { add: (name) => { if (!this.className.split(' ').includes(name)) this.className += ` ${name}`; } }; }
    appendChild(child) { this.children.push(child); return child; }
    querySelector(selector) {
      if (selector === 'time') return this.time || (this.time = new Element('time'));
      if (selector === '[data-retry]') return this.children.find((child) => child.dataset.retry) || null;
      if (selector === '.pending-assistant') return this.children.find((child) => child.className.includes('pending-assistant')) || null;
      if (selector === '.message-body') return this.body || (this.body = new Element('div'));
      return this.body || (this.body = new Element('div'));
    }
    set innerHTML(value) { this._html = value; }
    get innerHTML() { return this._html; }
  }
  const messages = new Element('section');
  messages.scrollTop = 0; messages.scrollHeight = 100;
  const persistence = { saves: 0 };
  const context = {
    state: { hostPendingPrompts: new Map(), hostSessionMessages: new Map(), hostSessionId: 's1', hostResumes: new Map(), hostResumeAliases: new Map() },
    $: () => messages,
    document: { createElement: (tag) => new Element(tag), querySelectorAll: (selector) => selector.includes('data-request-id') ? messages.children : [] },
    CSS: { escape: (value) => value },
    globalThis: { crypto: { randomUUID: () => 'request-1' } },
    saveHostPendingPrompts: () => { persistence.saves += 1; },
    initials: () => 'Y', renderMarkdown: (text) => `<p>${text}</p>`,
  };
  const names = ['appendPendingUserRow', 'countUserMessages', 'appendHostSessionPrompt', 'renderHostPendingPrompts', 'appendPendingAssistantText', 'markPendingPromptFailed', 'transferPendingHostPrompts'];
  vm.runInNewContext(`${names.map(extractFunction).join('\n')}\nObject.assign(globalThis, { ${names.join(', ')} });`, context);
  return { context, messages, persistence };
}

const user = (body) => ({ author_type: 'user', body });

test('a sent prompt appears immediately as a pending "sending…" bubble', () => {
  const { context, messages, persistence } = harness();
  context.appendHostSessionPrompt('s1', 'continue with step 2');
  const [row] = messages.children;
  assert.equal(row.className, 'message user pending');
  assert.equal(row.dataset.pending, '1');
  assert.match(row.innerHTML, /sending…/);
  assert.match(row.innerHTML, /continue with step 2/);
  assert.equal(context.state.hostPendingPrompts.get('s1').length, 1);
  assert.equal(context.state.hostPendingPrompts.get('s1')[0].request_id, 'request-1');
  assert.equal(persistence.saves, 1, 'pending state is persisted when the prompt is added');
});

test('the 5-second refresh redraws the prompt until the transcript contains it, then stops', () => {
  const { context, messages } = harness();
  context.appendHostSessionPrompt('s1', 'continue');
  messages.children.length = 0;                       // a refresh wipes the list and rebuilds it from the transcript
  context.renderHostPendingPrompts('s1', [user('earlier prompt')]);
  assert.equal(messages.children.length, 1, 'still pending: bubble is redrawn');

  messages.children.length = 0;
  context.renderHostPendingPrompts('s1', [user('earlier prompt'), user('continue')]);
  assert.equal(messages.children.length, 0, 'the transcript has it now: no duplicate bubble');
  assert.equal(context.state.hostPendingPrompts.has('s1'), false);
});

test('repeating an identical prompt is not confused with the one already in the transcript', () => {
  const { context, messages } = harness();
  context.state.hostSessionMessages.set('s1', [user('continue')]);       // already sent once before
  context.appendHostSessionPrompt('s1', 'continue');
  messages.children.length = 0;
  context.renderHostPendingPrompts('s1', [user('continue')]);            // still only the old one
  assert.equal(messages.children.length, 1);
  messages.children.length = 0;
  context.renderHostPendingPrompts('s1', [user('continue'), user('continue')]);
  assert.equal(messages.children.length, 0);
});

test('project-chat message is shown before the network round trip and only restored when not saved', () => {
  const send = extractFunction('sendMessage');
  assert.ok(send.indexOf('appendPendingUserRow(body, attachmentNote)') < send.indexOf('uploadAttachment'), 'bubble before uploads/POST');
  assert.ok(send.indexOf('appendPendingUserRow(body, attachmentNote)') < send.indexOf('/messages`'), 'bubble before the POST');
  assert.match(send, /let stored = false;[\s\S]*stored = true;[\s\S]*if \(!stored\) \{[\s\S]*pendingRow\.remove\(\);[\s\S]*\$\("messageInput"\)\.value = body/);
  const resume = extractFunction('resumeHostSessionInBackground');
  assert.doesNotMatch(resume, /state\.hostPendingPrompts\.delete\(sessionId\)/);
  assert.match(resume, /\.failed = true/);
  assert.match(resume, /markPendingPromptFailed\(row, item\)/);
  assert.match(app, /state\.hostSessionMessages\.set\(session\.session_id, session\.messages \|\| \[\]\);\s*renderHostPendingPrompts\(session\.session_id, session\.messages \|\| \[\]\)/);
});

test('a failed status poll does not end the wait; only a server-reported outcome does', async () => {
  let polls = 0;
  const calls = [];
  const context = {
    state: { activeServerId: null }, $: () => ({ textContent: '', placeholder: '', hidden: false, disabled: false }),
    renderHostResumeProgress: () => {}, setTimeout: (fn) => { fn(); return 0; }, Error, Promise, encodeURIComponent,
    api: async () => {
      calls.push(++polls);
      if (polls <= 3) throw new Error('network down');
      return polls === 4 ? { status: 'running', elapsed: 1 } : { status: 'completed', session_id: 's1' };
    },
  };
  vm.runInNewContext(`${app.slice(app.indexOf('const RESUME_POLL_TOLERANCE'), app.indexOf('// attach: the page'))}\nglobalThis.wait = waitForHostSessionResume;`, context);
  const done = await context.wait('s1');
  assert.equal(done.status, 'completed');
  assert.equal(calls.length, 5, 'three blips were retried');
});

test('a forgotten job is distinguished from a reported failure', async () => {
  const run = async (status) => {
    const context = { state: {}, renderHostResumeProgress: () => {}, setTimeout: (fn) => { fn(); return 0; }, Error, Promise, encodeURIComponent,
      api: async () => status };
    vm.runInNewContext(`${app.slice(app.indexOf('const RESUME_POLL_TOLERANCE'), app.indexOf('// attach: the page'))}\nglobalThis.wait = waitForHostSessionResume;`, context);
    return context.wait('s1').catch((error) => error);
  };
  assert.equal((await run({ status: 'unknown' })).lost, true);
  const failed = await run({ status: 'failed', error: 'boom' });
  assert.ok(!failed.lost);
  assert.equal(failed.message, 'boom');
});

test('streamed assistant text is attached below the correlated pending prompt', () => {
  const { context, messages } = harness();
  context.appendHostSessionPrompt('s1', 'hello');
  context.appendPendingAssistantText('request-1', 'partial response');
  assert.equal(messages.children[0].children.at(-1).textContent, 'partial response');
});

test('pending prompt and partial output move to the fork identity immediately', () => {
  const { context } = harness();
  context.state.hostSessionMessages.set('s1', [user('hello')]);
  context.appendHostSessionPrompt('s1', 'hello');
  context.state.hostSessionMessages.set('fork-1', [user('hello')]);
  context.transferPendingHostPrompts('s1', 'fork-1');
  const [pending] = context.state.hostPendingPrompts.get('fork-1');
  assert.equal(pending.request_id, 'request-1');
  assert.equal(pending.body, 'hello');
  assert.equal(pending.baseline, 1);
  assert.equal(context.state.hostSessionId, 'fork-1');
  assert.equal(context.state.hostPendingPrompts.has('s1'), false);
});

test('failed and cancelled prompts stay visible, offer retry, and restore composer text', () => {
  const { context, messages } = harness();
  context.appendHostSessionPrompt('s1', 'keep this message');
  const pending = context.state.hostPendingPrompts.get('s1')[0];
  pending.failed = true;
  context.markPendingPromptFailed(messages.children[0], pending);
  assert.match(messages.children[0].className, /failed/);
  assert.equal(messages.children[0].time.textContent, 'failed · kept here');
  assert.equal(messages.children[0].children.at(-1).textContent, 'Retry');
  assert.match(app, /if \(body\) \$\("messageInput"\)\.value = body/);
  assert.match(app, /item\.cancelled = Boolean\(error\.cancelled\)/);
});

test('resume polling uses a cursor, consumes fork ids, and waits at 400ms', () => {
  const wait = extractFunction('waitForHostSessionResume');
  assert.match(wait, /resume\?after=\$\{after\}/);
  assert.match(wait, /transferPendingHostPrompts\(sessionId, status\.session_id\)/);
  assert.match(wait, /setTimeout\(resolve, 400\)/);
  assert.match(wait, /status\.partial_text/);
});

test('resume status transfers the request before completion and renders its partial reply', async () => {
  const calls = [];
  const pending = { request_id: 'request-1', body: 'hello', baseline: 0 };
  const context = {
    state: { hostSessionId: 'source', hostPendingPrompts: new Map([['source', [pending]]]), hostSessionMessages: new Map(), hostResumes: new Map([['source', true]]), hostResumeAliases: new Map() },
    api: async (path) => {
      calls.push(path);
      return { status: 'completed', request_id: 'request-1', session_id: 'fork', events: [{ i: 0, type: 'assistant', text: 'partial answer' }], next: 1 };
    },
    saveHostPendingPrompts() {}, appendPendingAssistantText: (id, text) => { context.partial = [id, text]; },
    renderHostResumeProgress() {}, openHostSession: (id) => { context.opened = id; },
    setTimeout: (fn) => { fn(); return 0; }, Error, Promise, encodeURIComponent,
  };
  vm.runInNewContext(`${extractFunction('countUserMessages')}\n${extractFunction('transferPendingHostPrompts')}\n${app.slice(app.indexOf('const RESUME_POLL_TOLERANCE'), app.indexOf('// attach: the page'))}\nglobalThis.wait = waitForHostSessionResume;`, context);
  const result = await context.wait('source', 'request-1');
  assert.equal(result.status, 'completed');
  assert.equal(context.state.hostPendingPrompts.get('fork')[0].request_id, 'request-1');
  assert.equal(context.state.hostPendingPrompts.has('source'), false);
  assert.deepEqual(context.partial, ['request-1', 'partial answer']);
  assert.equal(context.opened, 'fork');
  assert.match(calls[0], /after=-1$/);
});

test('periodic host detail refresh is gated by summary changes and cached detail opens first', () => {
  const open = extractFunction('openHostSession');
  assert.ok(open.indexOf('renderHostSession(cached') < open.indexOf('await api('));
  const refresh = extractFunction('loadAllSessions');
  assert.match(refresh, /state\.currentChatUpdatedAt !== \(activeSession\.updated_at \|\| null\)/);
  assert.match(refresh, /activeSession\.size_bytes !== undefined/);
});
