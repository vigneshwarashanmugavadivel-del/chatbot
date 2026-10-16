const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readChatStream } = require('../static/stream.js');

function responseFrom(text, chunkSize = 1) {
  const bytes = new TextEncoder().encode(text);
  return new Response(new ReadableStream({
    start(controller) {
      for (let i = 0; i < bytes.length; i += chunkSize) controller.enqueue(bytes.slice(i, i + chunkSize));
      controller.close();
    },
  }));
}

test('handles split UTF-8 characters, CRLF frames, and multiple events per chunk', async () => {
  const wire = ': heartbeat\r\n\r\ndata: {"type":"delta","text":"Hi 🌿 مرحبا"}\r\n\r\n'
    + 'data: {"type":"done","data":{"reply":"Hi 🌿 مرحبا"}}\r\n\r\n';
  for (const size of [1, 7, 10000]) {
    const events = [];
    const data = await readChatStream(responseFrom(wire, size), (event) => events.push(event));
    assert.equal(events[0].text, 'Hi 🌿 مرحبا');
    assert.equal(data.reply, 'Hi 🌿 مرحبا');
  }
});

test('rejects a truncated reply without a done event', async () => {
  const wire = 'data: {"type":"delta","text":"Partial"}\n\n';
  await assert.rejects(readChatStream(responseFrom(wire), () => {}), /interrupted/);
});

test('propagates a readable server error after partial text', async () => {
  const wire = 'data: {"type":"delta","text":"Partial"}\n\n'
    + 'data: {"type":"error","message":"Search timed out"}\n\n';
  await assert.rejects(readChatStream(responseFrom(wire), () => {}), /Search timed out/);
});

test('cancels the network reader when processing fails', async () => {
  let cancelled = false;
  const response = new Response(new ReadableStream({
    start(controller) { controller.enqueue(new TextEncoder().encode('data: bad-json\n\n')); },
    cancel() { cancelled = true; },
  }));
  await assert.rejects(readChatStream(response, () => {}));
  assert.equal(cancelled, true);
});
