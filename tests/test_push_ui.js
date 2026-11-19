const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const workerSource = fs.readFileSync('static/sw.js', 'utf8');
const pushSource = fs.readFileSync('static/push.js', 'utf8');

test('service worker shows declarative payload and routes notification clicks', async () => {
  const listeners = {};
  const shown = [];
  const focused = { focus: async () => focused, navigate: async (url) => { focused.url = url; return focused; } };
  const self = {
    navigator: { setAppBadge: async (n) => { self.badge = n; } },
    registration: { scope: 'https://example.test/app/', showNotification: async (...args) => shown.push(args) },
    clients: { matchAll: async () => [focused], openWindow: async (url) => ({ url }) },
    addEventListener: (name, cb) => { listeners[name] = cb; },
  };
  vm.runInNewContext(workerSource, { self, URL, fetch });
  let pending;
  listeners.push({ data: { json: () => ({ web_push: 8030, app_badge: 3, notification: { title: 'Done', body: 'Short', tag: 'run-1', navigate: 'https://example.test/app/#chat=c1' } }) }, waitUntil: (p) => { pending = p; } });
  await pending;
  assert.equal(shown[0][0], 'Done');
  assert.equal(shown[0][1].body, 'Short');
  assert.equal(shown[0][1].data.navigate, 'https://example.test/app/#chat=c1');
  assert.equal(self.badge, 3);
  let clickPromise;
  listeners.notificationclick({ notification: { data: { navigate: 'https://example.test/app/#chat=c1' }, close() {} }, waitUntil: (p) => { clickPromise = p; } });
  await clickPromise;
  assert.equal(focused.url, 'https://example.test/app/#chat=c1');
});

test('service worker falls back for empty payload and attempts renewed subscription', async () => {
  const listeners = {};
  let shown;
  const self = {
    navigator: {}, registration: { scope: 'https://example.test/app/', showNotification: async (title, options) => { shown = [title, options]; }, pushManager: { subscribe: async () => ({ endpoint: 'https://push.test/new' }) } },
    clients: { matchAll: async () => [], openWindow: async () => {} },
    addEventListener: (name, cb) => { listeners[name] = cb; },
  };
  const requests = [];
  const fakeFetch = async (url, options = {}) => {
    requests.push([String(url), options]);
    return { json: async () => ({ enabled: true, public_key: 'AQ' }) };
  };
  vm.runInNewContext(workerSource, { self, URL, fetch: fakeFetch, atob: () => '\x04' });
  let pending;
  listeners.push({ data: null, waitUntil: (p) => { pending = p; } });
  await pending;
  assert.equal(shown[0], 'Agent Slack');
  assert.match(shown[1].body, /agent run/i);
  listeners.pushsubscriptionchange({ waitUntil: (p) => { pending = p; } });
  await pending;
  assert.equal(requests.length, 2);
  assert.match(requests[1][0], /api\/push\/subscribe/);
});

test('push client reports state and supports enable, disable, and test operations', async () => {
  const subscription = { endpoint: 'https://push.test/id', unsubscribe: async () => true };
  const registration = { pushManager: { getSubscription: async () => subscription, subscribe: async () => subscription } };
  const calls = [];
  const root = {
    navigator: { userAgent: 'Test Browser', platform: 'Linux', serviceWorker: { register: async () => registration, getRegistration: async () => registration } },
    Notification: Object.assign(function Notification() {}, { permission: 'granted', requestPermission: async () => 'granted' }),
    PushManager: function PushManager() {}, location: { href: 'https://example.test/app/' }, matchMedia: () => ({ matches: true }),
    fetch: async (path, options = {}) => {
      calls.push([path, options]);
      return { ok: true, json: async () => path.endsWith('/config') ? { enabled: true, public_key: 'AQ' } : { ok: true } };
    },
  };
  vm.runInNewContext(pushSource, { window: root, URL, atob: () => '\x01', Uint8Array, Boolean, Promise, String, Error });
  assert.equal(await root.AgentSlackPush.state(), 'subscribed');
  await root.AgentSlackPush.enable();
  assert.equal(calls.some(([path]) => path === '/api/push/subscribe'), true);
  await root.AgentSlackPush.test();
  await root.AgentSlackPush.disable();
  assert.equal(calls.some(([path]) => path === '/api/push/unsubscribe'), true);
});

test('push UI wiring is included in index and settings; iOS install hint is present', () => {
  const html = fs.readFileSync('static/index.html', 'utf8');
  assert.match(html, /src="\.\/base-path\.js"><\/script>\s*<script src="\.\/push\.js"><\/script>/);
  assert.match(html, /id="pushStatus"/);
  assert.match(html, /id="pushEnableBtn"/);
  assert.match(html, /id="pushDisableBtn"/);
  assert.match(html, /id="pushTestBtn"/);
  assert.match(html, /Add to Home Screen/);
});

test('push client uses window.pushManager (Declarative Web Push) without a service worker', async () => {
  const subscription = { endpoint: 'https://push.test/id', unsubscribe: async () => true };
  const calls = [];
  const root = {
    navigator: { userAgent: 'Test Browser', platform: 'MacIntel', maxTouchPoints: 0 },
    Notification: Object.assign(function Notification() {}, { permission: 'granted', requestPermission: async () => 'granted' }),
    pushManager: { getSubscription: async () => subscription, subscribe: async () => subscription },
    location: { href: 'https://example.test/agent_slack/' }, matchMedia: () => ({ matches: false }),
    fetch: async (path, options = {}) => {
      calls.push([path, options]);
      return { ok: true, json: async () => path.endsWith('/config') ? { enabled: true, public_key: 'AQ' } : { ok: true } };
    },
  };
  vm.runInNewContext(pushSource, { window: root, URL, atob: () => '\x01', Uint8Array, Boolean, Promise, String, Error });
  assert.equal(root.AgentSlackPush.supported(), true);
  assert.equal(await root.AgentSlackPush.state(), 'subscribed');
  await root.AgentSlackPush.enable();
  assert.equal(calls.some(([path]) => path === '/api/push/subscribe'), true);
  await root.AgentSlackPush.disable();
  assert.equal(calls.some(([path]) => path === '/api/push/unsubscribe'), true);
});
