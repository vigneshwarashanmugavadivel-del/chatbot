"""Read-only async adapter around the supplied website text-search query builder."""

import json
import logging
import os
import re
from decimal import Decimal
from typing import Literal
from html.parser import HTMLParser
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, ConfigDict, Field

from search_filters import Region, SearchFilters, build_filter_clauses, category_phrases, runtime_fields, strict_schema

from example.text_search import SearchConfig, assemble_response

logger = logging.getLogger(__name__)


class SearchUnavailable(Exception):
    """A sanitized, customer-readable search error."""


class ProductSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    query: str = Field(default="", max_length=300, description="Product names and descriptive keywords searched across title, short description, description and other catalog text fields. Do not put these terms only in categories. Empty string is for filter-only browsing.")
    filters: SearchFilters = Field(default_factory=SearchFilters)
    region: Region | None = Field(default=None, description="Country storefront; null uses configured default. AE, SA, QA, OM or BH.")
    sort: Literal["relevance", "price_asc", "price_desc", "title_asc"] = "relevance"
    page: int = Field(default=1, ge=1, le=20)
    limit: int = Field(default=8, ge=1, le=8, description='Return 8 recommendations by default; fewer only when the customer requests a smaller number.')


class SearchToolRequest(ProductSearchRequest):
    intent: Literal['new_search', 'refinement'] | None = Field(default=None,
        description='new_search for a different product category or a fresh request; refinement for changes to the current search.')
    explicit_filters: list[Literal[
        'categories', 'colors', 'materials', 'sizes', 'product_types', 'brands', 'product_ids', 'group_ids',
        'min_price', 'max_price', 'currency', 'on_sale', 'min_length_cm', 'max_length_cm',
        'min_width_cm', 'max_width_cm', 'min_height_cm', 'max_height_cm',
    ]] = Field(default_factory=list, max_length=18,
        description='For a new search: filter names explicitly requested in the latest user message, including preferences explicitly requested to carry over. Do not list filters merely remembered from the old category. Include currency when carrying over a budget in a specific currency.')


class ColorOption(BaseModel):
    id: str
    color: str


class VariantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    product_id: str = Field(min_length=1, max_length=200)
    variant_id: str = Field(min_length=1, max_length=200)
    search_state: ProductSearchRequest = Field(default_factory=ProductSearchRequest)


class SimilarRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    product_id: str = Field(min_length=1, max_length=200)
    search_state: ProductSearchRequest = Field(default_factory=ProductSearchRequest)
    limit: int = Field(default=8, ge=1, le=8)


class SimilarReference(BaseModel):
    id: str
    title: str


class VariantUnavailable(Exception):
    """The chosen variant no longer meets the selection requirements."""


class Product(BaseModel):
    id: str
    title: str
    group_id: str | None = None
    parent_id: str | None = None
    color_options: list[ColorOption] = Field(default_factory=list)
    color_options_error: str | None = None
    description: str | None = None
    category: str | None = None
    product_type: str | None = None
    material: str | None = None
    color: str | None = None
    size: str | None = None
    brand: str | None = None
    dimension: str | None = None
    display_price: str | None = None
    effective_price: float | None = None
    currency: str | None = None
    on_sale: bool = False
    price: str | None = None
    sale_price: str | None = None
    stock: int | None = None
    url: str | None = None
    image_url: str | None = None


class SearchResult(BaseModel):
    query: str
    products: list[Product]
    total: int
    page: int
    pages: int
    limit: int = 8
    similar_to: SimilarReference | None = None
    corrected_query: str | None = None
    applied_filters: dict = Field(default_factory=dict)
    sort: str = "relevance"
    region: str | None = None
    request: ProductSearchRequest | None = None


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def text(value, limit=1200):
    if value is None:
        return None
    if isinstance(value, list):
        value = ", ".join(str(item) for item in value)
    parser = PlainText()
    parser.feed(str(value))
    return " ".join(" ".join(parser.parts).split())[:limit] or None


def safe_url(value):
    if isinstance(value, list):
        value = value[0] if value else None
    if not isinstance(value, str):
        return None
    parsed = urlparse(value)
    return value if parsed.scheme in {"https", "http"} and parsed.netloc and not parsed.username else None


class ProductSearcher:
    def __init__(self):
        self.index = os.getenv("ES_INDEX", "").strip()
        self.url = os.getenv("ES_URL", "").rstrip("/")
        self.fields = {
            "id": os.getenv("ES_PRODUCT_ID_FIELD", "sku"),
            "parent_id": os.getenv("ES_SEARCH_COLLAPSE_FIELD", "parent_id") or "parent_id",
            "group_id": os.getenv("ES_GROUP_FIELD", "item_group_id"),
            "title": os.getenv("ES_TITLE_FIELD", "title"),
            "description": os.getenv("ES_SHORT_DESCRIPTION_FIELD", "short_description"),
            "full_description": os.getenv("ES_DESCRIPTION_FIELD", "description"),
            "category": os.getenv("ES_CATEGORY_FIELD", "category_vue"),
            "product_type": os.getenv("ES_PRODUCT_TYPE_FIELD", "type"),
            "material": os.getenv("ES_MATERIAL_FIELD", "materials_and_finish"),
            "color": os.getenv("ES_COLOUR_FIELD", "color"),
            "brand": os.getenv("ES_BRAND_FIELD", "brand"),
            "dimension": os.getenv("ES_DIMENSION_FIELD", "dimension"),
            "size": os.getenv("ES_SIZE_FIELD", "size"),
            "price": os.getenv("ES_PRICE_FIELD", "price"),
            "sale_price": os.getenv("ES_SALE_PRICE_FIELD", "sale_price"),
            "stock": os.getenv("ES_AVAILABILITY_FIELD", "stock"),
            "url": os.getenv("ES_URL_FIELD", "link"),
            "image_url": os.getenv("ES_IMAGE_FIELD", "image_link"),
        }
        default_search_fields = ",".join(
            f"{self.fields[key]}.text^{weight}" for key, weight in (
                ("description", 4), ("title", 3), ("full_description", 2),
                ("category", 1), ("material", 1), ("color", 1),
            )
        )
        self.config = SearchConfig(
            index=self.index,
            search_fields=tuple(field.strip() for field in os.getenv("ES_SEARCH_FIELDS", default_search_fields).split(",") if field.strip()),
            source_fields=tuple(dict.fromkeys(field for field in self.fields.values() if field)),
            collapse_field=os.getenv("ES_SEARCH_COLLAPSE_FIELD", "parent_id") or None,
            autocorrect=False,
            timeout=float(os.getenv("ES_TIMEOUT_SECONDS", "20")),
        )
        headers = {}
        auth = None
        if os.getenv("ES_API_KEY"):
            headers["Authorization"] = "ApiKey " + os.environ["ES_API_KEY"]
        elif os.getenv("ES_USERNAME") and os.getenv("ES_PASSWORD"):
            auth = (os.environ["ES_USERNAME"], os.environ["ES_PASSWORD"])
        self.client = httpx.AsyncClient(
            headers=headers, auth=auth, timeout=self.config.timeout,
            verify=os.getenv("ES_VERIFY_TLS", "true").lower() not in {"false", "0", "no"},
        )

    @property
    def configured(self):
        return bool(self.url and self.index)

    async def close(self):
        await self.client.aclose()

    def search_body(self, params: ProductSearchRequest):
        selected_region = f"region_{params.region.lower()}_en" if params.region else os.getenv("ES_REGION_FIELD", "")
        if selected_region and selected_region not in {f"region_{r}_{lang}" for r in ['ae','sa','qa','om','bh'] for lang in ['en','ar']}:
            raise SearchUnavailable("The configured regional storefront is unsupported.")
        currency = {'AE':'AED','SA':'SAR','QA':'QAR','OM':'OMR','BH':'BHD'}.get(
            params.region or (selected_region.split('_')[1].upper() if selected_region else ''),
            os.getenv('ES_DEFAULT_CURRENCY', 'AED'))
        localized = {'title','description','full_description','material','color','size','dimension','price','sale_price','stock','url'}
        fields = {key: f"{selected_region}.{field}" if selected_region and key in localized else field
                  for key, field in self.fields.items()}
        search_fields = []
        for configured in self.config.search_fields:
            name, separator, boost = configured.partition('^')
            if selected_region:
                for key in localized:
                    original = self.fields[key]
                    if name == original or name.startswith(original + '.'):
                        name = fields[key] + name[len(original):]
                        break
            search_fields.append(name + (separator + boost if separator else ''))
        # Identifiers have their own exact filters and never enter fuzzy text search.
        identifier_fields = {fields[key] + suffix for key in ('id', 'group_id')
                             for suffix in ('', '.text', '.keyword')}
        search_fields = [field for field in search_fields
                         if field.partition('^')[0] not in identifier_fields]
        if params.query and not search_fields:
            raise SearchUnavailable("No searchable text fields are configured.")
        request = {"search_query": params.query, "page_num": params.page, "num_results": params.limit}
        filters = params.filters
        runtime = runtime_fields(self.fields, filters, selected_region, currency, params.sort)
        text_clause = {"multi_match": {
            "query": params.query, "fields": search_fields,
            "type": "best_fields", "operator": "and", "fuzziness": "AUTO",
            "prefix_length": 1, "max_expansions": 50,
        }} if params.query else {"match_all": {}}
        body = {
            "query": {"bool": {
                "must": [text_clause],
                "filter": build_filter_clauses(filters, fields, runtime, currency),
            }},
            "_source": list(dict.fromkeys(fields.values())),
            "from": (params.page - 1) * params.limit,
            "size": params.limit,
            "track_total_hits": True,
            "sort": [{"_score": {"order": "desc"}}, {"mad_id": {"order": "desc"}}],
        }
        if self.config.collapse_field:
            body['collapse'] = {'field': self.config.collapse_field}
            body['aggs'] = {'unique_recs_count': {'cardinality': {'field': self.config.collapse_field}}}
        if runtime:
            body['runtime_mappings'] = runtime
        if params.sort.startswith('price_'):
            body['sort'] = [{'chat_price': {'order': 'asc' if params.sort == 'price_asc' else 'desc', 'missing': '_last'}}, {'mad_id': 'desc'}]
        elif params.sort == 'title_asc':
            body['sort'] = [{fields['title'] + '.keyword': 'asc'}, {'mad_id': 'desc'}]
        body['timeout'] = f"{max(1, int(self.config.timeout))}s"
        applied = filters.model_dump(exclude_none=True)
        applied = {key: value for key, value in applied.items() if value != []}
        applied['in_stock_only'] = True
        if 'chat_currency' in runtime:
            applied['currency'] = filters.currency or currency
        return body, request, fields, applied, currency

    async def search(self, params: ProductSearchRequest) -> SearchResult:
        if not self.configured:
            raise SearchUnavailable("Product search is not configured on the server.")
        body, request, fields, applied, currency = self.search_body(params)
        return await self.run_product_search(body, request, fields, applied, currency, params)

    async def run_product_search(self, body, request, fields, applied, currency, params):
        content = json.dumps({"index": self.index}) + "\n" + json.dumps(body) + "\n"
        try:
            response = await self.client.post(
                f"{self.url}/_msearch", content=content,
                headers={"Content-Type": "application/x-ndjson"},
            )
            response.raise_for_status()
            results = response.json().get("responses", [])
            if not results or results[0].get("error"):
                raise ValueError("Search returned an error")
            result = results[0]
            if result.get("timed_out") or result.get("_shards", {}).get("failed", 0):
                raise ValueError("Search returned partial results")
            assembled = assemble_response(result, request, collapse_field=self.config.collapse_field)
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            logger.warning("Product search failed (%s)", type(exc).__name__)
            raise SearchUnavailable("Product search is temporarily unavailable. Please try again.") from None
        products = [product for source in assembled["Products"]
                    if (product := self.normalize_product(source, fields, currency)) is not None]
        await self.attach_color_options(products, params)
        return SearchResult(query=params.query, products=products, total=assembled["total_count"],
                            page=params.page, pages=assembled["num_of_pages"], limit=params.limit,
                            applied_filters=applied, sort=params.sort, region=params.region, request=params)

    async def similar_products(self, selection: SimilarRequest) -> SearchResult:
        if not self.configured:
            raise SearchUnavailable("Product search is not configured on the server.")
        # Read the clicked SKU from the catalog; never trust client-supplied attributes.
        lookup_params = ProductSearchRequest(region=selection.search_state.region)
        lookup, _, fields, _, currency = self.search_body(lookup_params)
        lookup.pop('collapse', None)
        lookup.pop('aggs', None)
        lookup.update({'size': 1, 'sort': [{'mad_id': 'desc'}]})
        lookup['query']['bool']['filter'].append({'term': {fields['id'] + '.keyword': selection.product_id.lower()}})
        try:
            response = await self.fetch_variant_queries([lookup])
        except SearchUnavailable:
            raise SearchUnavailable("Similar products are temporarily unavailable. Please try again.") from None
        hits = response[0].get('hits', {}).get('hits', [])
        seed = self.normalize_product(hits[0].get('_source', {}), fields, currency) if hits else None
        if seed is None or not seed.category:
            raise VariantUnavailable("This product cannot be used to find alternatives right now. Please choose another product.")
        filters = selection.search_state.filters.model_copy(update={
            'product_ids': [], 'group_ids': [],
            'categories': selection.search_state.filters.categories or [seed.category],
        })
        params = selection.search_state.model_copy(update={'query': '', 'filters': filters, 'sort': 'relevance',
                                                          'page': 1, 'limit': selection.limit})
        body, request, fields, applied, currency = self.search_body(params)
        query = body['query']['bool']
        query['filter'].append({'term': {fields['category'] + '.keyword': seed.category.lower()}})
        # Similar means a different product family, not another colour of this SKU.
        exclusions = [{'term': {fields['id'] + '.keyword': seed.id.lower()}}]
        for key in ['parent_id', 'group_id']:
            identifier = getattr(seed, key)
            if identifier:
                exclusions.append({'term': {fields[key] + '.keyword': identifier.lower()}})
        query.setdefault('must_not', []).extend(exclusions)
        seed_text = ' '.join(value for value in [seed.title, seed.description, seed.material] if value)
        query['should'] = [{'more_like_this': {
            'fields': [fields[key] + '.text' for key in ['title', 'description', 'full_description', 'material']],
            'like': seed_text, 'min_term_freq': 1, 'min_doc_freq': 1, 'max_query_terms': 30,
        }}]
        if seed.color:
            query['should'].append({'match_phrase': {fields['color'] + '.text': {'query': seed.color, 'boost': 3}}})
        # Category establishes relevance when optional descriptive data is sparse.
        query['minimum_should_match'] = 0
        result = await self.run_product_search(body, request, fields, applied, currency, params)
        result.similar_to = SimilarReference(id=seed.id, title=seed.title)
        return result

    def normalize_product(self, source, fields, currency):
        values = {name: get_path(source, field) for name, field in fields.items()}
        stock = values["stock"]
        try:
            stock = int(stock) if stock is not None else None
        except (ValueError, TypeError):
            stock = None
        # Defense in depth: never send nonpositive/unknown stock to the LLM or UI.
        if stock is None or stock <= 0:
            return None
        regular = parse_price(values['price'], currency)
        sale = parse_price(values['sale_price'], currency)
        discounted = bool(regular and sale and regular[1] == sale[1] and sale[0] < regular[0])
        use_sale = bool(sale and (not regular or discounted))
        effective = sale if use_sale else regular
        display_price = text(values['sale_price'] if use_sale else values['price']) if effective else None
        return Product(
            id=text(values["id"]) or "", title=text(values["title"]) or "Untitled product",
            group_id=text(values["group_id"]), parent_id=text(values["parent_id"]),
            description=text(values["description"] or values["full_description"]),
            **{key: text(values[key]) for key in ("category", "product_type", "material", "color", "size", "price", "sale_price")},
            brand=text(values['brand']), dimension=text(values['dimension']),
            display_price=display_price, effective_price=float(effective[0]) if effective else None,
            currency=effective[1] if effective else None, on_sale=discounted,
            stock=stock, url=safe_url(values["url"]), image_url=safe_url(values["image_url"]),
        )

    @staticmethod
    def variant_params(params):
        # Changing colour explicitly changes SKU; every other structured constraint stays.
        filters = params.filters.model_copy(update={"colors": [], "product_ids": []})
        return params.model_copy(update={"query": "", "filters": filters, "page": 1})

    def family(self, product):
        if product.parent_id:
            return "parent_id", product.parent_id
        if product.group_id:
            return "group_id", product.group_id
        return "id", product.id

    async def fetch_variant_queries(self, bodies):
        content = "".join(json.dumps({"index": self.index}) + "\n" + json.dumps(body) + "\n" for body in bodies)
        try:
            response = await self.client.post(f"{self.url}/_msearch", content=content,
                                              headers={"Content-Type": "application/x-ndjson"})
            response.raise_for_status()
            results = response.json().get("responses", [])
            if len(results) != len(bodies):
                raise ValueError("Incomplete variant response")
            for result in results:
                if result.get("error") or result.get("timed_out") or result.get("_shards", {}).get("failed", 0):
                    raise ValueError("Variant search failed")
            return results
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            logger.warning("Variant search failed (%s)", type(exc).__name__)
            raise SearchUnavailable("Colour options are temporarily unavailable. Please try again.") from None

    async def attach_color_options(self, products, params):
        pending = []
        bodies = []
        variant_params = self.variant_params(params)
        for product in products:
            # At minimum the current in-stock colour is known, even if family lookup fails.
            product.color_options = [ColorOption(id=product.id, color=product.color)] if product.id and product.color else []
            family_key, family_id = self.family(product)
            if family_key == "id" or not family_id:
                continue  # Never join records by an empty group or by title.
            body, _, fields, _, currency = self.search_body(variant_params)
            body['query']['bool']['filter'].extend([
                {'term': {fields[family_key] + '.keyword': family_id.lower()}},
                {'exists': {'field': fields['color'] + '.keyword'}},
            ])
            body['query']['bool']['must_not'] = [{'term': {fields['color'] + '.keyword': ''}}]
            body.pop('aggs', None)
            body['collapse'] = {'field': fields['color'] + '.keyword'}
            body['sort'] = [{fields['color'] + '.keyword': 'asc'}, {fields['id'] + '.keyword': 'asc'}]
            # Full variant data lets parent records with no colour display a real SKU,
            # rather than pairing a generic parent image with an arbitrary colour label.
            body['_source'] = list(dict.fromkeys(fields.values()))
            body['from'] = 0
            body['size'] = 10000
            bodies.append(body)
            pending.append((product, fields, family_key, family_id, currency))
        if not bodies:
            return
        try:
            results = await self.fetch_variant_queries(bodies)
        except SearchUnavailable as exc:
            for product, *_ in pending:
                product.color_options_error = str(exc)
            return
        for (product, fields, family_key, family_id, currency), result in zip(pending, results):
            options = {product.color.casefold(): ColorOption(id=product.id, color=product.color)} if product.id and product.color else {}
            candidates = []
            for hit in result.get('hits', {}).get('hits', []):
                source = hit.get('_source', {})
                identifier = text(get_path(source, fields['id']))
                color = text(get_path(source, fields['color']))
                stock = get_path(source, fields['stock'])
                family_value = text(get_path(source, fields[family_key]))
                if not identifier or not color or not isinstance(stock, (int, float)) or stock <= 0:
                    continue
                if not family_value or family_value.casefold() != family_id.casefold():
                    continue
                options.setdefault(color.casefold(), ColorOption(id=identifier, color=color))
                variant = self.normalize_product(source, fields, currency)
                if variant is not None:
                    candidates.append(variant)
            if not product.color and candidates:
                # Prefer the variant pictured by the parent when that exact image is
                # available; otherwise show a real candidate's own image and details.
                selected = next((variant for variant in candidates
                                 if product.image_url and variant.image_url == product.image_url), candidates[0])
                for name in Product.model_fields:
                    setattr(product, name, getattr(selected, name))
                options[selected.color.casefold()] = ColorOption(id=selected.id, color=selected.color)
            product.color_options = sorted(options.values(), key=lambda option: option.color.casefold())

    async def select_variant(self, selection: VariantRequest):
        if not self.configured:
            raise SearchUnavailable("Product search is not configured on the server.")
        params = self.variant_params(selection.search_state)
        body, _, fields, _, currency = self.search_body(params)
        body.pop('aggs', None)
        body.pop('collapse', None)
        body['from'] = 0
        body['size'] = 1
        body['sort'] = [{'mad_id': 'desc'}]
        body['query']['bool']['filter'].append({'term': {fields['id'] + '.keyword': selection.product_id.lower()}})
        results = await self.fetch_variant_queries([body])
        hits = results[0].get('hits', {}).get('hits', [])
        base = self.normalize_product(hits[0].get('_source', {}), fields, currency) if hits else None
        if base is None:
            raise VariantUnavailable("This product is no longer available within your search requirements. Please search again.")
        family_key, family_id = self.family(base)
        target, _, _, _, _ = self.search_body(params)
        target.pop('aggs', None)
        target.pop('collapse', None)
        target.update({'from': 0, 'size': 1, 'sort': [{'mad_id': 'desc'}]})
        target['query']['bool']['filter'].extend([
            {'term': {fields[family_key] + '.keyword': family_id.lower()}},
            {'term': {fields['id'] + '.keyword': selection.variant_id.lower()}},
        ])
        results = await self.fetch_variant_queries([target])
        hits = results[0].get('hits', {}).get('hits', [])
        product = self.normalize_product(hits[0].get('_source', {}), fields, currency) if hits else None
        if product is None or self.family(product) != self.family(base):
            raise VariantUnavailable("That colour is no longer available within your search requirements. Please choose another.")
        await self.attach_color_options([product], params)
        return product


def get_path(source, field):
    value = source
    for part in field.split('.'):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def parse_price(raw, default_currency='AED'):
    if raw is None or isinstance(raw, (bool, list, dict)):
        return None
    match = re.fullmatch(r'(?:(?P<prefix>[A-Z]{3})\s*)?(?P<number>(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]+)?)(?:\s*(?P<suffix>[A-Z]{3}))?', str(raw).strip().upper())
    if not match or (match['prefix'] and match['suffix']):
        return None
    amount = Decimal(match['number'].replace(',', ''))
    if amount <= 0:
        return None
    return amount, match['prefix'] or match['suffix'] or default_currency


def resolve_search_request(tool: SearchToolRequest, previous: ProductSearchRequest | None):
    """Enforce a fresh filter set when moving to a new product category."""
    old = previous.filters.categories if previous else []
    new = tool.filters.categories
    old_phrases = {phrase for category in old for phrase in category_phrases(category)}
    new_phrases = {phrase for category in new for phrase in category_phrases(category)}
    changed = bool(old_phrases and new_phrases and old_phrases.isdisjoint(new_phrases))
    old_words = {word for phrase in old_phrases for word in re.findall(r'\w+', phrase)}
    new_words = {word for phrase in new_phrases for word in re.findall(r'\w+', phrase)}
    unrelated = changed and old_words.isdisjoint(new_words)
    fresh = tool.intent == 'new_search' or unrelated or (tool.intent is None and changed)
    values = tool.model_dump(exclude={'intent', 'explicit_filters'})
    if fresh:
        retained = set(tool.explicit_filters)
        if 'product_ids' not in retained:
            retained.add('categories')
        values['filters'] = tool.filters.model_dump(include=retained)
        values['page'] = 1
        if previous and values['query'] and values['query'] == previous.query and new:
            values['query'] = ''
    # The storefront is session context, not a preference belonging to one category.
    if values['region'] is None and previous:
        values['region'] = previous.region
    return ProductSearchRequest.model_validate(values)


SEARCH_TOOL = {
    'type': 'function', 'name': 'search_products', 'strict': True,
    'description': "Search ONLY in-stock Alhuzaifa products. Use query to search product names, features and descriptive keywords across title, short description and full description as well as other text fields. Do not turn names or features into category filters. For broad discovery leave categories empty; apply categories only for genuine category restrictions. Put structured colour, material, size, brand, type, SKU/group, budget, currency, sale and dimension constraints in filters. Empty query browses by filters. Attribute phrases match only their own fields (beige includes light beige). OR within arrays; AND across filters. Preserve active constraints for refinements. For a new category use intent=new_search and explicit_filters listing only newly stated or explicitly retained preferences; clear inherited filters from the previous category. Never relax filters within the same search to produce results. Budget and sort use positive displayed sale price when lower than regular, otherwise regular. Currency defaults to storefront currency; no conversion. Region selects AE/SA/QA/OM/BH storefront with its own stock and prices. Only unambiguous, labelled cm/mm/m measurements support dimension ranges; unknown measurements are excluded. Do not confuse catalog size labels with dimensions or seating capacity. Never swap length and width. Keep page size stable when paging; reset page to 1 when filters change.",
    'parameters': strict_schema(SearchToolRequest.model_json_schema()),
}
