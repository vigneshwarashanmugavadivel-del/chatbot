import json
import unittest
from unittest.mock import patch

import httpx

from main import app
from product_search import Product, ProductSearcher, ProductSearchRequest, VariantRequest, VariantUnavailable


def hit(sku, color, stock=2, family='FAMILY', **extra):
    return {'_source': {'sku': sku, 'title': 'Riana', 'parent_id': family, 'item_group_id': family,
                        'color': color, 'stock': stock, 'price': '2,000 AED', 'sale_price': '1,500 AED', **extra}}


class VariantTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.env = patch.dict('os.environ', {'ES_URL': 'https://search.example', 'ES_INDEX': 'products', 'ES_REGION_FIELD': ''})
        self.env.start()
        self.searcher = ProductSearcher()
        await self.searcher.client.aclose()

    async def asyncTearDown(self):
        await self.searcher.close()
        self.env.stop()

    def transport(self, handler):
        self.searcher.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    def bodies(self, request):
        lines = request.content.decode().strip().split('\n')
        return [json.loads(line) for line in lines[1::2]]

    async def test_color_lookup_keeps_filters_and_batches_families(self):
        products = [Product(id='ONE', title='Riana', parent_id='FAMILY', color='Beige', stock=2),
                    Product(id='SECOND', title='Mellow', parent_id='OTHER', color='Grey', stock=2)]
        params = ProductSearchRequest(query='beige sofa', filters={'colors': ['beige'], 'max_price': 5000, 'materials': ['fabric']})
        calls = []
        def handler(request):
            bodies = self.bodies(request)
            calls.append(bodies)
            self.assertEqual(len(bodies), 2)
            for body in bodies:
                constraints = body['query']['bool']['filter']
                self.assertIn({'range': {'stock': {'gt': 0}}}, constraints)
                self.assertIn({'range': {'chat_price': {'lte': 5000}}}, constraints)
                self.assertIn('materials_and_finish.text', json.dumps(constraints))
                self.assertNotIn('match_phrase": {"color.text', json.dumps(constraints))
                self.assertEqual(body['collapse']['field'], 'color.keyword')
            return httpx.Response(200, json={'responses': [
                {'hits': {'hits': [hit('ONE', 'Beige'), hit('TWO', 'Brown'), hit('DUP', 'brown'),
                                   hit('SOLD', 'Black', 0), hit('BAD', 'Red', family='WRONG')]}},
                {'hits': {'hits': [hit('SECOND', 'Grey', family='OTHER')]}},
            ]})
        self.transport(handler)
        await self.searcher.attach_color_options(products, params)
        self.assertEqual(len(calls), 1)
        self.assertEqual([(o.id, o.color) for o in products[0].color_options], [('ONE','Beige'), ('TWO','Brown')])
        self.assertEqual(params.filters.colors, ['beige'])

    async def test_empty_family_never_matches_unrelated_products(self):
        products = [Product(id='SINGLE', title='Sofa', parent_id='', group_id='', color='Beige', stock=1)]
        def handler(_):
            self.fail('An empty family must not trigger a shared-family query')
        self.transport(handler)
        await self.searcher.attach_color_options(products, ProductSearchRequest())
        self.assertEqual([o.id for o in products[0].color_options], ['SINGLE'])

    async def test_parent_without_colour_displays_the_pictured_variant_with_matching_details(self):
        parent = Product(id='PARENT', title='Riana', parent_id='FAMILY', color=None, stock=2,
                         image_url='https://example.com/grey.jpg', display_price='999 AED')
        def handler(_):
            return httpx.Response(200, json={'responses': [{'hits': {'hits': [
                hit('BEIGE', 'Beige', image_link='https://example.com/beige.jpg'),
                hit('GREY', 'Grey', image_link='https://example.com/grey.jpg', sale_price='1,800 AED'),
            ]}}]})
        self.transport(handler)
        await self.searcher.attach_color_options([parent], ProductSearchRequest())
        self.assertEqual(parent.id, 'GREY')
        self.assertEqual(parent.color, 'Grey')
        self.assertEqual(parent.image_url, 'https://example.com/grey.jpg')
        self.assertEqual(parent.display_price, '1,800 AED')
        self.assertEqual(next(o for o in parent.color_options if o.color == parent.color).id, parent.id)

    async def test_parent_with_generic_image_uses_actual_variant_image_instead_of_guessing_colour(self):
        parent = Product(id='PARENT', title='Riana', parent_id='FAMILY', color=None, stock=2,
                         image_url='https://example.com/generic.jpg')
        self.transport(lambda _: httpx.Response(200, json={'responses': [{'hits': {'hits': [
            hit('BEIGE', 'Beige', image_link='https://example.com/beige.jpg'),
            hit('GREY', 'Grey', image_link='https://example.com/grey.jpg'),
        ]}}]}))
        await self.searcher.attach_color_options([parent], ProductSearchRequest())
        self.assertEqual(parent.id, 'BEIGE')
        self.assertEqual(parent.color, 'Beige')
        self.assertEqual(parent.image_url, 'https://example.com/beige.jpg')

    async def test_variant_replaces_product_data_and_preserves_constraints(self):
        step = 0
        def handler(request):
            nonlocal step
            step += 1
            body = self.bodies(request)[0]
            clauses = body['query']['bool']['filter']
            self.assertIn({'range': {'stock': {'gt': 0}}}, clauses)
            self.assertIn({'range': {'chat_price': {'lte': 5000}}}, clauses)
            if step == 1:
                self.assertIn({'term': {'sku.keyword': 'one'}}, clauses)
                hits = [hit('ONE','Beige')]
            elif step == 2:
                self.assertIn({'term': {'sku.keyword': 'two'}}, clauses)
                self.assertIn({'term': {'parent_id.keyword': 'family'}}, clauses)
                hits = [hit('TWO','Brown', sale_price='1,800 AED', image_link='https://example.com/brown.jpg')]
            else:
                hits = [hit('ONE','Beige'), hit('TWO','Brown')]
            return httpx.Response(200, json={'responses':[{'hits': {'hits':hits}}]})
        self.transport(handler)
        product = await self.searcher.select_variant(VariantRequest(product_id='ONE',variant_id='TWO',
            search_state=ProductSearchRequest(filters={'colors':['beige'],'max_price':5000})))
        self.assertEqual(product.id, 'TWO')
        self.assertEqual(product.color, 'Brown')
        self.assertEqual(product.display_price, '1,800 AED')
        self.assertEqual(product.image_url, 'https://example.com/brown.jpg')
        self.assertEqual(len(product.color_options), 2)

    async def test_stale_stock_or_unrelated_variant_is_rejected(self):
        for candidate in [None, hit('TWO','Brown',stock=0), hit('TWO','Brown',family='UNRELATED')]:
            step = 0
            def handler(_):
                nonlocal step
                step += 1
                hits = [hit('ONE','Beige')] if step == 1 else ([candidate] if candidate else [])
                return httpx.Response(200,json={'responses':[{'hits':{'hits':hits}}]})
            self.transport(handler)
            with self.assertRaises(VariantUnavailable):
                await self.searcher.select_variant(VariantRequest(product_id='ONE',variant_id='TWO'))
            await self.searcher.client.aclose()

    async def test_option_failure_keeps_the_main_product(self):
        self.transport(lambda _: httpx.Response(503))
        product = Product(id='ONE',title='Riana',parent_id='FAMILY',color='Beige',stock=2)
        await self.searcher.attach_color_options([product],ProductSearchRequest())
        self.assertEqual(product.id, 'ONE')
        self.assertEqual([o.color for o in product.color_options], ['Beige'])
        self.assertIn('unavailable', product.color_options_error)

    async def test_regional_option_lookup_uses_regional_stock_and_colour(self):
        def handler(request):
            body = self.bodies(request)[0]
            self.assertIn({'range': {'region_sa_en.stock': {'gt':0}}},body['query']['bool']['filter'])
            self.assertEqual(body['collapse']['field'],'region_sa_en.color.keyword')
            return httpx.Response(200,json={'responses':[{'hits':{'hits':[]}}]})
        self.transport(handler)
        product = Product(id='ONE',title='Riana',parent_id='FAMILY',color='Beige',stock=2)
        await self.searcher.attach_color_options([product],ProductSearchRequest(region='SA'))

    async def test_variant_endpoint_returns_conflict_for_unavailable_selection(self):
        with patch.object(app.state, 'searcher', self.searcher, create=True):
            self.transport(lambda _: httpx.Response(200,json={'responses':[{'hits':{'hits':[]}}]}))
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
                response = await client.post('/api/products/variant',json={'product_id':'ONE','variant_id':'TWO'})
                self.assertEqual(response.status_code,409)
                self.assertEqual((await client.post('/api/products/variant',json={'product_id':'','variant_id':'TWO'})).status_code,422)
