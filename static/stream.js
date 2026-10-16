// Read SSE over POST/fetch, preserving UTF-8 and frames split across network chunks.
async function readChatStream(response, onEvent) {
  if (!response.body) throw new Error('Streaming is unavailable in this browser.');
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let dataLines = [];
  try {
    while (true) {
      const { value, done } = await reader.read();
      buffer += done ? decoder.decode() : decoder.decode(value, { stream: true });
      let newline;
      while ((newline = buffer.indexOf('\n')) !== -1) {
        const line = buffer.slice(0, newline).replace(/\r$/, '');
        buffer = buffer.slice(newline + 1);
        if (line === '' && dataLines.length) {
          const event = JSON.parse(dataLines.join('\n'));
          dataLines = [];
          if (event.type === 'error') throw new Error(event.message || 'The reply was interrupted. Please try again.');
          if (event.type === 'done') return event.data;
          onEvent(event);
        } else if (line.startsWith('data:')) {
          dataLines.push(line.slice(5).replace(/^ /, ''));
        }
      }
      if (done) throw new Error('The reply was interrupted. Please try again.');
    }
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}

if (typeof module !== 'undefined') module.exports = { readChatStream };
