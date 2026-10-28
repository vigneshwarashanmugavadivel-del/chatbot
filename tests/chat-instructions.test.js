const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function setup({ saved = null, failLoad = false, blockStorage = false } = {}) {
  const nodes = {};
  const storage = new Map(saved === null ? [] : [['alhuzaifa.chat-instructions', saved]]);
  const requests = [];
  const defaults = 'Default conversation instructions.';
  const context = vm.createContext({
    document: { querySelector(selector) {
      return nodes[selector] ||= {
        value: '', textContent: '', maxLength: 20000, disabled: false, open: false, listeners: {},
        addEventListener(name, callback) { this.listeners[name] = callback; },
        showModal() { this.open = true; }, close() { this.open = false; }, focus() { this.focused = true; },
      };
    } },
    localStorage: {
      getItem(key) { if (blockStorage) throw new Error('Blocked'); return storage.get(key) ?? null; },
      setItem(key, value) { if (blockStorage) throw new Error('Blocked'); storage.set(key, value); },
      removeItem(key) { if (blockStorage) throw new Error('Blocked'); storage.delete(key); },
    },
    fetch: async url => {
      requests.push(url);
      return { ok: !failLoad, json: async () => ({ chat_instructions: defaults, max_length: 20000 }) };
    },
  });
  vm.runInContext(fs.readFileSync('static/chat-instructions.js', 'utf8'), context);
  const open = () => nodes['#edit-chat-instructions'].listeners.click();
  const save = () => nodes['#chat-instructions-form'].listeners.submit({ preventDefault() {} });
  return { context, nodes, storage, requests, defaults, open, save };
}

test('loads defaults, saves custom instructions, and restores the saved version on reopen', async () => {
  const ui = setup();
  assert.equal(ui.context.getChatInstructions(), null);
  await ui.open();
  assert.equal(ui.nodes['#chat-instructions-text'].value, ui.defaults);
  ui.nodes['#chat-instructions-text'].value = '  Be concise and reply in Hindi.  ';
  ui.save();
  assert.equal(ui.context.getChatInstructions(), 'Be concise and reply in Hindi.');
  assert.equal(ui.storage.get('alhuzaifa.chat-instructions'), 'Be concise and reply in Hindi.');
  assert.equal(ui.nodes['#chat-instructions-dialog'].open, false);
  await ui.open();
  assert.equal(ui.nodes['#chat-instructions-text'].value, 'Be concise and reply in Hindi.');
  assert.deepEqual(ui.requests, ['/api/chat/instructions']);
});

test('cancel discards edits and default restoration requires saving', async () => {
  const ui = setup({ saved: 'Saved instructions.' });
  await ui.open();
  ui.nodes['#chat-instructions-text'].value = 'Unsaved draft.';
  ui.nodes['#cancel-chat-instructions'].listeners.click();
  assert.equal(ui.context.getChatInstructions(), 'Saved instructions.');
  await ui.open();
  assert.equal(ui.nodes['#chat-instructions-text'].value, 'Saved instructions.');
  ui.nodes['#default-chat-instructions'].listeners.click();
  assert.equal(ui.context.getChatInstructions(), 'Saved instructions.');
  ui.save();
  assert.equal(ui.context.getChatInstructions(), null);
  assert.equal(ui.storage.has('alhuzaifa.chat-instructions'), false);
});

test('blank instructions cannot overwrite saved instructions', async () => {
  const ui = setup({ saved: 'Saved instructions.' });
  await ui.open();
  ui.nodes['#chat-instructions-text'].value = '   ';
  ui.save();
  assert.equal(ui.context.getChatInstructions(), 'Saved instructions.');
  assert.equal(ui.nodes['#chat-instructions-dialog'].open, true);
  assert.match(ui.nodes['#chat-instructions-feedback'].textContent, /Enter instructions/);
});

test('a failed default load keeps saving disabled and preserves the current override', async () => {
  const ui = setup({ saved: 'Saved instructions.', failLoad: true });
  await ui.open();
  assert.equal(ui.nodes['#save-chat-instructions'].disabled, true);
  ui.save();
  assert.equal(ui.context.getChatInstructions(), 'Saved instructions.');
  assert.match(ui.nodes['#chat-instructions-feedback'].textContent, /Unable to load/);
});

test('editing still works for the current tab if browser storage is blocked', async () => {
  const ui = setup({ blockStorage: true });
  await ui.open();
  ui.nodes['#chat-instructions-text'].value = 'Keep replies short.';
  ui.save();
  assert.equal(ui.context.getChatInstructions(), 'Keep replies short.');
});
