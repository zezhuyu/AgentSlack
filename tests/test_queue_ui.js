const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const app = fs.readFileSync('static/app.js', 'utf8');

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

class Element {
  constructor(tagName) { this.tagName = tagName; this.children = []; this.className = ''; this.textContent = ''; }
  append(...children) { this.children.push(...children); }
  appendChild(child) { this.children.push(child); return child; }
}

test('queued terminal messages render as text-only bubbles under the transcript', () => {
  const messages = new Element('div');
  const context = { document: { createElement: (tag) => new Element(tag) }, $: () => messages };
  vm.runInNewContext(`${extractFunction('renderHostQueue')}\nglobalThis.renderHostQueue = renderHostQueue;`, context);
  const hostile = '<img src=x onerror="alert(1)">';
  context.renderHostQueue([{ content: 'also update the readme' }, { content: hostile }]);
  const [section] = messages.children;
  assert.equal(section.className, 'host-queue');
  assert.equal(section.children[0].textContent, '2 messages queued');
  const bubble = section.children[2];
  assert.equal(bubble.children[0].textContent, 'Queued');
  assert.equal(bubble.children[1].textContent, hostile);   // inserted as text, never as markup
  assert.equal(bubble.children[1].innerHTML, undefined);
  context.renderHostQueue([{ content: 'one' }]);
  assert.equal(messages.children[1].children[0].textContent, '1 message queued');
});

test('queued messages appear only for running sessions and show a chip in the session list', () => {
  assert.match(app, /session\.status === "Running" && session\.queued\?\.length\) renderHostQueue\(session\.queued\)/);
  assert.match(app, /session\.queued_count[\s\S]{0,200}queued-chip/);
  assert.match(app, /`\$\{session\.queued_count\} queued`/);
});
