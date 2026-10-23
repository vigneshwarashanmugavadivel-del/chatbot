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
  insertBefore(child, before) { const index = this.children.indexOf(before); child.parent = this; this.children.splice(index < 0 ? this.children.length : index, 0, child); }
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
  const search = { products: [product], request: state };
  const presentation = { kind: 'recommendations', title: 'Sofas', fields: [], items: [{ product, request: state }] };
  const context = vm.createContext({
    URL, AbortSignal, initialState: state,
    buildFollowUpSuggestions: require('../static/follow-ups.js').buildFollowUpSuggestions,
    document: {
      querySelector: key => nodes[key] ||= new Element('div'),
      querySelectorAll: selector => Object.values(nodes).flatMap(node => node.querySelectorAll(selector)),
      createElement: tag => new Element(tag),
    },
    fetch: async (url, options) => {
      if (url === '/api/health') return { json: async () => ({ configured: true }) };
      requests.push({ url, body: JSON.parse(options.body) });
      if (url === '/api/chat/stream') return { ok: !fail, json: async () => ({ detail: 'Search is temporarily unavailable.' }) };
      return { ok: !fail, json: async () => fail ? { detail: 'That colour is no longer available.' }
        : { ...product, id: 'GREY', color: 'Grey', display_price: '1,800 AED', image_url: 'https://example.com/grey.jpg', dimension: 'Length:265 cm' } };
    },
  });
  vm.runInContext(fs.readFileSync('static/app.js', 'utf8'), context);
  vm.runInContext('activeSearch = initialState', context);
  context.readChatStream = async () => ({ reply: 'Here are some alternatives.',
    presentation: { kind: 'recommendations', title: 'Similar options', fields: [], items: [{
      product: { ...product, id: 'OTHER', title: 'Alternative' }, request: state }] },
    search_state: state, follow_ups: ['Which is easiest to clean?', 'Can I see these in grey?', 'How do these compare?', 'What goes with these?'] });
  const block = context.renderPresentation(presentation);
  return { context, nodes, block, requests, search, state };
}

test('card specifications separate joined labels and words without catalog-specific headings', () => {
  const { context } = setup();
  const card = context.createProductCard({ id:'A', title:'Table', brand:'Al Huzaifa',
    dimension:'Height: 65 cmSmall: Diameter:40 cmSeat Height:45 cm',
    material:'Frame: Wood, acrylicTop: Sintered stoneSideboard',
  }, { products:[], request:{} });
  const specs = descendants(card).filter(node => node.tag === 'dd').map(node => node.textContent);
  assert(specs.includes('Height: 65 cm\nSmall: Diameter: 40 cm\nSeat Height: 45 cm'));
  assert(specs.includes('Frame: Wood, acrylic\nTop: Sintered stone Sideboard'));
  assert(specs.includes('Al Huzaifa'));
  assert(descendants(card).some(node => node.tag === 'dt' && node.textContent === 'Dimensions:'));
});

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
  assert.equal(requests[0].url,'/api/chat/stream');
  assert.deepEqual(requests[0].body.search_state,state);
  assert(requests[0].body.messages.at(-1).content.includes('SKU BEIGE'));
  assert(nodes['#messages'].textContent.includes('Similar options'));
  assert(nodes['#messages'].textContent.includes('SKU: OTHER'));
  const children=nodes['#messages'].children;
  const resultIndex = children.findLastIndex(node => node.className === 'search-results');
  const replyIndex = children.findLastIndex(node => node.className === 'message assistant');
  assert(resultIndex < replyIndex);
  assert.equal(children.at(-1).className,'follow-up-suggestions');
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

test('clicking a follow-up sends the question with existing search context', async () => {
  const { context, nodes, block, requests } = setup(false, { max_price: 5000 });
  await block.querySelector('.show-similar').listeners.click();
  const choices = nodes['#messages'].querySelectorAll('.follow-up-link');
  const grey = choices.find(button => button.textContent === 'Can I see these in grey?');
  assert(grey);
  context.readChatStream = async () => ({ reply: 'Here are your grey alternatives.', search: null });
  await grey.listeners.click();
  const submitted = requests.at(-1);
  assert.equal(submitted.url, '/api/chat/stream');
  assert.equal(submitted.body.messages.at(-1).content, 'Can I see these in grey?');
  assert.equal(submitted.body.search_state.filters.max_price, 5000);
  assert.equal(nodes['#messages'].querySelectorAll('.follow-up-link').length, 0);
  assert.equal(nodes['#send'].disabled, false);
});

test('comparison renders original values and marks missing facts without inventing them', () => {
  const { context, nodes } = setup();
  const block = context.renderPresentation({ kind: 'comparison', title: 'Compare finishes',
    fields: ['material', 'dimension'], items: [
      { product: { id: 'A', title: 'First', material: 'Linen', dimension: null }, request: {} },
      { product: { id: 'B', title: 'Second', material: 'Leather', dimension: 'Length: 200 cm' }, request: {} },
    ] });
  assert(block.textContent.includes('First'));
  assert(block.textContent.includes('Second'));
  assert(block.textContent.includes('Linen'));
  assert(block.textContent.includes('Leather'));
  assert(block.textContent.includes('Not listed'));
  assert(block.textContent.includes('Length: 200 cm'));
  assert.equal(descendants(block).filter(node => node.className === 'product-card').length, 0);
});

test('text-only presentation renders no new product block', () => {
  const { context } = setup();
  assert.equal(context.renderPresentation({ kind: 'text', items: [], fields: [] }), null);
});

test('retrieval events do not display products and preferences come only from final state', async () => {
  const { context, nodes } = setup();
  context.readChatStream = async (_, onEvent) => {
    onEvent({ type: 'retrieval', products: [{ title: 'Investigation only' }] });
    return { reply: 'I checked the details.', presentation: null, search_state: { query: 'saved', filters: { max_price: 800 } }, follow_ups: [] };
  };
  nodes['#message'].value = 'Check this product.';
  await vm.runInContext('submitMessage()', context);
  assert(!nodes['#messages'].textContent.includes('Investigation only'));
  assert.equal(vm.runInContext('activeSearch.query', context), 'saved');
  assert.equal(vm.runInContext('activeSearch.filters.max_price', context), 800);
});
