import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from agent_actions import AgentSession, AGENT_TOOLS
from product_search import Product, ProductSearchRequest, SearchResult


def catalog_result(identifier='A', region=None, **values):
    return SearchResult(query='catalog', products=[Product(id=identifier, title=identifier, stock=2, **values)],
                        total=1, page=1, pages=1, region=region)


class ActionTests(unittest.IsolatedAsyncioTestCase):
    async def test_model_selects_up_to_fifteen_from_a_larger_retrieval(self):
        session = AgentSession()
        products = [Product(id=str(i), title=f'Product {i}', stock=2) for i in range(24)]
        source = SearchResult(query='catalog', products=products, total=24, page=1, pages=1, limit=24)
        searcher = SimpleNamespace(search=AsyncMock(return_value=source))
        data, events = await session.execute('search_products', '{"limit":24}', searcher)
        self.assertEqual(searcher.search.call_args.args[0].limit, 24)
        self.assertEqual(len(data['products']), 24)
        self.assertEqual(events, [])
        references = [{'retrieval_id':data['retrieval_id'], 'product_id':str(i)} for i in range(23, 6, -1)]
        for presentation in ['recommendations', 'comparison']:
            arguments = {'presentation':presentation, 'products':references[:15], 'fields':['material']}
            result, _ = await session.execute('display_products', json.dumps(arguments), None)
            self.assertEqual(result['product_ids'], [str(i) for i in range(23, 8, -1)])
            previous = session.presentation
            arguments['products'] = references[:16]
            with self.assertRaises(ValueError):
                await session.execute('display_products', json.dumps(arguments), None)
            self.assertIs(session.presentation, previous)

    async def test_retrieval_is_evidence_only_with_an_immutable_snapshot(self):
        preferences = ProductSearchRequest(query='previous', filters={'max_price':1000})
        source = catalog_result(material='Linen')
        session = AgentSession(preferences)
        data, events = await session.execute('search_products', '{"query":"investigation"}',
            SimpleNamespace(search=AsyncMock(return_value=source)))
        self.assertEqual(data['retrieval_id'], 'retrieval_1')
        self.assertEqual(events, [])
        self.assertIsNone(session.presentation)
        self.assertEqual(session.preferences, preferences)
        source.products[0].material = 'changed'
        self.assertEqual(session.retrievals['retrieval_1'].products[0].material, 'Linen')

    async def test_explicit_display_preserves_model_order_and_retrieval_context(self):
        session = AgentSession()
        session.remember(catalog_result('A', region='AE'), ProductSearchRequest(region='AE'))
        session.remember(catalog_result('B', region='SA'), ProductSearchRequest(region='SA'))
        result, events = await session.execute('display_products', json.dumps({'presentation':'recommendations', 'products':[
            {'retrieval_id':'retrieval_2','product_id':'B'}, {'retrieval_id':'retrieval_1','product_id':'A'}]}), None)
        self.assertEqual(result['product_ids'], ['B','A'])
        self.assertEqual(events[0]['type'], 'presentation')
        self.assertEqual([item.request.region for item in session.presentation.items], ['SA','AE'])
        self.assertIsNone(session.preferences)

    async def test_unknown_reference_does_not_destroy_a_successful_presentation(self):
        session = AgentSession()
        session.remember(catalog_result(), ProductSearchRequest())
        await session.execute('display_products', json.dumps({'presentation':'recommendations','products':[
            {'retrieval_id':'retrieval_1','product_id':'A'}]}), None)
        previous = session.presentation
        for reference in [{'retrieval_id':'retrieval_1','product_id':'INVENTED'},
                          {'retrieval_id':'another_turn','product_id':'A'}]:
            with self.assertRaises(ValueError):
                await session.execute('display_products', json.dumps({'presentation':'recommendations','products':[reference]}), None)
            self.assertIs(session.presentation, previous)

    async def test_comparison_uses_only_catalog_values_and_known_fields(self):
        session = AgentSession()
        session.remember(catalog_result('A', material='Linen'), ProductSearchRequest())
        session.remember(catalog_result('B', material='Leather'), ProductSearchRequest())
        arguments = {'presentation':'comparison','products':[
            {'retrieval_id':'retrieval_1','product_id':'A'}, {'retrieval_id':'retrieval_2','product_id':'B'}],
            'fields':['material','dimension']}
        await session.execute('display_products', json.dumps(arguments), None)
        self.assertEqual(session.presentation.items[0].product.material, 'Linen')
        self.assertIsNone(session.presentation.items[0].product.dimension)
        arguments['fields'] = ['invented_durability_rating']
        with self.assertRaises(ValueError):
            await session.execute('display_products', json.dumps(arguments), None)

    async def test_recommendation_cards_ignore_unused_comparison_columns(self):
        session = AgentSession()
        session.remember(catalog_result('A', material='Linen'), ProductSearchRequest())
        result, events = await session.execute('display_products', json.dumps({
            'presentation':'recommendations', 'title':'Options',
            'products':[{'retrieval_id':'retrieval_1', 'product_id':'A'}],
            'fields':['description','color','display_price','dimension'],
        }), None)
        self.assertEqual(result['product_ids'], ['A'])
        self.assertEqual(result['fields'], [])
        self.assertEqual(events[0]['presentation']['kind'], 'recommendations')
        self.assertEqual(events[0]['presentation']['items'][0]['product']['material'], 'Linen')

    async def test_comparison_still_requires_columns_and_multiple_products(self):
        session = AgentSession()
        session.remember(catalog_result('A'), ProductSearchRequest())
        session.remember(catalog_result('B'), ProductSearchRequest())
        references = [{'retrieval_id':'retrieval_1','product_id':'A'},
                      {'retrieval_id':'retrieval_2','product_id':'B'}]
        for products, fields in [(references, []), (references[:1], ['material'])]:
            with self.assertRaises(ValueError):
                await session.execute('display_products', json.dumps({
                    'presentation':'comparison', 'products':products, 'fields':fields,
                }), None)
        self.assertIsNone(session.presentation)

    async def test_unavailable_product_cannot_be_displayed(self):
        session = AgentSession()
        source = catalog_result()
        source.products[0].stock = 0
        session.remember(source, ProductSearchRequest())
        with self.assertRaises(ValueError):
            await session.execute('display_products', json.dumps({'presentation':'recommendations','products':[
                {'retrieval_id':'retrieval_1','product_id':'A'}]}), None)

    async def test_preferences_update_and_clear_are_explicit_and_independent(self):
        session = AgentSession()
        _, events = await session.execute('update_preferences', '{"search_state":{"query":"tables","filters":{"max_price":2000}}}', None)
        self.assertEqual(session.preferences.filters.max_price, 2000)
        self.assertIsNone(session.presentation)
        self.assertEqual(events[0]['type'], 'preferences')
        await session.execute('update_preferences', '{"search_state":null}', None)
        self.assertIsNone(session.preferences)

    async def test_text_action_clears_current_turn_visuals(self):
        session = AgentSession()
        await session.execute('display_products', '{"presentation":"text"}', None)
        self.assertEqual(session.presentation.kind, 'text')
        self.assertEqual(session.presentation.items, [])

    async def test_similarity_retrieval_has_no_display_or_preference_side_effect(self):
        session = AgentSession()
        searcher = SimpleNamespace(similar_products=AsyncMock(return_value=catalog_result()))
        result, events = await session.execute('find_similar_products', '{"product_id":"SOURCE"}', searcher)
        self.assertEqual(result['retrieval_id'], 'retrieval_1')
        self.assertEqual(events, [])
        self.assertIsNone(session.preferences)
        self.assertIsNone(session.presentation)

    async def test_all_action_schemas_are_strict(self):
        def inspect(value):
            if isinstance(value, dict):
                if value.get('type') == 'object':
                    self.assertFalse(value['additionalProperties'])
                    self.assertEqual(set(value['required']), set(value['properties']))
                for item in value.values(): inspect(item)
            elif isinstance(value, list):
                for item in value: inspect(item)
        for tool in AGENT_TOOLS:
            inspect(tool['parameters'])
