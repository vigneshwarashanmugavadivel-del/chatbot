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
- The model owns retrieval, presentation and preference decisions. `search_products`, `get_product_details` and `find_similar_products` return catalog evidence with per-turn retrieval IDs; they never display products or update customer preferences. `display_products` explicitly selects ordered recommendation cards, a comparison, or text-only presentation. Every selected retrieval/product reference is validated against actual records from this turn. Comparison values come directly from the catalog; missing facts are labelled "Not listed". Comparison columns supplied for cards or text are ignored rather than causing a display failure.
- `update_preferences` explicitly saves or clears the complete search state for subsequent messages. Temporary investigation queries and presentation changes are independent of those saved preferences. The model receives confirmation/errors for every action and may revise its next decision without a fixed sequence or call cap.
- Replies are short conversational paragraphs rather than product/specification reports. A direct question is asked only when clarification is necessary; recommendations do not automatically end in a filter question.
- Four optional follow-up questions are generated from the conversation, latest reply, active preferences and actual catalog results together with the reply in the final structured model response. They can cover comparisons, styling, product details, related products or refinements; there are no category-specific question lists or browser-side filter heuristics. Clicking a question sends its text as an ordinary chat message while preserving the active search context. Suggestions also work for advice without product cards, replace the previous set, and are disabled while busy.
- The final chat model call returns both `reply` and four `follow_ups`; no extra suggestion call is made. Only the reply string is decoded and streamed to the browser, including split JSON escapes and Unicode characters. Suggestions appear when that same response finishes. A turn with no tools uses one model call; a turn with one tool round normally uses two. Show similar uses the same agent and combined final response.
- Replies stream into the chat as text is generated. Status messages show progress; product visuals appear only when an explicit display action is accepted, independently of retrieval completion.
- Catalog-backed product cards show larger images, a description, labelled brand/colour/material/dimension/size specifications, SKU, and formatted prices. Product navigation links and stock-status wording are hidden in the demo UI; availability checks remain enforced internally.
- Cards show selectable in-stock colour options. Selecting a colour refreshes that card's image, price, SKU, and specifications directly from Elasticsearch without a model call.
- Each card has a **Show similar** button. It sends an ordinary chat request through the agent, which can retrieve alternatives, select their presentation and generate the reply plus four follow-up questions in the same final response. The underlying similarity retrieval excludes the source family and retains the supplied non-identity constraints. Direct `/api/products/similar` remains a read-only retrieval endpoint without model generation.
- History stays in browser memory; reload or New chat clears it.
- The latest ten turns are sent as context. OpenAI response storage is disabled using `store=False`; provider retention policies still apply.
- API credentials stay on the backend. Missing configuration and upstream errors produce readable messages.
- The model calls the available retrieval and action tools and receives their results before responding. The server validates parameters and limits results to eight products per search. The model chooses its tools, arguments, order and number of calls; the loop continues until it returns a final answer, with no application-defined tool-call cap. Each tool result, including empty results and validation errors, is returned to the model for its next decision. Per-request upstream timeouts still apply, and disconnecting the browser cancels the stream.
- No cart actions, order tracking, or policy lookup yet.

## Elasticsearch implementation

`product_search.py` builds the search request and reuses `assemble_response` from the supplied `example/text_search.py`. It sends asynchronous HTTP `_msearch` requests using `httpx`. The example file is unchanged.

Text searches use one Elasticsearch `multi_match` clause over configured fields with `fuzziness: AUTO` and `operator: and`. All query words must match within a field. There are no parallel strict-query branches, phrase-boost loops, product dictionaries, or automatic SKU boosts. Relevance sorting, the `mad_id` tie-breaker, pagination, and optional `parent_id` collapse remain.

Customers can ask for an exact SKU, for example “Show me SKU SSRIANA3SG5”. The model can put the complete code in `filters.product_ids` with an empty text query; lookup arguments are independent of saved preferences. The backend uses exact case-normalized `.keyword` matching and preserves punctuation. Explicit text and SKU constraints, if both supplied, must both match. SKU/group fields never participate in fuzzy text search.

`search_filters.build_filter_clauses` translates validated preferences into filter clauses shared by text searches, SKU lookups, browsing, and similarity search. Structured filters and availability checks remain enforced. Fuzziness tolerates nearby spellings and many plural differences, but does not provide semantic synonyms or full linguistic stemming. Full stemming requires compatible index/search analyzers and reindexing into a new index.

The live mapping uses `keyword` fields with `.text` subfields. Default relevance weights are short description `^4`, title `^3`, full description `^2`, then category, material, and color `^1`. These weights influence relevance scores rather than imposing a strict ordering between every pair of results. Override `ES_SEARCH_FIELDS` with a comma-separated list, including optional boosts such as `title.text^2`. `ES_*_FIELD` settings map source data to product cards; `ES_URL_FIELD` and `ES_IMAGE_FIELD` default to `link` and `image_link`.

Broad product-name and descriptive requests use `query` to search title, short description and full description alongside the other text fields. For example, `{"query":"curved sofa"}` can match descriptive catalog text without requiring a category named “curved sofa”. Category filters are reserved for actual category restrictions and can be combined with keywords, e.g. `{"query":"marble","filters":{"categories":["dining table"]}}`. In a regional storefront, applicable text fields use that region's title/descriptions rather than root text.

Every search requires **positive stock** before ranking, pagination, and variant collapse. A second check removes any unavailable or unknown-stock product before returning results to the model or UI. Customers cannot disable this rule. Old chat results must be searched again before recommendation.

Supported filters:

- Category, color, material/finish, size label, and brand: phrases restricted to their respective fields. Category wording supports common singular/plural forms (sofas/sofa); beige includes light beige. These are field-specific phrase matches, not exact whole-field equality.
- Product type (`simple`, `configurable`, `bundle`), SKU, and group ID: case-normalized exact matches.
- Minimum/maximum effective price, currency, and on-sale status. Filters use a positive sale price only when below the regular price in the same currency; an invalid, zero, or higher sale price falls back to the regular price. Missing/invalid prices do not satisfy a budget filter. Zero prices are treated as unknown, not free items.
- Minimum/maximum labelled length, width, and height, expressed in cm. Catalog cm/mm/metre units are normalized. Missing, unlabelled, repeated variant measurements, ranges, or ambiguous measurements are excluded. Seat/arm/back measurements do not stand in for overall measurements.
- Region: `AE`, `SA`, `QA`, `OM`, or `BH` selects its English regional stock, prices, product text and links. No stock or price fallback to another region occurs. Without a region, `ES_REGION_FIELD` controls the default; its existing empty value uses root catalog data. Root currency defaults to `ES_DEFAULT_CURRENCY=AED`. No currency conversion occurs.

Values within one attribute array are alternatives (OR); different filters combine as requirements (AND). Ranges are inclusive. Sorting supports relevance, numeric effective price ascending/descending, and name A–Z. Price sorting stays within the selected currency. Filters also work with an empty query for catalog browsing. Style and seat count have no dedicated structured fields and remain keyword searches; the assistant must verify retrieved details.

Prices and measurements are parsed using request-scoped Elasticsearch runtime fields in `search_filters.py`, **without changing mappings or documents**. This uses the displayed price strings such as `1,214 AED`; the regional `price_vue`/`sale_price_vue` values were observed to differ from the displayed strings. For production scale, ingest normalized prices and measurements into dedicated indexed numeric fields instead of computing them per request.

Applied constraints appear above the product cards. The browser sends the last explicitly saved preferences as `search_state`. Each displayed card retains its own retrieval request for colour-selection context; displaying a card does not overwrite saved preferences. The model must retain explicit constraints unless the customer requests a change. This is conversation-based interpretation, not a deterministic preference parser.

Preference changes are model decisions executed through `update_preferences`. The action supplies a complete validated state, or null to clear it. Retrieval requests remain independent and the backend does not infer category changes or reset preferences from a query. Every displayed item retains the request that produced it, so colour/variant actions can validate that item's context without overwriting saved customer preferences.

Colour options are fetched in one batched `_msearch` for the displayed product families. Family matching uses the configured collapse/parent identifier, then a nonempty item group if no parent is known; products without either identifier keep only their known current colour. Records are never joined by title or an empty group. Each colour has a representative matching SKU; duplicate colour labels are combined. Only in-stock variants satisfying the other structured filters are offered. Discovering other colours drops the original free-text query, colour filter and specific-SKU filter, while retaining budget, material, category, size, dimensions, currency, sale status, group constraints and region.

Parent records without a colour are resolved to a concrete matching variant before display. If a variant has the parent's exact image URL it is preferred; otherwise the card uses a real variant's own image and details. This keeps the displayed SKU, colour, image, price and specifications together and allows the matching colour button to be preselected.

Selecting a colour rechecks the original and selected SKUs, their family relationship, positive stock, and the retained filters. If availability changes, the UI keeps the current card and displays an error. A successful selection replaces just that card, marks the selected colour, and records the displayed SKU for chat follow-ups. Changing colour on one card does not change the original search filters for the whole result set. If the optional family lookup fails, the main results remain usable with a readable colour-options error.

`ES_BRAND_FIELD` and `ES_DIMENSION_FIELD` default to `brand` and `dimension`. `ES_REGION_FIELD`, when set, must be one of the known `region_<country>_<language>` paths. `ES_POC_MODE` is retained but does not change behavior. Autocorrection from the standalone example remains disabled.

Product descriptions are converted to plain text; links must use HTTP(S). Search timeouts, upstream errors, partial results, and empty matches are handled separately. A failed search is reported to the user, never replaced with invented products. Retrieval totals for collapsed groups use Elasticsearch's approximate cardinality aggregation, matching the sample. Displayed recommendations can be an explicitly selected subset of those results.

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
- `trace`: the turn's `trace_id`, also included in the final response.
- `reset`: clear any text from a prior model/tool round.
- `delta`: append `text` to the current reply.
- `presentation`: render the model-selected cards/comparison, or clear the current turn visual for text-only presentation. Retrieval calls emit no presentation event.
- `preferences`: report an explicit preference update; the final state is authoritative for the next user turn.
- `done`: authoritative final `data` containing `reply`, `presentation`, `search_state`, and four `follow_ups`. Retrieval errors are returned to the model for recovery, rather than replacing an accepted presentation.
- `error`: terminal failure with a readable `message`; no successful completion follows.

`static/stream.js` handles UTF-8 characters and event boundaries split across network chunks. The browser commits conversation history and active filters only on `done`. Interrupted replies are removed and the input is restored for retry. Upstream streams close when processing ends or the request is cancelled. Configuration and request-validation failures return normal HTTP errors before streaming starts; later failures use SSE error events.

Streaming improves visible responsiveness; the model still needs to interpret the request and complete the search before generating a grounded product answer. There is no browser-wide total timeout for chat turns; individual catalog/model calls retain their own timeouts. The response disables caching and requests that compatible proxies disable buffering via `X-Accel-Buffering: no`.

## Agent logs

Agent traces are written automatically to `logs/agent.jsonl` (one JSON object per line). Each turn has a unique `trace_id`; records include IST timestamps (`Asia/Kolkata`, `+05:30`), ordered step numbers, model rounds, token usage when available, tool arguments/results and durations, Elasticsearch request bodies/responses, presentation/preference events, and final replies or failures/cancellations. Streaming chunks log character counts; the complete text is stored with the completed model/final response.

The same events are also written to `logs/agent.log` as compact, single-line step summaries with timestamps, trace IDs, timings, product counts, and a short preview of the user message and final reply. Each completed model round also includes compact `LLM output:` lines showing every generated tool call with its arguments, or the complete final response with follow-up suggestions. These outputs are not truncated. Full prompts, catalog payloads, streaming chunks, and UI bookkeeping are omitted from the readable log; full details remain in JSONL. For a custom `AGENT_LOG_PATH`, the companion uses the same filename with a `.log` suffix (or `.readable.log` appended if the configured path already ends in `.log`). When the companion is first created, existing events in the current JSONL file are converted automatically; existing rotated backups are not converted. Both formats rotate independently using the same limits.

Credentials are redacted, authorization headers are not logged, and hidden/encrypted reasoning is omitted. Log files use owner-only permissions and `logs/` is excluded from Git. Customer messages and catalog records are retained in these local traces. The default rotation is 10 MiB per file with five backups. Configure `AGENT_LOG_PATH`, `AGENT_LOG_MAX_BYTES` and `AGENT_LOG_BACKUP_COUNT` in `.env`; relative paths resolve from the project directory.

```bash
tail -f logs/agent.log
rg 'YOUR_TRACE_ID' logs/agent.jsonl
```

## Checks

```bash
pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
node tests/stream.test.js
node tests/colors.test.js
node tests/follow-ups.test.js
```

Tests mock OpenAI; a live model call requires a configured API key.
This is a local demo. Authentication and application rate limiting are needed before public deployment.
Next step: evaluate real customer searches and refine product comparisons and recommendations.
