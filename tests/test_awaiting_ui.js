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

function render(awaiting) {
  const messages = new Element('div');
  const context = { document: { createElement: (tag) => new Element(tag) }, $: () => messages };
  vm.runInNewContext(`${extractFunction('renderHostAwaiting')}\nglobalThis.renderHostAwaiting = renderHostAwaiting;`, context);
  context.renderHostAwaiting(awaiting);
  return messages.children[0];
}

test('a pending question renders its text and options as plain text with a terminal hint', () => {
  const hostile = '<img src=x onerror="alert(1)">';
  const card = render({ kind: 'question', text: hostile, options: ['Yes (Recommended)', hostile], multi: true, questions: 2 });
  assert.equal(card.className, 'host-awaiting kind-question');
  assert.equal(card.children[0].textContent, 'Waiting for your answer');
  assert.equal(card.children[1].textContent, hostile);          // text node, never markup
  assert.equal(card.children[1].innerHTML, undefined);
  const options = card.children[2];
  assert.equal(options.children.length, 2);
  assert.equal(options.children[1].textContent, hostile);
  assert.equal(card.children[3].textContent, 'Answer in the terminal (you can pick several) · 2 questions.');
});

test('a plan approval has its own heading and no option list', () => {
  const card = render({ kind: 'plan', text: 'Refactor the chat', options: [], multi: false, questions: 1 });
  assert.equal(card.children[0].textContent, 'Plan ready for your approval');
  assert.equal(card.children.length, 3);                        // heading, text, note
  assert.equal(card.children[2].textContent, 'Answer in the terminal.');
});

test('awaiting sessions are marked in the list and detail view', () => {
  assert.match(app, /session\.awaiting \? "Needs you"/);
  assert.match(app, /is-awaiting/);
  assert.match(app, /if \(session\.awaiting\) renderHostAwaiting\(session\.awaiting\)/);
});
