from model_fixtures import answer
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from main import ChatRequest, chat_events
from product_search import Product, SearchResult, SearchUnavailable


def response(call=None):
    return SimpleNamespace(status='completed', output=[call] if call else [],
                           output_text='' if call else answer('Here are a few tables to consider.'))


def call(name, arguments, identifier):
    return SimpleNamespace(type='function_call', name=name, arguments=json.dumps(arguments), call_id=identifier)


class ProductDetailsTests(unittest.IsolatedAsyncioTestCase):
    async def run_pairing(self, lookup_first, lookup_fails=False):
        reference = call('get_product_details', {'query':'HAZEL-SKU', 'region':'AE'}, 'reference')
        target = call('search_products', {'query':'coffee table'}, 'target')
        ordered = [reference, target] if lookup_first else [target, reference]
        create = AsyncMock(side_effect=[response(item) for item in ordered] + [response()])
        client = SimpleNamespace(responses=SimpleNamespace(create=create))

        async def search(params):
            if params.query == 'HAZEL-SKU':
                if lookup_fails:
                    raise SearchUnavailable('Reference lookup unavailable.')
                products = [Product(id='HAZEL-SKU', title='Hazel', category='Sofa', color='Beige')]
            else:
                products = [Product(id='TABLE', title='Table', category='Coffee Tables')]
            return SearchResult(query=params.query, products=products, total=1, page=1, pages=1, request=params)

        searcher = SimpleNamespace(search=AsyncMock(side_effect=search))
        payload = ChatRequest(messages=[{'role':'user','content':'What coffee table goes with Hazel?'}],
                              search_state={'query':'sofa','region':'AE','filters':{'max_price':5000}})
        events = [event async for event in chat_events(payload, client, searcher)]
        self.assertFalse(any(event['type'] in ('presentation', 'preferences') for event in events))
        final = events[-1]['data']
        self.assertIsNone(final['presentation'])
        self.assertEqual(final['search_state']['query'], 'sofa')
        self.assertEqual(final['search_state']['filters']['max_price'], 5000)
        lookup_params = next(args.args[0] for args in searcher.search.call_args_list if args.args[0].query == 'HAZEL-SKU')
        self.assertEqual(lookup_params.region, 'AE')
        self.assertIsNone(lookup_params.filters.max_price)
        tool_outputs = [item for item in create.call_args_list[-1].kwargs['input']
                        if isinstance(item, dict) and item.get('type') == 'function_call_output']
        reference_output = next(item for item in tool_outputs if item['call_id'] == 'reference')
        if not lookup_fails:
            self.assertIn('retrieval_id', json.loads(reference_output['output']))

    async def test_retrievals_do_not_display_products_or_change_preferences(self):
        await self.run_pairing(lookup_first=True)

    async def test_later_retrieval_does_not_display_products(self):
        await self.run_pairing(lookup_first=False)

    async def test_failed_lookup_does_not_change_customer_preferences(self):
        await self.run_pairing(lookup_first=False, lookup_fails=True)
