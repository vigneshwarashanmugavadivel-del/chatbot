"""Validated model actions and a per-turn registry of retrieved catalog evidence."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from product_search import (
    Product, ProductSearchRequest, SearchResult, SimilarRequest, SEARCH_TOOL, PRODUCT_DETAILS_TOOL,
)
from search_filters import strict_schema

ComparisonField = Literal['description', 'category', 'material', 'color', 'dimension', 'size', 'brand', 'display_price']


class ProductReference(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    retrieval_id: str = Field(min_length=1, max_length=100)
    product_id: str = Field(min_length=1, max_length=200)


class DisplayRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    presentation: Literal['recommendations', 'comparison', 'text']
    products: list[ProductReference] = Field(default_factory=list, max_length=8)
    title: str | None = Field(default=None, max_length=160)
    fields: list[ComparisonField] = Field(
        default_factory=list, max_length=8,
        description='Columns for comparison presentation. Use [] for recommendations or text; ignored in those modes.')

    @model_validator(mode='after')
    def valid_presentation(self):
        # Card/text renderers do not use comparison columns. Discard harmless
        # extra columns instead of making the model retry a valid display.
        if self.presentation != 'comparison':
            self.fields = []
        if self.presentation == 'text' and self.products:
            raise ValueError('Text presentation cannot include products')
        if self.presentation != 'text' and not self.products:
            raise ValueError('A product presentation needs retrieved products')
        if self.presentation == 'comparison' and (len(self.products) < 2 or not self.fields):
            raise ValueError('A comparison needs at least two products and selected comparison fields')
        return self


class PreferenceUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    search_state: ProductSearchRequest | None = Field(description='Complete customer preferences for subsequent turns; null clears them. Investigation queries are independent.')


class PresentationItem(BaseModel):
    product: Product
    request: ProductSearchRequest


class ProductPresentation(BaseModel):
    kind: Literal['recommendations', 'comparison', 'text']
    title: str | None = None
    items: list[PresentationItem] = Field(default_factory=list)
    fields: list[ComparisonField] = Field(default_factory=list)


def action_tool(name, description, schema):
    return {'type': 'function', 'name': name, 'description': description,
            'strict': True, 'parameters': strict_schema(schema.model_json_schema())}


DISPLAY_TOOL = action_tool('display_products',
    'Choose what the customer sees this turn: ordered recommendation cards, a comparison, or text only. '
    'Select exact retrieval_id/product_id pairs returned by retrieval tools during this turn. '
    'The backend renders original catalog data; unknown products are rejected. Comparison fields use original values, with missing facts labelled as not listed. '
    'Each successful call replaces the current turn presentation without changing preferences. It returns confirmation so you can align the final answer. '
    'Retrieval alone does not display anything; select products explicitly when you want cards or a comparison.', DisplayRequest)
PREFERENCES_TOOL = action_tool('update_preferences',
    'Explicitly save the complete customer search preferences for subsequent messages, or clear them with null. '
    'Retrieval and display do not change these preferences. Temporary investigation queries need not be saved.', PreferenceUpdate)
SIMILAR_TOOL = action_tool('find_similar_products',
    'Retrieve catalog alternatives to an exact product identifier, using the supplied complete search state and limit. '
    'Returns a retrieval_id and product records without displaying them or changing preferences. '
    'The source product and its family are excluded; similarity uses catalog text and preserves supplied non-identity constraints.', SimilarRequest)
AGENT_TOOLS = [SEARCH_TOOL, PRODUCT_DETAILS_TOOL, SIMILAR_TOOL, DISPLAY_TOOL, PREFERENCES_TOOL]


class AgentSession:
    def __init__(self, preferences=None):
        self.preferences = preferences
        self.presentation = None
        self.retrievals = {}

    def remember(self, result: SearchResult, request: ProductSearchRequest):
        identifier = f'retrieval_{len(self.retrievals) + 1}'
        snapshot = result.model_copy(deep=True, update={'request': (result.request or request).model_copy(deep=True)})
        self.retrievals[identifier] = snapshot
        return {'retrieval_id': identifier, **snapshot.model_dump()}

    async def execute(self, name, arguments, searcher):
        if name in ('search_products', 'get_product_details'):
            params = ProductSearchRequest.model_validate_json(arguments)
            result = await searcher.search(params)
            return self.remember(result, params), []
        if name == 'find_similar_products':
            params = SimilarRequest.model_validate_json(arguments)
            result = await searcher.similar_products(params)
            return self.remember(result, params.search_state), []
        if name == 'update_preferences':
            update = PreferenceUpdate.model_validate_json(arguments)
            self.preferences = update.search_state
            state = self.preferences.model_dump() if self.preferences else None
            return {'search_state': state}, [{'type': 'preferences', 'search_state': state}]
        if name == 'display_products':
            display = DisplayRequest.model_validate_json(arguments)
            items = []
            seen = set()
            for reference in display.products:
                retrieval = self.retrievals.get(reference.retrieval_id)
                product = next((product for product in retrieval.products if product.id == reference.product_id), None) if retrieval else None
                if product is None:
                    raise ValueError('Unknown product reference: retrieve this product before displaying it')
                if product.stock is None or product.stock <= 0:
                    raise ValueError('This retrieved product is not eligible for display')
                identity = (product.id, retrieval.region)
                if identity in seen:
                    raise ValueError('Duplicate product in presentation')
                seen.add(identity)
                items.append(PresentationItem(product=product.model_copy(deep=True), request=retrieval.request.model_copy(deep=True)))
            self.presentation = ProductPresentation(kind=display.presentation, title=display.title, items=items,
                                                    fields=list(dict.fromkeys(display.fields)))
            confirmation = {'presentation': self.presentation.kind, 'product_ids': [item.product.id for item in items],
                            'fields': self.presentation.fields, 'title': self.presentation.title}
            return confirmation, [{'type': 'presentation', 'presentation': self.presentation.model_dump()}]
        raise ValueError('Unknown action')
