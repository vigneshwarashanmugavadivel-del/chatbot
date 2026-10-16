# Alhuzaifa chatbot demo

FastAPI serves an HTML/CSS/JavaScript UI and calls OpenAI's Responses API using `gpt-6.1-sol`. The model can search Alhuzaifa's live Elasticsearch catalog through a validated, read-only tool.

## Run locally

Python 3.10+ is required. A virtual environment is created at `.venv`.

```bash
source .venv/bin/activate
pip install -r requirements.txt
```

The local `.env` contains your Elasticsearch configuration. Keep it private. For a fresh checkout, copy `.env.example` to `.env` and fill in `OPENAI_API_KEY`, `ES_URL`, `ES_INDEX`, and either `ES_API_KEY` or `ES_USERNAME`/`ES_PASSWORD`. An API key takes precedence over username/password.

Run with the configured `CHAT_API_HOST` and `CHAT_API_PORT`:

```bash
python main.py
```

Open http://localhost:8000. API documentation is at `/docs`.
Alternatively, `uvicorn main:app --reload --port 8000` runs on localhost; Uvicorn's CLI options take precedence over the host/port settings used by `python main.py`.
Restart after changing `.env`. Your account must have API billing and access to the configured model.

## Current functionality

- Product search, general furniture advice, and conversational refinement.
- After recommendations, the assistant asks one relevant question to add a missing filter, usually budget first, then colour, material or category-appropriate dimensions. Short answers refine the prior search while preserving other requirements. It is instructed to skip preferences already answered or declined and ask before broadening a search with no matches. Show similar also adds a question about a missing budget, colour or material preference.
- Clickable filter suggestions appear below follow-up replies. Budget ceilings are rounded from displayed prices in the same currency; colour choices include returned variants, materials are drawn from returned specifications, and dimension choices preserve the requested axis. Clicking sends a readable chat message with the active search context. Empty results can offer explicit links to remove existing price/colour/material constraints. Suggestions are replaced after the next successful reply and disabled during requests; free-text input remains available.
- Replies stream into the chat as text is generated. Status messages show request interpretation, catalog search, and reply generation; product cards appear as soon as search finishes.
- Catalog-backed product cards show larger images, a description, labelled brand/colour/material/dimension/size specifications, SKU, and formatted prices. Product navigation links and stock-status wording are hidden in the demo UI; availability checks remain enforced internally.
- Cards show selectable in-stock colour options. Selecting a colour refreshes that card's image, price, SKU, and specifications directly from Elasticsearch without a model call.
- Each card has a **Show similar** button. It adds up to eight alternative product cards above a short reply, without calling the language model. Similarity uses Elasticsearch `more_like_this` over product text/materials plus a colour preference, with the source category required. The source SKU and its parent/group family are excluded. Budget, colour, material, size, dimension, sale, currency, brand and region requirements remain; specific SKU/group restrictions and the original text query are replaced to allow alternatives. Results are ranked by similarity rather than the previous price/name sort. If no alternatives meet the filters, the UI reports this without broadening them.
- History stays in browser memory; reload or New chat clears it.
- The latest ten turns are sent as context. OpenAI response storage is disabled using `store=False`; provider retention policies still apply.
- API credentials stay on the backend. Missing configuration and upstream errors produce readable messages.
- The model calls `search_products(query, filters, region, sort, page, limit)` and receives actual catalog results before responding. The server validates parameters, limits searches to eight products per call and two calls per chat request, and returns product cards directly from the search results.
- No cart actions, order tracking, or policy lookup yet.

## Elasticsearch implementation

`product_search.py` reuses `build_query` and `assemble_response` from the supplied `example/text_search.py`. It sends asynchronous HTTP `_msearch` requests using `httpx`, so the legacy Elasticsearch 7 client from the standalone example is not required. The example file is unchanged.

The query preserves the sample's phrase boosts, all-word matching, relevance sorting, `mad_id` tie-breaker, and `parent_id` collapse. Queries treat punctuation as literal text rather than query-string operators. Only selected product fields are returned; internal index metadata and credentials are never sent to the model.

The live mapping uses `keyword` fields with `.text` subfields. Search uses `.text` for title, category, descriptions, material, color, SKU, and item group. Override `ES_SEARCH_FIELDS` with a comma-separated list if needed. `ES_*_FIELD` settings map source data to product cards; `ES_URL_FIELD` and `ES_IMAGE_FIELD` default to `link` and `image_link`.

Every search requires **positive stock** before ranking, pagination, and variant collapse. A second check removes any unavailable or unknown-stock product before returning results to the model or UI. Customers cannot disable this rule. Old chat results must be searched again before recommendation.

Supported filters:

- Category, color, material/finish, size label, and brand: phrases restricted to their respective fields. Category wording supports common singular/plural forms (sofas/sofa); beige includes light beige. These are field-specific phrase matches, not exact whole-field equality.
- Product type (`simple`, `configurable`, `bundle`), SKU, and group ID: case-normalized exact matches.
- Minimum/maximum effective price, currency, and on-sale status. Filters use a positive sale price only when below the regular price in the same currency; an invalid, zero, or higher sale price falls back to the regular price. Missing/invalid prices do not satisfy a budget filter. Zero prices are treated as unknown, not free items.
- Minimum/maximum labelled length, width, and height, expressed in cm. Catalog cm/mm/metre units are normalized. Missing, unlabelled, repeated variant measurements, ranges, or ambiguous measurements are excluded. Seat/arm/back measurements do not stand in for overall measurements.
- Region: `AE`, `SA`, `QA`, `OM`, or `BH` selects its English regional stock, prices, product text and links. No stock or price fallback to another region occurs. Without a region, `ES_REGION_FIELD` controls the default; its existing empty value uses root catalog data. Root currency defaults to `ES_DEFAULT_CURRENCY=AED`. No currency conversion occurs.

Values within one attribute array are alternatives (OR); different filters combine as requirements (AND). Ranges are inclusive. Sorting supports relevance, numeric effective price ascending/descending, and name A–Z. Price sorting stays within the selected currency. Filters also work with an empty query for catalog browsing. Style and seat count have no dedicated structured fields and remain keyword searches; the assistant must verify retrieved details.

Prices and measurements are parsed using request-scoped Elasticsearch runtime fields in `search_filters.py`, **without changing mappings or documents**. This uses the displayed price strings such as `1,214 AED`; the regional `price_vue`/`sale_price_vue` values were observed to differ from the displayed strings. For production scale, ingest normalized prices and measurements into dedicated indexed numeric fields instead of computing them per request.

Applied constraints appear above the product cards. The browser sends the last validated request as `search_state`, allowing follow-ups to retain their budget, attributes, region and sort. The model is instructed to ask before relaxing constraints. This is conversation-based interpretation, not a deterministic preference parser.

Switching product categories starts a fresh search: “show tables” after a sofa search clears the sofa's budget, colour, material and dimensions. Region remains session context. The search tool declares `intent` and `explicit_filters`; the backend removes inherited constraints on new searches and guards unrelated category changes even if mislabelled as refinements. “Show tables with the same budget” explicitly carries only that budget/currency forward. Same-category changes such as “only beige ones”, “cheaper” and narrowing tables to dining tables retain other constraints. The model interprets which preferences the customer explicitly requests; the server validates and applies the resulting reset policy.

Colour options are fetched in one batched `_msearch` for the displayed product families. Family matching uses the configured collapse/parent identifier, then a nonempty item group if no parent is known; products without either identifier keep only their known current colour. Records are never joined by title or an empty group. Each colour has a representative matching SKU; duplicate colour labels are combined. Only in-stock variants satisfying the other structured filters are offered. Discovering other colours drops the original free-text query, colour filter and specific-SKU filter, while retaining budget, material, category, size, dimensions, currency, sale status, group constraints and region.

Parent records without a colour are resolved to a concrete matching variant before display. If a variant has the parent's exact image URL it is preferred; otherwise the card uses a real variant's own image and details. This keeps the displayed SKU, colour, image, price and specifications together and allows the matching colour button to be preselected.

Selecting a colour rechecks the original and selected SKUs, their family relationship, positive stock, and the retained filters. If availability changes, the UI keeps the current card and displays an error. A successful selection replaces just that card, marks the selected colour, and records the displayed SKU for chat follow-ups. Changing colour on one card does not change the original search filters for the whole result set. If the optional family lookup fails, the main results remain usable with a readable colour-options error.

`ES_BRAND_FIELD` and `ES_DIMENSION_FIELD` default to `brand` and `dimension`. `ES_REGION_FIELD`, when set, must be one of the known `region_<country>_<language>` paths. `ES_POC_MODE` is retained but does not change behavior. Autocorrection from the standalone example remains disabled.

Product descriptions are converted to plain text; links must use HTTP(S). Search timeouts, upstream errors, partial results, and empty matches are handled separately. A failed search is reported to the user, never replaced with invented products. Search totals for collapsed groups use Elasticsearch's approximate cardinality aggregation, matching the sample.

## API

- `GET /`: demo UI
- `GET /api/health`: server/configuration status (does not verify credentials)
- `POST /api/chat`: `{"messages":[{"role":"user","content":"Help me choose a sofa"}]}`
- `POST /api/chat/stream`: same request body, returning Server-Sent Events over a POST request. This is the endpoint used by the UI. The original JSON endpoint remains available.
- `POST /api/products/variant`: `{"product_id":"CURRENT_SKU","variant_id":"SELECTED_SKU","search_state":{...}}`; returns the selected product and its colour options. `search_state` uses the `/api/search` request format. Returns HTTP 409 when the chosen variant no longer satisfies the selection requirements, or 503 for search-service failures.
- `POST /api/products/similar`: `{"product_id":"SOURCE_SKU","search_state":{...},"limit":8}`; returns a `SearchResult` with `similar_to` identifying the source. Product attributes are read from the catalog, not supplied by the browser. Selecting Show similar after switching colour uses the currently displayed SKU.
- `POST /api/search`: returns filtered in-stock products without calling OpenAI:

```json
{
  "query": "",
  "filters": {
    "categories": ["sofa"],
    "colors": ["beige"],
    "max_price": 5000,
    "currency": "AED",
    "max_length_cm": 250
  },
  "sort": "price_asc",
  "region": "AE",
  "page": 1,
  "limit": 6
}
```

Try “Show beige sofas under 5,000 AED, cheapest first,” then “Only leather ones,” or “Maximum length 250 cm.” For a reference such as “the second one,” the UI sends displayed product names and SKUs as conversation context; the model searches again for current facts.

## Streaming

`main.py` streams OpenAI `response.output_text.delta` events while preserving the validated search-tool loop. Only reply text is forwarded; tool arguments and reasoning are not exposed. SSE frames contain JSON with a `type`:

- `status`: a readable progress `message`.
- `reset`: clear any text from a prior model/tool round.
- `delta`: append `text` to the current reply.
- `search`: display the latest catalog results, or a `search_error` if unavailable.
- `done`: authoritative final `data` containing `reply`, `search`, and `search_error`.
- `error`: terminal failure with a readable `message`; no successful completion follows.

`static/stream.js` handles UTF-8 characters and event boundaries split across network chunks. The browser commits conversation history and active filters only on `done`. Interrupted replies are removed and the input is restored for retry. Upstream streams close when processing ends or the request is cancelled. Configuration and request-validation failures return normal HTTP errors before streaming starts; later failures use SSE error events.

Streaming improves visible responsiveness; the model still needs to interpret the request and complete the search before generating a grounded product answer. The response disables caching and requests that compatible proxies disable buffering via `X-Accel-Buffering: no`.

## Checks

```bash
pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
node tests/stream.test.js
node tests/colors.test.js
node tests/filters.test.js
```

Tests mock OpenAI; a live model call requires a configured API key.
This is a local demo. Authentication and application rate limiting are needed before public deployment.
Next step: evaluate real customer searches and refine product comparisons and recommendations.
