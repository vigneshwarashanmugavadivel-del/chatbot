const { test } = require('node:test');
const assert = require('node:assert/strict');
const { buildFollowUpSuggestions } = require('../static/follow-ups.js');

test('four contextual questions are rendered as their exact next messages', () => {
  const questions = ['How do these compare?', 'What goes with these?', 'Can I see other styles?', 'Which is easiest to clean?'];
  const choices = buildFollowUpSuggestions(questions);
  assert.equal(choices.length, 4);
  assert.deepEqual(choices.map(choice => choice.label), questions);
  assert.deepEqual(choices.map(choice => choice.message), questions);
});

test('suggestions work without search results or a question in the reply', () => {
  assert.equal(buildFollowUpSuggestions(['How can I decorate my room?']).length, 1);
});

test('missing suggestions are safe and duplicate or blank questions are excluded', () => {
  assert.deepEqual(buildFollowUpSuggestions(null), []);
  assert.deepEqual(buildFollowUpSuggestions(['Hello?', ' hello? ', '', null]), [{ label: 'Hello?', message: 'Hello?' }]);
});

test('at most four questions are rendered and markup remains plain text', () => {
  const choices = buildFollowUpSuggestions(['<script>hello</script>', 'Two?', 'Three?', 'Four?', 'Five?']);
  assert.equal(choices.length, 4);
  assert.equal(choices[0].label, '<script>hello</script>');
});
