import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from main import ChatRequest, app, chat_events
from product_search import Product, SearchResult, SearchUnavailable


class FakeStream:
    def __init__(self, events):
        self.events = events
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.closed = True

    async def __aiter__(self):
        for event in self.events:
            if isinstance(event, Exception):
                raise event
            yield event


def text_stream(text='Hello there'):
    return FakeStream([
        SimpleNamespace(type='response.output_text.delta', delta=text[:5]),
        SimpleNamespace(type='response.output_text.delta', delta=text[5:]),
        SimpleNamespace(type='response.completed', response=SimpleNamespace(
            status='completed', output=[], output_text=text)),
    ])


class StreamingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.env = patch.dict('os.environ', {'OPENAI_API_KEY': ''})
        self.env.start()
        self.lifespan = app.router.lifespan_context(app)
        await self.lifespan.__aenter__()
        self.real_searcher = app.state.searcher
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test')

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.real_searcher.close()
        await self.lifespan.__aexit__(None, None, None)
        self.env.stop()

    def configure(self, streams):
        create = AsyncMock(side_effect=streams)
        app.state.openai = SimpleNamespace(responses=SimpleNamespace(create=create), close=AsyncMock())
        return create

    async def events(self):
        response = await self.client.post('/api/chat/stream', json={'messages': [{'role': 'user', 'content': 'Hello'}]})
        self.assertEqual(response.status_code, 200)
        self.assertIn('text/event-stream', response.headers['content-type'])
        self.assertEqual(response.headers['x-accel-buffering'], 'no')
        return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]

    async def test_text_deltas_and_final_response(self):
        stream = text_stream()
        create = self.configure([stream])
        events = await self.events()
        self.assertEqual(''.join(e['text'] for e in events if e['type'] == 'delta'), 'Hello there')
        self.assertEqual(events[-1]['type'], 'done')
        self.assertEqual(events[-1]['data']['reply'], 'Hello there')
        self.assertTrue(create.call_args.kwargs['stream'])
        self.assertTrue(stream.closed)

    async def test_search_cards_are_sent_before_final_text(self):
        call = SimpleNamespace(type='function_call', name='search_products', call_id='call1',
                               arguments='{"query":"sofa","filters":{"max_price":5000}}')
        first = FakeStream([SimpleNamespace(type='response.completed', response=SimpleNamespace(
            status='completed', output=[call], output_text=''))])
        create = self.configure([first, text_stream('Try Cloud')])
        result = SearchResult(query='sofa', products=[Product(id='S1', title='Cloud', stock=2)],
                              total=1, page=1, pages=1)
        app.state.searcher = SimpleNamespace(search=AsyncMock(return_value=result), close=AsyncMock())
        events = await self.events()
        types = [e['type'] for e in events]
        self.assertLess(types.index('search'), types.index('delta'))
        self.assertEqual(events[-1]['data']['search']['products'][0]['stock'], 2)
        self.assertEqual(create.call_args_list[1].kwargs['input'][-1]['call_id'], 'call1')

    async def test_search_failure_remains_visible(self):
        call = SimpleNamespace(type='function_call', name='search_products', call_id='call1', arguments='{"query":"sofa"}')
        self.configure([FakeStream([SimpleNamespace(type='response.completed', response=SimpleNamespace(
            status='completed', output=[call], output_text=''))]), text_stream('Search is unavailable.')])
        app.state.searcher = SimpleNamespace(search=AsyncMock(side_effect=SearchUnavailable('Search unavailable.')), close=AsyncMock())
        events = await self.events()
        search = next(e for e in events if e['type'] == 'search')
        self.assertIsNone(search['search'])
        self.assertEqual(events[-1]['data']['search_error'], 'Search unavailable.')

    async def test_truncated_and_failed_upstream_streams_are_not_completed(self):
        for terminal in [None, 'response.incomplete', 'response.failed', 'error']:
            events = [SimpleNamespace(type='response.output_text.delta', delta='Partial')]
            if terminal:
                events.append(SimpleNamespace(type=terminal))
            stream = FakeStream(events)
            self.configure([stream])
            result = await self.events()
            self.assertEqual(result[-1]['type'], 'error')
            self.assertFalse(any(e['type'] == 'done' for e in result))
            self.assertTrue(stream.closed)

    async def test_preflight_errors_are_http_errors(self):
        response = await self.client.post('/api/chat/stream', json={'messages': [{'role': 'user', 'content': 'Hello'}]})
        self.assertEqual(response.status_code, 503)
        response = await self.client.post('/api/chat/stream', json={'messages': []})
        self.assertEqual(response.status_code, 422)

    async def test_delta_is_available_before_completion_and_close_releases_stream(self):
        stream = text_stream()
        self.configure([stream])
        generator = chat_events(ChatRequest(messages=[{'role': 'user', 'content': 'Hello'}]),
                                app.state.openai, app.state.searcher, streaming=True)
        self.assertEqual((await anext(generator))['type'], 'reset')
        self.assertEqual((await anext(generator))['type'], 'status')
        event = await anext(generator)
        self.assertEqual(event, {'type': 'delta', 'text': 'Hello'})
        self.assertFalse(stream.closed)
        await generator.aclose()
        self.assertTrue(stream.closed)
