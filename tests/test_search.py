import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from main import app
from product_search import Product, ProductSearcher, ProductSearchRequest, SearchResult, SearchUnavailable


class SearchTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.env = patch.dict('os.environ', {
            'ES_URL': 'https://search.example', 'ES_INDEX': 'products',
            'ES_SEARCH_COLLAPSE_FIELD': 'parent_id', 'ES_API_KEY': '',
            'ES_USERNAME': 'test', 'ES_PASSWORD': 'test',
        })
        self.env.start()
        self.searcher = ProductSearcher()
        # These tests cover the primary search; family expansion is tested separately.
        self.searcher.attach_color_options = AsyncMock()
        await self.searcher.client.aclose()

    async def asyncTearDown(self):
        await self.searcher.close()
        self.env.stop()

    def transport(self, handler):
        self.searcher.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    async def test_query_matches_sample_and_products_are_normalized(self):
        def handler(request):
            self.assertEqual(request.url.path, '/_msearch')
            self.assertTrue(request.content.endswith(b'\n'))
            header, body = map(json.loads, request.content.decode().strip().split('\n'))
            self.assertEqual(header['index'], 'products')
            self.assertEqual(body['collapse']['field'], 'parent_id')
            self.assertEqual(body['from'], 6)
            self.assertEqual(body['size'], 6)
            query = body['query']['bool']['must'][0]['multi_match']
            self.assertIn('title.text^3', query['fields'])
            self.assertEqual(query['operator'], 'and')
            self.assertEqual(query['fuzziness'], 'AUTO')
            return httpx.Response(200, json={'responses': [{
                'hits': {'total': {'value': 12}, 'hits': [{'_source': {
                    'sku': 'SOFA-1', 'title': 'Cloud', 'price': '1,214 AED',
                    'sale_price': '900 AED', 'stock': 3, 'parent_id': 'P1',
                    'short_description': '<p>Soft <b>fabric</b></p>',
                    'link': 'https://www.alhuzaifa.com/sofa', 'image_link': 'javascript:bad',
                }}]}, 'aggregations': {'unique_recs_count': {'value': 8}},
            }]})
        self.transport(handler)
        result = await self.searcher.search(ProductSearchRequest(query='sofa', page=2, limit=6))
        self.assertEqual(result.total, 8)
        self.assertEqual(result.pages, 2)
        self.assertEqual(result.products[0].description, 'Soft fabric')
        self.assertEqual(result.products[0].stock, 3)
        self.assertEqual(result.products[0].effective_price, 900)
        self.assertEqual(result.products[0].display_price, '900 AED')
        self.assertEqual(result.products[0].price, '1,214 AED')
        self.assertIsNone(result.products[0].image_url)

    async def test_empty_results(self):
        self.transport(lambda _: httpx.Response(200, json={'responses': [{'hits': {'total': 0, 'hits': []}}]}))
        result = await self.searcher.search(ProductSearchRequest(query='unfindable'))
        self.assertEqual(result.products, [])
        self.assertEqual(result.total, 0)

    async def test_http_embedded_and_partial_errors_are_sanitized(self):
        for status, payload in [(401, {'error': 'secret'}), (200, {'responses': [{'error': 'secret'}]}),
                                (200, {'responses': [{'timed_out': True}]}),
                                (200, {'responses': [{'_shards': {'failed': 1}}]})]:
            with self.subTest(status=status, payload=payload):
                self.transport(lambda _, status=status, payload=payload: httpx.Response(status, json=payload))
                with self.assertRaises(SearchUnavailable) as error:
                    await self.searcher.search(ProductSearchRequest(query='sofa'))
                self.assertNotIn('secret', str(error.exception))
                await self.searcher.client.aclose()


class ToolChatTests(unittest.IsolatedAsyncioTestCase):
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

    def setup_tool(self, arguments=None, error=None):
        result = SearchResult(query='sofa', products=[Product(id='S1', title='Cloud', price='900 AED')], total=1, page=1, pages=1)
        self.search = AsyncMock(return_value=result, side_effect=error)
        app.state.searcher = SimpleNamespace(search=self.search, close=AsyncMock(), configured=True)
        call = SimpleNamespace(type='function_call', name='search_products', call_id='call_1',
                               arguments=arguments or '{"query":"sofa","page":1,"limit":6}')
        self.create = AsyncMock(side_effect=[
            SimpleNamespace(status='completed', output=[call], output_text=''),
            SimpleNamespace(status='completed', output=[], output_text='Here are your results.'),
        ])
        app.state.openai = SimpleNamespace(responses=SimpleNamespace(create=self.create), close=AsyncMock())

    async def test_tool_results_reach_model_and_ui(self):
        self.setup_tool()
        response = await self.client.post('/api/chat', json={'messages': [{'role': 'user', 'content': 'Show sofas'}]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['search']['products'][0]['id'], 'S1')
        output = self.create.call_args_list[1].kwargs['input'][-1]
        self.assertEqual(output['type'], 'function_call_output')
        self.assertEqual(output['call_id'], 'call_1')
        self.assertEqual(json.loads(output['output'])['products'][0]['price'], '900 AED')

    async def test_tool_validation_blocks_unbounded_queries(self):
        self.setup_tool('{"query":"sofa","limit":999,"page":1}')
        response = await self.client.post('/api/chat', json={'messages': [{'role': 'user', 'content': 'Show sofas'}]})
        self.assertEqual(response.status_code, 200)
        self.search.assert_not_awaited()
        self.assertIn('error', json.loads(self.create.call_args_list[1].kwargs['input'][-1]['output']))

    async def test_search_failure_is_visible(self):
        self.setup_tool(error=SearchUnavailable('Product search is temporarily unavailable.'))
        response = await self.client.post('/api/chat', json={'messages': [{'role': 'user', 'content': 'Show sofas'}]})
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()['search'])
        self.assertIn('unavailable', response.json()['search_error'])

    async def test_active_filters_are_available_to_the_model_on_followup(self):
        self.setup_tool()
        response = await self.client.post('/api/chat', json={
            'messages': [{'role': 'user', 'content': 'Only leather ones'}],
            'search_state': {'query': '', 'filters': {'categories': ['sofa'], 'colors': ['beige'], 'max_price': 5000}, 'sort': 'price_asc'},
        })
        self.assertEqual(response.status_code, 200)
        instructions = self.create.call_args_list[0].kwargs['instructions']
        self.assertIn('Active search context', instructions)
        self.assertIn('"max_price":5000.0', instructions)
        self.assertIn('"colors":["beige"]', instructions)

    async def test_search_endpoint_and_input_validation(self):
        self.setup_tool()
        self.assertEqual((await self.client.post('/api/search', json={'query': 'sofa'})).status_code, 200)
        for payload in [{'query': 'sofa', 'filters': {'colors': [' ']}}, {'query': 'sofa', 'limit': 100}, {'query': 'sofa', 'page': 0},
                        {'query': 'sofa', 'index': 'other'}]:
            self.assertEqual((await self.client.post('/api/search', json=payload)).status_code, 422)

    async def test_category_change_clears_old_filters_before_search(self):
        self.setup_tool('{"query":"","intent":"new_search","explicit_filters":["categories"],"filters":{"categories":["tables"],"colors":["Slate grey"],"materials":["Leather"],"max_price":4000}}')
        response = await self.client.post('/api/chat',json={
            'messages':[{'role':'user','content':'show me tables'}],
            'search_state':{'filters':{'categories':['sofas'],'colors':['Slate grey'],'materials':['Leather'],'max_price':4000}},
        })
        self.assertEqual(response.status_code,200)
        actual = self.search.call_args.args[0]
        self.assertEqual(actual.filters.categories,['tables'])
        self.assertEqual(actual.filters.colors,[])
        self.assertEqual(actual.filters.materials,[])
        self.assertIsNone(actual.filters.max_price)
