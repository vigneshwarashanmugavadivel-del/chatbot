// Suggested choices send ordinary chat messages; the backend still validates filters.
function buildFilterSuggestions(search, reply) {
  if (!search || !reply?.includes('?')) return [];
  const filters = search.request?.filters || search.applied_filters || {};
  const products = search.products || [];
  if (!products.length) {
    const choices = [];
    if (filters.min_price != null || filters.max_price != null) choices.push({ label: 'Remove budget limit', message: 'Remove the price limits and keep my other filters.' });
    if (filters.colors?.length) choices.push({ label: 'Any colour', message: 'Remove the colour filter and keep my other filters.' });
    if (filters.materials?.length) choices.push({ label: 'Any material', message: 'Remove the material filter and keep my other filters.' });
    return choices;
  }
  const question = reply.split('\n').filter(line => line.includes('?')).at(-1) || '';
  let type;
  if (/budget|spend|price|cost|how much/i.test(question)) type = 'budget';
  else if (/colou?r|shade|tone/i.test(question)) type = 'color';
  else if (/material|fabric|finish/i.test(question)) type = 'material';
  else if (/length|long|width|wide|height|tall|dimension|measure/i.test(question)) type = 'dimension';
  else if (filters.min_price == null && filters.max_price == null) type = 'budget';
  else if (!filters.colors?.length) type = 'color';
  else if (!filters.materials?.length) type = 'material';

  if (type === 'budget') {
    const currency = filters.currency || products.find(product => product.currency)?.currency
      || ({ AE: 'AED', SA: 'SAR', QA: 'QAR', OM: 'OMR', BH: 'BHD' })[search.region] || 'AED';
    const prices = products.filter(product => product.currency === currency && product.effective_price > 0)
      .map(product => product.effective_price).sort((a, b) => a - b);
    if (!prices.length) return [];
    const limits = [...new Set([0, .5, 1].map(fraction => {
      const price = prices[Math.floor((prices.length - 1) * fraction)];
      const step = price >= 1000 ? 1000 : price >= 100 ? 100 : 10;
      return Math.ceil(price / step) * step;
    }))];
    return [...limits.map(amount => ({ label: `Up to ${amount.toLocaleString('en-US')} ${currency}`,
      message: `Set my maximum budget to ${amount} ${currency} and keep the other filters.` })),
      { label: 'No budget limit', message: 'I have no budget limit. Remove any price limits and keep the other filters.' }];
  }
  if (type === 'color') {
    const colors = new Map();
    for (const product of products) {
      for (const color of [product.color, ...(product.color_options || []).map(option => option.color)]) {
        if (color?.trim()) colors.set(color.trim().toLowerCase(), color.trim());
      }
    }
    return [...[...colors.values()].slice(0, 5).map(color => ({ label: color,
      message: `Set my colour filter to ${color} and keep the other filters.` })),
      { label: 'Any colour', message: 'I have no colour preference. Remove the colour filter and keep the other filters.' }];
  }
  if (type === 'material') {
    const source = products.map(product => product.material || '').join(' ');
    const materials = ['Leather', 'Fabric', 'Microfiber', 'Linen', 'Velvet', 'Wood', 'Marble', 'Metal', 'Glass', 'Ceramic', 'Rattan', 'Cotton', 'Wool', 'Polyester', 'Plywood']
      .filter(material => new RegExp(`\\b${material}\\b`, 'i').test(source)).slice(0, 4);
    return [...materials.map(material => ({ label: material,
      message: `Set my material filter to ${material} and keep the other filters.` })),
      { label: 'Any material', message: 'I have no material preference. Remove the material filter and keep the other filters.' }];
  }
  if (type === 'dimension') {
    const axis = /width|wide/i.test(question) ? 'width' : /height|tall/i.test(question) ? 'height' : 'length';
    const values = products.flatMap(product => {
      const matches = [...(product.dimension || '').matchAll(new RegExp(`${axis}:\\s*([0-9]+(?:\\.[0-9]+)?)\\s*cm`, 'gi'))];
      return matches.length === 1 ? [Number(matches[0][1])] : [];
    }).filter(value => value > 0).sort((a, b) => a - b);
    if (!values.length) return [];
    const limits = [...new Set([values[0], values[Math.floor(values.length / 2)], values.at(-1)].map(value => Math.ceil(value / 10) * 10))];
    return limits.map(value => ({ label: `${axis[0].toUpperCase() + axis.slice(1)} up to ${value} cm`,
      message: `Set my maximum ${axis} to ${value} cm and keep the other filters.` }));
  }
  return [];
}

if (typeof module !== 'undefined') module.exports = { buildFilterSuggestions };
