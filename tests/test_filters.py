import json
import unittest
from dataclasses import replace
from decimal import Decimal
from unittest.mock import patch

import httpx
from pydantic import ValidationError

from product_search import ProductSearcher, ProductSearchRequest, SEARCH_TOOL, parse_price


class FilterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.env = patch.dict('os.environ', {'ES_URL': 'https://search.example', 'ES_INDEX': 'products', 'ES_REGION_FIELD': ''})
        self.env.start()
        self.searcher = ProductSearcher()

    async def asyncTearDown(self):
        await self.searcher.close()
        self.env.stop()

    async def test_stock_filter_is_mandatory_even_for_browse_and_price_sort(self):
        for params in [ProductSearchRequest(), ProductSearchRequest(query='sofa'),
                       ProductSearchRequest(sort='price_asc', filters={'max_price': 5000})]:
            body, *_ = self.searcher.search_body(params)
            self.assertIn({'range': {'stock': {'gt': 0}}}, body['query']['bool']['filter'])
            self.assertNotIn('post_filter', body)
            self.assertIn('unique_recs_count', body['aggs'])

    async def test_all_constraints_are_applied_before_grouping(self):
        filters = {
            'categories': ['sofa'], 'colors': ['beige', 'brown'], 'materials': ['leather'],
            'sizes': ['large'], 'brands': ['Al Huzaifa Furniture'], 'product_types': ['simple'],
            'product_ids': ['SKU-1'], 'group_ids': ['GROUP-1'], 'min_price': 2000,
            'max_price': 5000, 'currency': 'AED', 'on_sale': True,
            'min_length_cm': 150, 'max_length_cm': 250, 'min_width_cm': 50,
            'max_width_cm': 120, 'min_height_cm': 40, 'max_height_cm': 100,
        }
        body, _, _, applied, _ = self.searcher.search_body(ProductSearchRequest(filters=filters, sort='price_asc'))
        constraints = body['query']['bool']['filter']
        self.assertIn({'range': {'chat_price': {'gte': 2000, 'lte': 5000}}}, constraints)
        self.assertIn({'range': {'chat_length_cm': {'gte': 150, 'lte': 250}}}, constraints)
        self.assertIn({'range': {'chat_width_cm': {'gte': 50, 'lte': 120}}}, constraints)
        self.assertIn({'range': {'chat_height_cm': {'gte': 40, 'lte': 100}}}, constraints)
        self.assertIn({'term': {'chat_currency': 'AED'}}, constraints)
        self.assertIn({'term': {'chat_on_sale': True}}, constraints)
        self.assertIn({'terms': {'sku.keyword': ['sku-1']}}, constraints)
        self.assertIn({'terms': {'item_group_id.keyword': ['group-1']}}, constraints)
        self.assertIn({'terms': {'type.keyword': ['simple']}}, constraints)
        self.assertEqual(body['sort'][0]['chat_price']['order'], 'asc')
        color_clause = [c for c in constraints if 'bool' in c and any('color.text' in x.get('match_phrase', {}) for x in c['bool']['should'])][0]
        self.assertEqual(color_clause['bool']['minimum_should_match'], 1)
        self.assertEqual(len(color_clause['bool']['should']), 2)
        self.assertTrue(applied['in_stock_only'])

    async def test_region_uses_its_own_stock_prices_and_currency(self):
        body, _, fields, applied, currency = self.searcher.search_body(ProductSearchRequest(region='SA', filters={'max_price': 2000}))
        self.assertEqual(currency, 'SAR')
        self.assertEqual(applied['currency'], 'SAR')
        self.assertEqual(fields['stock'], 'region_sa_en.stock')
        self.assertIn({'range': {'region_sa_en.stock': {'gt': 0}}}, body['query']['bool']['filter'])
        self.assertEqual(body['runtime_mappings']['chat_price']['script']['params']['region'], 'region_sa_en')

    async def test_plural_category_wording_still_matches_singular_catalog_values(self):
        body, *_ = self.searcher.search_body(ProductSearchRequest(filters={'categories': ['sofas']}))
        clauses = body['query']['bool']['filter']
        category = [c for c in clauses if 'bool' in c][0]
        phrases = [item['match_phrase']['category_vue.text'] for item in category['bool']['should']]
        self.assertEqual(phrases, ['sofas', 'sofa'])

    async def test_keyword_queries_search_title_and_both_descriptions_without_category_restriction(self):
        body, *_ = self.searcher.search_body(ProductSearchRequest(query='curved sofa'))
        query = body['query']['bool']['must'][0]['multi_match']
        self.assertEqual(query['query'], 'curved sofa')
        for field in ['title.text^3', 'short_description.text^4', 'description.text^2']:
            self.assertIn(field, query['fields'])
        self.assertNotIn('category_vue', json.dumps(body['query']['bool']['filter']))

    async def test_custom_search_fields_are_localized_and_boosts_preserved(self):
        self.searcher.config = replace(self.searcher.config, search_fields=('title.text^3',))
        body, *_ = self.searcher.search_body(ProductSearchRequest(query='sofas', region='AE'))
        query = body['query']['bool']['must'][0]['multi_match']
        self.assertEqual(query['fields'], ['region_ae_en.title.text^3'])
        self.assertNotIn('should', body['query']['bool'])

    async def test_single_generic_text_clause_preserves_query_and_constraints(self):
        for original in ['sofas', 'curved sofas', 'tables in marble', 'shelves', 'unlistedword']:
            body, *_ = self.searcher.search_body(ProductSearchRequest(
                query=original, filters={'colors':['Beige'], 'max_price':4000}))
            clauses = body['query']['bool']['must']
            self.assertEqual(len(clauses), 1)
            fuzzy = clauses[0]['multi_match']
            self.assertEqual(fuzzy['query'], original)
            self.assertEqual(fuzzy['fuzziness'], 'AUTO')
            self.assertEqual(fuzzy['operator'], 'and')
            for field in ['title.text^3', 'short_description.text^4', 'description.text^2']:
                self.assertIn(field, fuzzy['fields'])
            constraints = json.dumps(body['query']['bool']['filter'])
            self.assertIn('color.text', constraints)
            self.assertIn('4000', constraints)
            self.assertNotIn('simple_query_string', json.dumps(body))
            self.assertNotIn('should', body['query']['bool'])

    async def test_identifiers_are_excluded_even_from_custom_text_fields(self):
        self.searcher.config = replace(self.searcher.config, search_fields=(
            'title.text', 'sku.text^10', 'item_group_id.keyword'))
        body, *_ = self.searcher.search_body(ProductSearchRequest(query='sofa'))
        self.assertEqual(body['query']['bool']['must'][0]['multi_match']['fields'], ['title.text'])

    async def test_exact_sku_preserves_punctuation_and_does_not_require_text(self):
        body, *_ = self.searcher.search_body(ProductSearchRequest(
            region='AE', filters={'product_ids':[' SSZW23034-4 SEATER-G1 ']}))
        self.assertEqual(body['query']['bool']['must'], [{'match_all': {}}])
        self.assertIn({'terms': {'sku.keyword':['sszw23034-4 seater-g1']}}, body['query']['bool']['filter'])
        self.assertIn({'range': {'region_ae_en.stock': {'gt': 0}}}, body['query']['bool']['filter'])

    async def test_sku_filter_has_no_fuzzy_alternative(self):
        body, *_ = self.searcher.search_body(ProductSearchRequest(filters={'product_ids':['SKU-123']}))
        self.assertIn({'terms': {'sku.keyword':['sku-123']}}, body['query']['bool']['filter'])
        self.assertNotIn('fuzziness', json.dumps(body))

    async def test_keyword_search_can_be_combined_with_an_explicit_category(self):
        body, *_ = self.searcher.search_body(ProductSearchRequest(query='marble', filters={'categories':['dining table']}))
        self.assertEqual(body['query']['bool']['must'][0]['multi_match']['query'], 'marble')
        self.assertIn('category_vue.text', json.dumps(body['query']['bool']['filter']))

    async def test_region_keyword_search_uses_the_regions_title_and_descriptions(self):
        body, *_ = self.searcher.search_body(ProductSearchRequest(query='curved sofa', region='AE'))
        fields = body['query']['bool']['must'][0]['multi_match']['fields']
        for field in ['region_ae_en.title.text^3','region_ae_en.short_description.text^4','region_ae_en.description.text^2']:
            self.assertIn(field, fields)
        self.assertNotIn('title.text', fields)

    async def test_out_of_stock_unknown_and_negative_stock_are_never_returned(self):
        await self.searcher.client.aclose()
        def handler(_):
            return httpx.Response(200, json={'responses': [{'hits': {'total': 5, 'hits': [
                {'_source': {'sku': str(i), 'stock': stock, 'title': 'Sofa', 'price': '500 AED'}}
                for i, stock in enumerate([0, None, -1, 'unknown', 2])
            ]}}]})
        self.searcher.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        result = await self.searcher.search(ProductSearchRequest(query='sofa'))
        self.assertEqual([p.id for p in result.products], ['4'])

    async def test_effective_price_rejects_invalid_or_higher_sales(self):
        await self.searcher.client.aclose()
        sales = ['', '0 AED', 'bad', '2,000 AED', '500 USD', '800 AED']
        def handler(_):
            return httpx.Response(200, json={'responses': [{'hits': {'total': 6, 'hits': [
                {'_source': {'sku': str(i), 'stock': 2, 'title': 'Sofa', 'price': '1,000 AED', 'sale_price': sale}}
                for i, sale in enumerate(sales)
            ]}}]})
        self.searcher.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        result = await self.searcher.search(ProductSearchRequest(query='sofa'))
        self.assertEqual([p.effective_price for p in result.products], [1000, 1000, 1000, 1000, 1000, 800])
        self.assertEqual([p.on_sale for p in result.products], [False]*5 + [True])

    async def test_bad_filters_cannot_bypass_stock_or_inject_queries(self):
        for filters in [{'in_stock_only': False}, {'max_price': -1}, {'min_price': 5000, 'max_price': 1000},
                        {'max_width_cm': 0}, {'min_width_cm': 200, 'max_width_cm': 100},
                        {'currency': 'USD OR 1=1'}, {'colors': [' ']}, {'min_price': float('inf')}]:
            with self.subTest(filters=filters), self.assertRaises(ValidationError):
                ProductSearchRequest(filters=filters)
        with self.assertRaises(ValidationError):
            ProductSearchRequest(region='unknown')

    async def test_strict_tool_schema_has_required_properties_recursively(self):
        def check(schema):
            if isinstance(schema, dict):
                if schema.get('type') == 'object':
                    self.assertFalse(schema['additionalProperties'])
                    self.assertEqual(set(schema['required']), set(schema['properties']))
                self.assertNotIn('default', schema)
                for value in schema.values():
                    check(value)
            elif isinstance(schema, list):
                for value in schema:
                    check(value)
        check(SEARCH_TOOL['parameters'])


class PriceParsingTests(unittest.TestCase):
    def test_currency_and_thousands_are_preserved(self):
        for raw, expected in [('1,214 AED', ('1214', 'AED')), ('AED 1,214.50', ('1214.50', 'AED')),
                              ('99.95 SAR', ('99.95', 'SAR')), ('250', ('250', 'AED'))]:
            amount, currency = parse_price(raw)
            self.assertEqual((amount, currency), (Decimal(expected[0]), expected[1]))

    def test_missing_free_malformed_and_ambiguous_prices_are_excluded(self):
        for raw in [None, '', 0, '0 AED', 'unknown', '12,34 AED', '1.234,50 AED', '-100 AED', 'NaN',
                    '500 to 900 AED', ['100 AED'], True, 'AED 100 SAR', '100..50 AED']:
            with self.subTest(raw=raw):
                self.assertIsNone(parse_price(raw))
