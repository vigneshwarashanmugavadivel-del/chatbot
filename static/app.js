const messages = document.querySelector('#messages');
const form = document.querySelector('#chat-form');
const input = document.querySelector('#message');
const send = document.querySelector('#send');
const reset = document.querySelector('#reset');
const status = document.querySelector('#status');
const suggestions = document.querySelector('#suggestions');
let history = [];
let busy = false;
let activeSearch = null;

function addMessage(role, content) {
  const bubble = document.createElement('div');
  bubble.className = `message ${role}`;
  bubble.dir = 'auto';
  bubble.textContent = content;
  messages.append(bubble);
  messages.scrollTop = messages.scrollHeight;
  return bubble;
}

function newChat() {
  history = [];
  activeSearch = null;
  messages.replaceChildren();
  status.textContent = '';
  suggestions.hidden = false;
  addMessage('assistant', 'Welcome to Alhuzaifa. What are you looking for today?');
  input.value = '';
  input.focus();
}

function safeLink(value) {
  try {
    const url = new URL(value);
    return ['https:', 'http:'].includes(url.protocol) && !url.username ? url.href : null;
  } catch { return null; }
}

function renderFilterSuggestions(search, reply) {
  document.querySelectorAll('.filter-suggestions').forEach((element) => element.remove());
  const choices = buildFilterSuggestions(search, reply);
  if (!choices.length) return;
  const group = document.createElement('div');
  group.className = 'filter-suggestions';
  group.setAttribute('role', 'group');
  group.setAttribute('aria-label', 'Suggested filters');
  for (const choice of choices) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'filter-link';
    button.textContent = choice.label;
    button.disabled = busy;
    button.addEventListener('click', () => {
      if (busy) return;
      input.value = choice.message;
      return submitMessage();
    });
    group.append(button);
  }
  messages.append(group);
  messages.scrollTop = messages.scrollHeight;
}

function renderProducts(search, before = null) {
  const block = document.createElement('section');
  block.className = 'search-results';
  block.setAttribute('aria-label', 'Product search results');
  const summary = document.createElement('p');
  summary.className = 'search-summary';
  const queryLabel = search.query || search.request?.filters?.categories?.join(', ') || 'Products';
  summary.textContent = search.products.length
    ? `${search.similar_to ? `Similar to ${search.similar_to.title}` : `Search: ${queryLabel}`} · About ${search.total} matches · Page ${search.page}`
    : search.similar_to ? 'No similar products match your current filters.' : `No products found for “${queryLabel}”.`;
  block.append(summary);
  const active = document.createElement('p');
  active.className = 'search-summary';
  const labels = {
    categories: 'Category', colors: 'Color', materials: 'Material', sizes: 'Size',
    brands: 'Brand', product_types: 'Product type', product_ids: 'SKU', group_ids: 'Group',
    min_price: 'Minimum price', max_price: 'Maximum price', currency: 'Currency',
    min_length_cm: 'Minimum length (cm)', max_length_cm: 'Maximum length (cm)',
    min_width_cm: 'Minimum width (cm)', max_width_cm: 'Maximum width (cm)',
    min_height_cm: 'Minimum height (cm)', max_height_cm: 'Maximum height (cm)',
  };
  const applied = [];
  for (const [key, value] of Object.entries(search.applied_filters || {})) {
    if (key === 'on_sale') applied.push(value ? 'On sale' : 'No discount');
    else if (labels[key]) applied.push(`${labels[key]}: ${Array.isArray(value) ? value.join(', ') : value}`);
  }
  if (search.region) applied.push(`Storefront: ${search.region}`);
  const sortLabels = { price_asc: 'Price: low to high', price_desc: 'Price: high to low', title_asc: 'Name: A–Z' };
  if (sortLabels[search.sort]) applied.push(sortLabels[search.sort]);
  active.textContent = applied.join(' · ');
  block.append(active);
  const grid = document.createElement('div');
  grid.className = 'product-grid';
  for (const product of search.products) grid.append(createProductCard(product, search));
  block.append(grid);
  if (before) messages.insertBefore(block, before);
  else messages.append(block);
  messages.scrollTop = messages.scrollHeight;
  return block;
}

function setBusy(value) {
  busy = value;
  send.disabled = reset.disabled = input.disabled = value;
  document.querySelectorAll('.colour-option').forEach((button) => { button.disabled = value; });
  document.querySelectorAll('.show-similar').forEach((button) => { button.disabled = value; });
  document.querySelectorAll('.filter-link').forEach((button) => { button.disabled = value; });
}

function createProductCard(product, search) {
  const card = document.createElement('article');
  card.className = 'product-card';
  const media = document.createElement('div');
  media.className = 'product-media';
  const placeholder = document.createElement('span');
  placeholder.className = 'product-image-placeholder';
  placeholder.textContent = 'Image unavailable';
  media.append(placeholder);
  const imageUrl = safeLink(product.image_url);
  if (imageUrl) {
    const image = document.createElement('img');
    image.src = imageUrl;
    image.alt = product.title;
    image.loading = 'lazy';
    image.referrerPolicy = 'no-referrer';
    placeholder.hidden = true;
    image.addEventListener('error', () => {
      image.remove();
      placeholder.hidden = false;
    });
    media.append(image);
  }
  card.append(media);
  const body = document.createElement('div');
  body.className = 'product-body';
  if (product.category) {
    const category = document.createElement('p');
    category.className = 'product-category';
    category.textContent = product.category;
    body.append(category);
  }
  const title = document.createElement('h3');
  title.textContent = product.title;
  body.append(title);
  if (product.description) {
    const description = document.createElement('p');
    description.className = 'product-description';
    description.textContent = product.description;
    body.append(description);
  }
  const specs = document.createElement('dl');
  specs.className = 'product-specs';
  for (const [label, value] of [
    ['Brand', product.brand],
    ['Colour', product.color],
    ['Material and finish', product.material],
    ['Dimensions', product.dimension],
    ['Size', product.size],
  ]) {
    if (!value) continue;
    const row = document.createElement('div');
    const name = document.createElement('dt');
    name.textContent = label;
    const detail = document.createElement('dd');
    // Catalog labels often run together: "cmWidth" or "steelBody".
    detail.textContent = String(value)
      .replace(/([^\s])(?=(?:Seat\s+)?(?:Length|Width|Height|Depth|Diameter|Frame|Body)\s*:)/gi, '$1\n')
      .replace(/:\s*/g, ': ');
    row.append(name, detail);
    specs.append(row);
  }
  if (specs.childElementCount) body.append(specs);
  const options = product.color_options || [];
  if (options.length) {
    const selectedOption = options.find((option) => option.id === product.id)
      || options.find((option) => product.color && option.color.trim().toLowerCase() === product.color.trim().toLowerCase());
    const colors = document.createElement('div');
    colors.className = 'product-colors';
    colors.setAttribute('role', 'group');
    colors.setAttribute('aria-label', `Available colours for ${product.title}`);
    const label = document.createElement('p');
    label.className = 'product-colors-label';
    label.textContent = selectedOption ? `Available colours · ${selectedOption.color} selected` : 'Available colours';
    colors.append(label);
    const choices = document.createElement('div');
    choices.className = 'product-color-options';
    const feedback = document.createElement('p');
    feedback.className = 'product-color-status';
    feedback.setAttribute('role', 'status');
    for (const option of options) {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'colour-option';
      button.textContent = option.color;
      button.disabled = busy;
      button.setAttribute('aria-pressed', String(option === selectedOption));
      button.addEventListener('click', () => {
        if (option !== selectedOption) return selectProductColor(card, product, option, search, feedback);
      });
      choices.append(button);
    }
    colors.append(choices, feedback);
    if (product.color_options_error) feedback.textContent = product.color_options_error;
    if (product.variant_selected) {
      const note = document.createElement('p');
      note.className = 'product-variant-note';
      note.textContent = 'Colour changed for this product.';
      colors.append(note);
    }
    body.append(colors);
  }
  const purchase = document.createElement('div');
  purchase.className = 'product-purchase';
  const price = document.createElement('p');
  price.className = 'product-price';
  const current = document.createElement('span');
  current.textContent = product.display_price || 'Price unavailable';
  price.append(current);
  if (product.on_sale && product.price) {
    const original = document.createElement('s');
    original.textContent = product.price;
    price.append(original);
  }
  purchase.append(price);
  if (product.id) {
    const sku = document.createElement('p');
    sku.className = 'product-sku';
    sku.textContent = `SKU: ${product.id}`;
    purchase.append(sku);
  }
  body.append(purchase);
  if (product.id) {
    const similar = document.createElement('button');
    similar.type = 'button';
    similar.className = 'show-similar';
    similar.textContent = 'Show similar';
    similar.disabled = busy;
    similar.setAttribute('aria-label', `Show products similar to ${product.title}`);
    similar.addEventListener('click', () => showSimilarProducts(product, search));
    body.append(similar);
  }
  card.append(body);
  return card;
}

async function showSimilarProducts(product, search) {
  if (busy) return;
  setBusy(true);
  suggestions.hidden = true;
  const question = `Show products similar to ${product.title} (SKU ${product.id}).`;
  const bubble = addMessage('user', question);
  status.textContent = `Finding products similar to ${product.title}…`;
  try {
    const state = { ...(search.request || {}), filters: { ...(search.request?.filters || {}) } };
    if (product.variant_selected && product.color && state.filters.colors?.length) state.filters.colors = [product.color];
    const response = await fetch('/api/products/similar', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ product_id: product.id, search_state: state, limit: 8 }),
      signal: AbortSignal.timeout(90000),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Could not load similar products. Please try again.');
    const filters = data.request?.filters || {};
    let followUp = '';
    if (filters.min_price == null && filters.max_price == null) followUp = 'What budget would you like to stay within?';
    else if (!filters.colors?.length && data.products.some((item) => item.color)) followUp = 'Do you have a preferred colour?';
    else if (!filters.materials?.length && data.products.some((item) => item.material)) followUp = 'Do you have a preferred material or finish?';
    const reply = data.products.length
      ? `Here are some alternatives to ${product.title}, keeping your search requirements.${followUp ? '\n\n' + followUp : ''}`
      : `I couldn’t find alternatives to ${product.title} with the current filters. Would you like to broaden one of your requirements?`;
    renderProducts(data);
    addMessage('assistant', reply);
    activeSearch = data.request;
    history.push(
      { role: 'user', content: question.slice(0, 4000) },
      { role: 'assistant', content: (reply + '\nDisplayed products in order: ' + data.products.map((item) => `${item.title} (${item.color || 'colour unknown'}, SKU ${item.id})`).join('; ')).slice(0, 4000) },
    );
    history = history.slice(-20);
    renderFilterSuggestions(data, reply);
    status.textContent = '';
  } catch (error) {
    bubble.remove();
    status.textContent = error.name === 'TimeoutError' ? 'Finding similar products timed out. Please try again.' : error.message;
  } finally {
    setBusy(false);
    input.focus();
  }
}

async function selectProductColor(card, product, option, search, feedback) {
  if (busy || product.id === option.id) return;
  setBusy(true);
  let selectedButton = null;
  feedback.textContent = `Loading ${option.color}…`;
  try {
    const response = await fetch('/api/products/variant', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ product_id: product.id, variant_id: option.id, search_state: search.request || {} }),
      signal: AbortSignal.timeout(60000),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Could not load that colour. Please try again.');
    const selected = { ...data, variant_selected: true };
    const index = search.products.findIndex((item) => item.id === product.id);
    if (index !== -1) search.products[index] = selected;
    const replacement = createProductCard(selected, search);
    card.replaceWith(replacement);
    selectedButton = replacement.querySelector('[aria-pressed="true"]');
    // Keep follow-up references grounded in the SKU the customer is now viewing.
    history.push(
      { role: 'user', content: `For ${product.title}, select the ${option.color} colour (SKU ${option.id}).`.slice(0, 4000) },
      { role: 'assistant', content: `Showing ${data.title} in ${data.color}, SKU ${data.id}, ${data.display_price || 'price unknown'}. Current displayed products in order: ${search.products.map((item) => `${item.title} (${item.color || 'colour unknown'}, SKU ${item.id})`).join('; ')}`.slice(0, 4000) },
    );
    history = history.slice(-20);
    status.textContent = `Showing ${data.title} in ${data.color}.`;
  } catch (error) {
    feedback.textContent = error.name === 'TimeoutError' ? 'Loading this colour timed out. Please try again.' : error.message;
  } finally {
    setBusy(false);
    selectedButton?.focus();
  }
}

async function submitMessage() {
  const content = input.value.trim();
  if (!content || busy) return;
  setBusy(true);
  suggestions.hidden = true;
  input.value = '';
  const bubble = addMessage('user', content);
  let assistantBubble = null;
  let productBlock = null;
  function updateReply(text) {
    if (!assistantBubble) assistantBubble = addMessage('assistant', '');
    assistantBubble.textContent = text;
    assistantBubble.hidden = !text;
    messages.scrollTop = messages.scrollHeight;
  }
  status.textContent = 'Thinking…';
  try {
    const response = await fetch('/api/chat/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ messages: [...history.slice(-20), { role: 'user', content }], search_state: activeSearch }),
      signal: AbortSignal.timeout(180000),
    });
    if (!response.ok) {
      const error = await response.json();
      throw new Error(typeof error.detail === 'string' ? error.detail : 'Please check your message and try again.');
    }
    const data = await readChatStream(response, (event) => {
      if (event.type === 'status') status.textContent = event.message;
      else if (event.type === 'reset') updateReply('');
      else if (event.type === 'delta') {
        status.textContent = 'Writing…';
        updateReply((assistantBubble?.textContent || '') + event.text);
      } else if (event.type === 'search') {
        productBlock?.remove();
        productBlock = event.search ? renderProducts(event.search, assistantBubble) : null;
        if (event.search_error) status.textContent = event.search_error;
      }
    });
    const productContext = data.search?.products.length
      ? '\nDisplayed catalog products in order: ' + data.search.products.map((p) => `${p.title} (SKU ${p.id})`).join('; ')
        + `. Search query: ${data.search.query}; page: ${data.search.page}; limit: ${data.search.limit}.`
      : '';
    history.push({ role: 'user', content }, { role: 'assistant', content: data.reply.slice(0, 3000) + productContext.slice(0, 1000) });
    history = history.slice(-20);
    updateReply(data.reply);
    if (data.search) {
      activeSearch = data.search.request;
      if (!productBlock) productBlock = renderProducts(data.search, assistantBubble);
    }
    renderFilterSuggestions(data.search_error ? null : data.search, data.reply);
    status.textContent = data.search_error || '';
  } catch (error) {
    bubble.remove();
    assistantBubble?.remove();
    productBlock?.remove();
    input.value = content;
    status.textContent = error.name === 'TimeoutError' ? 'The request timed out. Please try again.' : error.message;
  } finally {
    setBusy(false);
    input.focus();
  }
}

form.addEventListener('submit', (event) => { event.preventDefault(); submitMessage(); });
input.addEventListener('keydown', (event) => {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    submitMessage();
  }
});
reset.addEventListener('click', newChat);
suggestions.querySelectorAll('button').forEach((button) => button.addEventListener('click', () => {
  input.value = button.textContent;
  submitMessage();
}));
newChat();
fetch('/api/health').then((response) => response.json()).then((data) => {
  if (!data.configured && !busy) status.textContent = 'Setup needed: add OPENAI_API_KEY to .env and restart the server.';
}).catch(() => { status.textContent = 'Unable to reach the server. Please reload the page.'; });
