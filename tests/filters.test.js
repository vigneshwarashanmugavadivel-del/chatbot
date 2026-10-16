const { test } = require('node:test');
const assert = require('node:assert/strict');
const { buildFilterSuggestions } = require('../static/filters.js');

const search = {
  request: { filters: {} },
  products: [
    { color: 'Beige', color_options: [{ color: 'Grey' }], material: 'Fabric and solid wood', effective_price: 2271, currency: 'AED', dimension: 'Length:188 cmWidth:99 cm' },
    { color: 'Grey', material: 'Natural leather', effective_price: 5520, currency: 'AED', dimension: 'Length:250 cmWidth:107 cm' },
  ],
};

test('budget links use rounded catalog prices in the same currency', () => {
  const options = buildFilterSuggestions(search, 'What budget would you like to stay within?');
  assert.deepEqual(options.map(option => option.label), ['Up to 3,000 AED', 'Up to 6,000 AED', 'No budget limit']);
  assert(options[0].message.includes('3000 AED'));
  assert(options[0].message.includes('keep the other filters'));
});

test('colour links include real variant options and combine duplicate labels', () => {
  const options = buildFilterSuggestions(search, 'What colour would you prefer?');
  assert.deepEqual(options.map(option => option.label), ['Beige', 'Grey', 'Any colour']);
});

test('material links only offer materials present in the results', () => {
  const options = buildFilterSuggestions(search, 'Do you prefer a particular material or finish?');
  assert.deepEqual(options.map(option => option.label), ['Leather', 'Fabric', 'Wood', 'Any material']);
});

test('dimension links keep the axis named in the question', () => {
  const options = buildFilterSuggestions(search, 'What maximum width would work?');
  assert.deepEqual(options.map(option => option.label), ['Width up to 100 cm', 'Width up to 110 cm']);
  assert(options.every(option => option.message.includes('maximum width')));
});

test('empty results offer explicit removal of existing constraints', () => {
  const options = buildFilterSuggestions({ products: [], request: { filters: { max_price: 5000, colors: ['beige'] } } },
    'Would you like to broaden a requirement?');
  assert.deepEqual(options.map(option => option.label), ['Remove budget limit', 'Any colour']);
});

test('fact-only responses and missing search results do not show filter links', () => {
  assert.deepEqual(buildFilterSuggestions(search, 'This sofa is 250 cm long.'), []);
  assert.deepEqual(buildFilterSuggestions(null, 'What budget?'), []);
});

test('budget links do not treat other currencies as interchangeable', () => {
  const options = buildFilterSuggestions({ ...search, request: { filters: { currency: 'USD' } } }, 'What budget?');
  assert.deepEqual(options, []);
});
