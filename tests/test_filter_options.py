import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from agent_actions import AgentSession
from product_search import ProductSearcher, ProductSearchRequest, SearchUnavailable


class FilterOptionsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.env = patch.dict('os.environ', {
            'ES_URL': 'https://search.example', 'ES_INDEX': 'products', 'ES_REGION_FIELD': '',
        })
        self.env.start()
        self.searcher = ProductSearcher()
        await self.searcher.client.aclose()

    async def asyncTearDown(self):
        await self.searcher.close()
        self.env.stop()

    def transport(self, handler):
        self.searcher.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    async def test_options_cover_all_matches_and_keep_current_constraints(self):
        params = ProductSearchRequest(query='sofa', region='SA', page=7, limit=1,
                                      filters={'max_price': 5000, 'colors': ['beige']})
        original = params.model_dump()
        expected, *_ = self.searcher.search_body(params)

        def handler(request):
            body = json.loads(request.content.decode().strip().split('\n')[1])
            self.assertEqual(body['query'], expected['query'])
            self.assertEqual(body['size'], 0)
            self.assertEqual(body['from'], 0)
            self.assertNotIn('collapse', body)
            self.assertNotIn('sort', body)
            self.assertEqual(body['aggs']['colors']['terms']['field'], 'region_sa_en.color.keyword')
            self.assertEqual(body['runtime_mappings']['chat_price']['script']['params']['region'], 'region_sa_en')
            return httpx.Response(200, json={'responses': [{
                'hits': {'total': {'value': 123}, 'hits': []},
                'aggregations': {
                    'colors': {'buckets': [{'key': 'beige', 'doc_count': 123}], 'sum_other_doc_count': 0},
                    'materials': {'buckets': [{'key': 'leather', 'doc_count': 40}], 'sum_other_doc_count': 10},
                    'prices_by_currency': {'buckets': [{'key': 'SAR', 'price': {
                        'count': 123, 'min': 1000, 'max': 4900, 'avg': 2500, 'sum': 307500}}]},
                    'length_cm': {'count': 100, 'min': 150, 'max': 300, 'avg': 200, 'sum': 20000},
                    'on_sale': {'buckets': [{'key': 1, 'doc_count': 20}, {'key': 0, 'doc_count': 103}]},
                },
            }]})

        self.transport(handler)
        result = await self.searcher.available_filters(params)
        self.assertEqual(result['matched_variants'], 123)
        self.assertEqual(result['options']['materials']['values'], [{'value': 'leather', 'matching_variants': 40}])
        self.assertEqual(result['options']['materials']['other_variant_count'], 10)
        self.assertEqual(result['prices_by_currency'][0]['currency'], 'SAR')
        self.assertEqual(result['prices_by_currency'][0]['max'], 4900)
        self.assertEqual(result['dimensions_cm']['length']['min'], 150)
        self.assertEqual(result['on_sale'][0]['value'], True)
        self.assertEqual(params.model_dump(), original)

    async def test_discovery_does_not_require_price_or_dimension_values_to_match(self):
        def handler(request):
            body = json.loads(request.content.decode().strip().split('\n')[1])
            self.assertEqual(body['query']['bool']['filter'], [{'range': {'stock': {'gt': 0}}}])
            for field in ['chat_price', 'chat_currency', 'chat_on_sale', 'chat_length_cm', 'chat_width_cm', 'chat_height_cm']:
                self.assertIn(field, body['runtime_mappings'])
            return httpx.Response(200, json={'responses': [{'hits': {'total': 0}, 'aggregations': {}}]})

        self.transport(handler)
        result = await self.searcher.available_filters(ProductSearchRequest(query='sofa'))
        self.assertEqual(result['matched_variants'], 0)
        self.assertEqual(result['options']['colors']['values'], [])
        self.assertEqual(result['prices_by_currency'], [])

    async def test_failed_and_partial_discovery_are_reported(self):
        for payload in [{'error': {'reason': 'private upstream details'}}, {'timed_out': True}, {'_shards': {'failed': 1}}]:
            self.transport(lambda request: httpx.Response(200, json={'responses': [payload]}))
            with self.assertRaisesRegex(SearchUnavailable, '^Available search filters are temporarily unavailable'):
                await self.searcher.available_filters(ProductSearchRequest(query='sofa'))
            await self.searcher.client.aclose()

    async def test_filter_discovery_only_returns_evidence_to_model(self):
        preferences = ProductSearchRequest(query='sofa', filters={'max_price': 3000})
        session = AgentSession(preferences)
        evidence = {'matched_variants': 10, 'options': {'colors': {'values': [{'value': 'beige', 'matching_variants': 5}]}}}
        searcher = SimpleNamespace(available_filters=AsyncMock(return_value=evidence))
        result, events = await session.execute('get_available_filters', preferences.model_dump_json(), searcher)
        self.assertEqual(result, evidence)
        self.assertEqual(searcher.available_filters.call_args.args[0], preferences)
        self.assertEqual(events, [])
        self.assertIs(session.preferences, preferences)
        self.assertIsNone(session.presentation)
        self.assertEqual(session.retrievals, {})
