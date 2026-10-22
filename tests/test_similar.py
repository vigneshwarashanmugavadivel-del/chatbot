import json
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from main import app
from product_search import ProductSearcher, ProductSearchRequest, SimilarRequest, VariantUnavailable


def hit(sku='SEED', family='FAMILY', stock=2, **extra):
    return {'_source': {'sku': sku, 'parent_id': family, 'item_group_id': family, 'title': 'Riana',
                        'color': 'Beige', 'category_vue': 'Sofa', 'stock': stock,
                        'materials_and_finish': 'Fabric with wood frame', 'price': '2,000 AED', **extra}}


class SimilarTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.env = patch.dict('os.environ', {'ES_URL':'https://search.example', 'ES_INDEX':'products', 'ES_REGION_FIELD':''})
        self.env.start()
        self.searcher = ProductSearcher()
        await self.searcher.client.aclose()
        self.searcher.attach_color_options = AsyncMock()

    async def asyncTearDown(self):
        await self.searcher.close()
        self.env.stop()

    def transport(self, handler):
        self.searcher.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    async def test_similar_query_keeps_requirements_and_excludes_original_family(self):
        step = 0
        def handler(request):
            nonlocal step
            step += 1
            body = json.loads(request.content.decode().strip().split('\n')[1])
            query = body['query']['bool']
            self.assertIn({'range': {'stock': {'gt':0}}}, query['filter'])
            if step == 1:
                self.assertIn({'term': {'sku.keyword':'seed'}}, query['filter'])
                return httpx.Response(200, json={'responses':[{'hits':{'hits':[hit()]}}]})
            self.assertIn({'term': {'category_vue.keyword':'sofa'}}, query['filter'])
            self.assertIn({'range': {'chat_price':{'lte':5000}}}, query['filter'])
            self.assertIn({'range': {'chat_length_cm':{'lte':250}}}, query['filter'])
            self.assertIn('materials_and_finish.text', json.dumps(query['filter']))
            self.assertIn('color.text', json.dumps(query['filter']))
            self.assertNotIn('sku.keyword', json.dumps(query['filter']))
            self.assertNotIn('item_group_id.keyword', json.dumps(query['filter']))
            self.assertIn({'term': {'parent_id.keyword':'family'}},query['must_not'])
            self.assertIn({'term': {'item_group_id.keyword':'family'}},query['must_not'])
            self.assertIn({'term': {'sku.keyword':'seed'}},query['must_not'])
            self.assertIn('more_like_this', query['should'][0])
            self.assertEqual(body['sort'][0], {'_score':{'order':'desc'}})
            self.assertEqual(body['size'],8)
            return httpx.Response(200,json={'responses':[{'hits':{'total':1,'hits':[hit('ALTERNATIVE','OTHER')]},
                                                          'aggregations':{'unique_recs_count':{'value':1}}}]})
        self.transport(handler)
        state = ProductSearchRequest(query='Riana', filters={'colors':['beige'],'materials':['fabric'],
            'max_price':5000,'max_length_cm':250,'product_ids':['SEED'],'group_ids':['FAMILY']},sort='price_asc')
        result = await self.searcher.similar_products(SimilarRequest(product_id='SEED',search_state=state))
        self.assertEqual(result.products[0].id, 'ALTERNATIVE')
        self.assertEqual(result.similar_to.id, 'SEED')
        self.assertEqual(result.request.filters.max_price,5000)
        self.assertEqual(result.request.filters.product_ids,[])
        self.assertEqual(state.filters.product_ids,['SEED'])

    async def test_missing_source_or_category_does_not_produce_unrelated_recommendations(self):
        for source in [[],[hit(stock=0)],[hit(category_vue='')]]:
            self.transport(lambda _: httpx.Response(200,json={'responses':[{'hits':{'hits':source}}]}))
            with self.assertRaises(VariantUnavailable):
                await self.searcher.similar_products(SimilarRequest(product_id='SEED'))
            await self.searcher.client.aclose()

    async def test_empty_alternatives_are_a_valid_result(self):
        step = 0
        def handler(_):
            nonlocal step
            step += 1
            return httpx.Response(200,json={'responses':[{'hits':{'total':0,'hits':[hit()] if step == 1 else []}}]})
        self.transport(handler)
        result = await self.searcher.similar_products(SimilarRequest(product_id='SEED'))
        self.assertEqual(result.products,[])
        self.assertEqual(result.total,0)
        self.assertEqual(result.similar_to.title,'Riana')

    async def test_endpoint_rejects_invalid_request_and_returns_catalog_results(self):
        step = 0
        def handler(_):
            nonlocal step
            step += 1
            return httpx.Response(200,json={'responses':[{'hits':{'total':1,'hits':[hit() if step == 1 else hit('OTHER','DIFFERENT')]}}]})
        self.transport(handler)
        questions = ['How do these compare?', 'What goes with these?', 'Can I see other colours?', 'Which is easiest to clean?']
        with patch.object(app.state,'searcher',self.searcher,create=True):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
                response=await client.post('/api/products/similar',json={'product_id':'SEED'})
                self.assertEqual(response.status_code,200)
                self.assertEqual(response.json()['products'][0]['id'],'OTHER')
                self.assertEqual(response.json()['follow_ups'], [])
                for payload in [{'product_id':''},{'product_id':'SEED','limit':100},{'product_id':'SEED','category':'fake'}]:
                    self.assertEqual((await client.post('/api/products/similar',json=payload)).status_code,422)
