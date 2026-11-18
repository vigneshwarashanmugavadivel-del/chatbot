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
  if (role === 'assistant') renderAssistantText(bubble, content);
  else bubble.textContent = content;
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

function renderAssistantText(bubble, content) {
  bubble.replaceChildren();
  let offset = 0;
  for (const match of content.matchAll(/https?:\/\/[^\s<>()]+/g)) {
    const urlText = match[0].replace(/[.,!?;:]+$/, '');
    const href = safeLink(urlText);
    if (!href) continue;
    bubble.append(content.slice(offset, match.index));
    const link = document.createElement('a');
    link.href = href;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    link.textContent = urlText;
    bubble.append(link);
    offset = match.index + urlText.length;
  }
  bubble.append(content.slice(offset));
}

function renderFollowUps(questions) {
  document.querySelectorAll('.follow-up-suggestions').forEach((element) => element.remove());
  const choices = buildFollowUpSuggestions(questions);
  if (!choices.length) return;
  const group = document.createElement('div');
  group.className = 'follow-up-suggestions';
  group.setAttribute('role', 'group');
  group.setAttribute('aria-label', 'Suggested replies');
  for (const choice of choices) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'follow-up-link';
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

function renderPresentation(presentation, before = null) {
  if (!presentation || presentation.kind === 'text') return null;
  const block = document.createElement('section');
  block.className = 'search-results';
  block.setAttribute('aria-label', presentation.kind === 'comparison' ? 'Product comparison' : 'Recommended products');
  if (presentation.title) {
    const heading = document.createElement('h3');
    heading.className = 'presentation-title';
    heading.textContent = presentation.title;
    block.append(heading);
  }
  if (presentation.kind === 'comparison') {
    const wrapper = document.createElement('div');
    wrapper.className = 'comparison-scroll';
    const table = document.createElement('table');
    table.className = 'product-comparison';
    const header = document.createElement('tr');
    const corner = document.createElement('th');
    corner.textContent = 'Details';
    header.append(corner);
    for (const item of presentation.items) {
      const name = document.createElement('th');
      name.setAttribute('scope', 'col');
      name.textContent = item.product.title;
      header.append(name);
    }
    const head = document.createElement('thead');
    head.append(header);
    table.append(head);
    const body = document.createElement('tbody');
    const labels = { description: 'Description', category: 'Category', material: 'Material and finish',
      color: 'Colour', dimension: 'Dimensions', size: 'Size', brand: 'Brand', display_price: 'Price' };
    for (const field of presentation.fields) {
      const row = document.createElement('tr');
      const label = document.createElement('th');
      label.setAttribute('scope', 'row');
      label.textContent = labels[field] || field;
      row.append(label);
      for (const item of presentation.items) {
        const value = document.createElement('td');
        const original = item.product[field];
        value.textContent = original && ['material', 'dimension', 'size'].includes(field)
          ? formatSpecifications(original) : original || 'Not listed';
        row.append(value);
      }
      body.append(row);
    }
    table.append(body);
    wrapper.append(table);
    block.append(wrapper);
  } else {
    const grid = document.createElement('div');
    grid.className = 'product-grid';
    for (const item of presentation.items) {
      grid.append(createProductCard(item.product));
    }
    block.append(grid);
  }
  if (before) messages.insertBefore(block, before);
  else messages.append(block);
  messages.scrollTop = messages.scrollHeight;
  return block;
}

function setBusy(value) {
  busy = value;
  send.disabled = reset.disabled = input.disabled = value;
  document.querySelectorAll('.show-similar').forEach((button) => { button.disabled = value; });
  document.querySelectorAll('.follow-up-link').forEach((button) => { button.disabled = value; });
}

function formatSpecifications(value) {
  return String(value)
    // Recognize label boundaries, including multiword labels, without a list
    // of catalog-specific headings (e.g. "cmSmall:" or "acrylicTop:").
    .replace(/([^\s])(?=[A-Z][A-Za-z]*(?:[ \t]+[A-Za-z]+){0,3}[ \t]*:)/g, '$1\n')
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
    .replace(/:[ \t]*/g, ': ')
    .trim();
}

function createProductCard(product) {
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
  const productUrl = safeLink(product.url);
  if (productUrl) {
    const link = document.createElement('a');
    link.className = 'product-link';
    link.href = productUrl;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    link.textContent = product.title;
    link.setAttribute('aria-label', `${product.title} (opens in a new tab)`);
    title.append(link);
  } else {
    title.textContent = product.title;
  }
  body.append(title);
  if (product.description) {
    const description = document.createElement('p');
    description.className = 'product-description';
    description.textContent = product.description;
    body.append(description);
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
  body.append(purchase);
  if (product.id) {
    const similar = document.createElement('button');
    similar.type = 'button';
    similar.className = 'show-similar';
    similar.textContent = 'Show similar';
    similar.disabled = busy;
    similar.setAttribute('aria-label', `Show products similar to ${product.title}`);
    similar.addEventListener('click', () => showSimilarProducts(product));
    body.append(similar);
  }
  card.append(body);
  return card;
}

async function showSimilarProducts(product) {
  if (busy) return;
  input.value = `Show products similar to ${product.title} (SKU ${product.id}).`;
  return submitMessage();
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
    renderAssistantText(assistantBubble, text);
    assistantBubble.hidden = !text;
    messages.scrollTop = messages.scrollHeight;
  }
  status.textContent = 'Thinking…';
  try {
    const response = await fetch('/api/chat/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ messages: [...history.slice(-20), { role: 'user', content }], search_state: activeSearch,
        chat_instructions: typeof getChatInstructions === 'function' ? getChatInstructions() : null }),
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
      } else if (event.type === 'presentation') {
        productBlock?.remove();
        productBlock = renderPresentation(event.presentation, assistantBubble);
      }
    });
    const items = data.presentation?.items || [];
    const productContext = items.length
      ? '\nDisplayed catalog products in order: ' + items.map(item => `${item.product.title} (SKU ${item.product.id}, storefront ${item.request?.region || 'default'})`).join('; ')
      : '';
    history.push({ role: 'user', content }, { role: 'assistant', content: data.reply.slice(0, 3000) + productContext.slice(0, 1000) });
    history = history.slice(-20);
    updateReply(data.reply);
    if (data.presentation && !productBlock) productBlock = renderPresentation(data.presentation, assistantBubble);
    if ('search_state' in data) activeSearch = data.search_state;
    renderFollowUps(data.follow_ups);
    status.textContent = '';
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
