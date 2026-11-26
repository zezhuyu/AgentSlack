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

function harness() {
  class Element {
    constructor(tag) { this.tagName = tag; this.children = []; this.dataset = {}; this.attributes = {}; this.style = {}; this._html = ''; this.hidden = false; }
    append(...nodes) { this.children.push(...nodes); }
    appendChild(node) { this.children.push(node); return node; }
    setAttribute(name, value) { this.attributes[name] = value; }
    set innerHTML(value) { this._html = value; if (!value) this.children = []; }
    querySelectorAll(selector) { return this.children.flatMap((child) => (child.tagName === 'img' ? [child] : []).concat(child.querySelectorAll?.(selector) || [])); }
  }
  class File {
    constructor(parts, name, options = {}) { this.name = name; this.type = options.type || ''; this.size = parts.reduce((sum, part) => sum + (part.size || part.length || 0), 0); this.lastModified = 1; }
  }
  const elements = { pendingAttachments: new Element('div'), uploadError: new Element('div'), attachInput: new Element('input'), attachBtn: new Element('button') };
  const revoked = [];
  let nextUrl = 0;
  const context = {
    state: { pendingAttachments: [] },
    $: (id) => elements[id],
    document: { createElement: (tag) => new Element(tag), createTextNode: (text) => Object.assign(new Element('#text'), { textContent: text }), querySelectorAll: (selector) => elements.pendingAttachments.querySelectorAll(selector) },
    URL: { createObjectURL: () => `blob:${++nextUrl}`, revokeObjectURL: (url) => revoked.push(url) },
    File,
    formatBytes: (size) => `${size} B`,
  };
  const names = ['openAttachmentPicker', 'handleAttachmentInputChange', 'handleMessagePaste', 'selectAttachments', 'renderPendingAttachments', 'clearPendingAttachments', 'showAttachmentError'];
  vm.runInNewContext(`${names.map(extractFunction).join('\n')}\nObject.assign(globalThis, { ${names.join(', ')} });`, context);
  return { context, elements, revoked, File };
}

test('paperclip opens the picker and selected files enter the composer with a reset input', () => {
  const { context, elements } = harness();
  let opened = 0;
  elements.attachInput.click = () => { opened += 1; };
  context.openAttachmentPicker();
  const selected = { name: 'photo.jpg', type: 'image/jpeg', size: 4, lastModified: 2 };
  const input = { files: [selected], value: 'photo.jpg' };
  context.handleAttachmentInputChange({ target: input });
  assert.equal(opened, 1);
  assert.deepEqual(context.state.pendingAttachments, [selected]);
  assert.equal(input.value, '');
});

test('pasted images get a filename while plain text paste stays in the textarea', () => {
  const { context, File } = harness();
  let prevented = false;
  context.handleMessagePaste({ clipboardData: { files: [{ name: '', type: 'image/png', size: 6, lastModified: 3 }] }, preventDefault() { prevented = true; } });
  assert.equal(prevented, true);
  assert.match(context.state.pendingAttachments[0].name, /^pasted-image-\d+-1\.png$/);
  context.handleMessagePaste({ clipboardData: { files: [] }, preventDefault() { throw new Error('text paste must not be prevented'); } });
  assert.ok(context.state.pendingAttachments[0] instanceof File);
});

test('image chips preview, remove, and revoke object URLs', () => {
  const { context, elements, revoked } = harness();
  context.selectAttachments([{ name: 'thumb.png', type: 'image/png', size: 8, lastModified: 1 }]);
  const chip = elements.pendingAttachments.children[0];
  const image = chip.children.find((child) => child.tagName === 'img');
  assert.equal(image.src, 'blob:1');
  const remove = chip.children.find((child) => child.tagName === 'button');
  remove.onclick();
  assert.deepEqual(context.state.pendingAttachments, []);
  assert.deepEqual(revoked, ['blob:1']);
});
