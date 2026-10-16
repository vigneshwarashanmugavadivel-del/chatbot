const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

// Small DOM fixture for exercising the interaction without a browser dependency.
class Element {
  constructor(tag) { this.tag = tag; this.children = []; this.attrs = {}; this.listeners = {}; this._text = ''; }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(child => typeof child === 'string' ? child : child.textContent).join(''); }
  get childElementCount() { return this.children.filter(child => typeof child !== 'string').length; }
  append(...children) { for (const child of children) { if (typeof child !== 'string') child.parent = this; this.children.push(child); } }
  replaceChildren(...children) { this.children = []; this._text = ''; this.append(...children); }
  setAttribute(key, value) { this.attrs[key] = value; }
  addEventListener(name, listener) { this.listeners[name] = listener; }
  focus() { this.focused = !this.disabled; }
  querySelectorAll(selector) {
    return descendants(this).filter(node => selector.startsWith('.') ? node.className === selector.slice(1)
      : selector === '[aria-pressed="true"]' && node.attrs['aria-pressed'] === 'true');
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  replaceWith(other) { const index = this.parent.children.indexOf(this); this.parent.children[index] = other; other.parent = this.parent; }
  remove() { if (this.parent) this.parent.children.splice(this.parent.children.indexOf(this), 1); }
}
function descendants(node) {
  return node.children.flatMap(child => typeof child === 'string' ? [] : [child, ...descendants(child)]);
}
function setup(fail = false, filters = { colors: ['beige'], max_price: 5000 }) {
  const nodes = {};
  const requests = [];
  const product = { id: 'BEIGE', title: 'Riana', color: 'Beige', stock: 2, display_price: '1,500 AED',
    image_url: 'https://example.com/beige.jpg', dimension: 'Length:260 cm',
    color_options: [{ id: 'BEIGE', color: 'Beige' }, { id: 'GREY', color: 'Grey' }] };
  const state = { filters, region: 'AE' };
  const search = { query: 'sofa', total: 1, page: 1, products: [product], request: state };
  const context = vm.createContext({
    URL, AbortSignal,
    buildFilterSuggestions: require('../static/filters.js').buildFilterSuggestions,
    document: {
      querySelector: key => nodes[key] ||= new Element('div'),
      querySelectorAll: selector => Object.values(nodes).flatMap(node => node.querySelectorAll(selector)),
      createElement: tag => new Element(tag),
    },
    fetch: async (url, options) => {
      if (url === '/api/health') return { json: async () => ({ configured: true }) };
      requests.push({ url, body: JSON.parse(options.body) });
      if (url === '/api/products/similar') return { ok: !fail, json: async () => fail
        ? { detail: 'Similar products are temporarily unavailable.' }
        : { ...search, products: [{ ...product, id: 'OTHER', title: 'Alternative' }], similar_to: { id: product.id, title: product.title } } };
      return { ok: !fail, json: async () => fail ? { detail: 'That colour is no longer available.' }
        : { ...product, id: 'GREY', color: 'Grey', display_price: '1,800 AED', image_url: 'https://example.com/grey.jpg', dimension: 'Length:265 cm' } };
    },
  });
  vm.runInContext(fs.readFileSync('static/app.js', 'utf8'), context);
  const block = context.renderProducts(search);
  return { context, nodes, block, requests, search, state };
}

test('selecting a colour replaces image, price, specifications and SKU on the same card', async () => {
  const { context, nodes, block, requests, search, state } = setup();
  const button = block.querySelectorAll('.colour-option').find(node => node.textContent === 'Grey');
  await button.listeners.click();
  assert.equal(requests.length, 1);
  assert.equal(requests[0].url, '/api/products/variant');
  assert.deepEqual(requests[0].body, { product_id: 'BEIGE', variant_id: 'GREY', search_state: state });
  const elements = descendants(block);
  assert(elements.some(node => node.tag === 'img' && node.src === 'https://example.com/grey.jpg'));
  assert(block.textContent.includes('1,800 AED'));
  assert(block.textContent.includes('Length: 265 cm'));
  assert(block.textContent.includes('SKU: GREY'));
  assert(!block.textContent.includes('SKU: BEIGE'));
  assert.equal(block.querySelector('[aria-pressed="true"]').textContent, 'Grey');
  assert.equal(block.querySelector('[aria-pressed="true"]').focused, true);
  assert.equal(search.products[0].id, 'GREY');
  assert.equal(nodes['#send'].disabled, false);
  assert(vm.runInContext('history[history.length - 1].content', context).includes('SKU GREY'));
});

test('failed selection retains the current card and displays the error', async () => {
  const { nodes, block, search } = setup(true);
  await block.querySelectorAll('.colour-option').find(node => node.textContent === 'Grey').listeners.click();
  assert(block.textContent.includes('SKU: BEIGE'));
  assert(block.textContent.includes('That colour is no longer available.'));
  assert.equal(search.products[0].id, 'BEIGE');
  assert.equal(block.querySelector('[aria-pressed="true"]').textContent, 'Beige');
  assert.equal(nodes['#send'].disabled, false);
});

test('selecting the current colour makes no additional request', async () => {
  const { block, requests } = setup();
  await block.querySelector('[aria-pressed="true"]').listeners.click();
  assert.equal(requests.length, 0);
});

test('Show similar displays alternative cards above the reply and records them for follow-ups', async () => {
  const { context, nodes, block, requests, state } = setup(false, { max_price: 5000 });
  await block.querySelector('.show-similar').listeners.click();
  assert.equal(requests[0].url,'/api/products/similar');
  assert.deepEqual(requests[0].body,{product_id:'BEIGE',search_state:state,limit:8});
  assert(nodes['#messages'].textContent.includes('Similar to Riana'));
  assert(nodes['#messages'].textContent.includes('SKU: OTHER'));
  const children=nodes['#messages'].children;
  const resultIndex = children.findLastIndex(node => node.className === 'search-results');
  const replyIndex = children.findLastIndex(node => node.className === 'message assistant');
  assert(resultIndex < replyIndex);
  assert.equal(children.at(-1).className,'filter-suggestions');
  assert(vm.runInContext('history[history.length - 1].content',context).includes('SKU OTHER'));
  assert.equal(nodes['#send'].disabled,false);
});

test('Show similar reports a failure and keeps existing cards usable', async () => {
  const { nodes, block } = setup(true);
  await block.querySelector('.show-similar').listeners.click();
  assert(nodes['#status'].textContent.includes('temporarily unavailable'));
  assert(block.textContent.includes('SKU: BEIGE'));
  assert.equal(block.querySelector('.show-similar').disabled,false);
});

test('clicking a suggested filter submits the choice with the existing search context', async () => {
  const { context, nodes, block, requests } = setup(false, { max_price: 5000 });
  await block.querySelector('.show-similar').listeners.click();
  const choices = nodes['#messages'].querySelectorAll('.filter-link');
  const grey = choices.find(button => button.textContent === 'Grey');
  assert(grey);
  context.readChatStream = async () => ({ reply: 'Here are your grey alternatives.', search: null });
  await grey.listeners.click();
  const submitted = requests.at(-1);
  assert.equal(submitted.url, '/api/chat/stream');
  assert(submitted.body.messages.at(-1).content.includes('colour filter to Grey'));
  assert.equal(submitted.body.search_state.filters.max_price, 5000);
  assert.equal(nodes['#messages'].querySelectorAll('.filter-link').length, 0);
  assert.equal(nodes['#send'].disabled, false);
});
