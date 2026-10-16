import unittest

from product_search import ProductSearchRequest, SearchToolRequest, resolve_search_request


class SearchIntentTests(unittest.TestCase):
    def setUp(self):
        self.previous = ProductSearchRequest(query='leather sofa', region='AE', page=3, filters={
            'categories':['sofas'], 'colors':['Slate grey'], 'materials':['Leather'], 'max_price':4000,
            'currency':'AED', 'max_length_cm':250, 'on_sale':True, 'product_ids':['SOFA-SKU'],
        })

    def test_new_category_drops_accidentally_copied_filters(self):
        tool = SearchToolRequest(query='', intent='new_search', explicit_filters=['categories'],
                                 filters={**self.previous.filters.model_dump(),'categories':['tables']},page=3)
        result = resolve_search_request(tool,self.previous)
        self.assertEqual(result.filters.categories,['tables'])
        self.assertEqual(result.filters.colors,[])
        self.assertEqual(result.filters.materials,[])
        self.assertIsNone(result.filters.max_price)
        self.assertIsNone(result.filters.max_length_cm)
        self.assertIsNone(result.filters.on_sale)
        self.assertEqual(result.filters.product_ids,[])
        self.assertEqual(result.region,'AE')
        self.assertEqual(result.page,1)

    def test_unrelated_category_is_reset_even_if_model_calls_it_a_refinement(self):
        tool = SearchToolRequest(intent='refinement',filters={**self.previous.filters.model_dump(),'categories':['tables']})
        result = resolve_search_request(tool,self.previous)
        self.assertIsNone(result.filters.max_price)
        self.assertEqual(result.filters.materials,[])

    def test_explicit_budget_is_kept_without_old_colour_or_material(self):
        tool = SearchToolRequest(intent='new_search',explicit_filters=['categories','max_price','currency'],
                                 filters={**self.previous.filters.model_dump(),'categories':['tables']})
        result = resolve_search_request(tool,self.previous)
        self.assertEqual(result.filters.max_price,4000)
        self.assertEqual(result.filters.currency,'AED')
        self.assertEqual(result.filters.colors,[])
        self.assertEqual(result.filters.materials,[])

    def test_same_category_and_short_answer_refinements_preserve_requirements(self):
        for category in ['sofa','sofas']:
            tool = SearchToolRequest(intent='refinement', filters={**self.previous.filters.model_dump(),
                                                                  'categories':[category],'colors':['Beige']})
            result = resolve_search_request(tool,self.previous)
            self.assertEqual(result.filters.max_price,4000)
            self.assertEqual(result.filters.materials,['Leather'])
            self.assertEqual(result.filters.colors,['Beige'])

    def test_subcategory_refinement_keeps_budget(self):
        previous = ProductSearchRequest(filters={'categories':['tables'],'max_price':4000})
        tool = SearchToolRequest(intent='refinement',filters={'categories':['dining tables'],'max_price':4000})
        self.assertEqual(resolve_search_request(tool,previous).filters.max_price,4000)

    def test_new_search_does_not_reuse_previous_query(self):
        tool = SearchToolRequest(query=self.previous.query,intent='new_search',filters={'categories':['tables']})
        result=resolve_search_request(tool,self.previous)
        self.assertEqual(result.query,'')
