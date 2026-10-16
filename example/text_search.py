"""Standalone version of Robin's Text Search workflow.

The three public stages are build_query, fetch_results, and assemble_response.
Only the Elasticsearch Python client is needed; no Robin modules are imported.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class SearchConfig:
    index: str
    search_fields: tuple[str, ...]
    source_fields: tuple[str, ...]
    facet_types: Mapping[str, str] | None = None
    collapse_field: str | None = None
    autocorrect: bool = True
    post_filter: bool = False
    timeout: float = 20.0


def _filter_clause(item: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    """Translate the common Robin filter operators into Elasticsearch DSL."""
    field = item["field"]
    operator = item.get("operator", "exact")
    value = item.get("value")
    if operator in {"exact", "eq"}:
        return "filter", {"terms": {field: value if isinstance(value, list) else [value]}}
    if operator in {"not-exact", "ne"}:
        return "must_not", {"terms": {field: value if isinstance(value, list) else [value]}}
    if operator == "exists":
        clause = {"exists": {"field": field}}
        return ("filter" if value else "must_not"), clause
    if operator in {"gt", "gte", "lt", "lte"}:
        return "filter", {"range": {field: {operator: value}}}
    if operator == "range":
        if not isinstance(value, dict) or not set(value).issubset({"gt", "gte", "lt", "lte"}):
            raise ValueError("range filter value must be a dict with gt/gte/lt/lte keys")
        return "filter", {"range": {field: value}}
    raise ValueError(f"Unsupported filter operator: {operator}")


def _filter_bool(filters: list[Mapping[str, Any]]) -> dict[str, Any]:
    result: dict[str, list[dict[str, Any]]] = {"filter": [], "must_not": []}
    for item in filters:
        bucket, clause = _filter_clause(item)
        result[bucket].append(clause)
    return {"bool": {key: value for key, value in result.items() if value}} if filters else {"match_all": {}}


def _facet_aggregations(
    requested: list[str], facet_types: Mapping[str, str], filters: list[Mapping[str, Any]], limit: int
) -> dict[str, Any]:
    aggs = {}
    for field in requested:
        kind = facet_types.get(field)
        if kind not in {"terms", "stats"}:
            raise ValueError(f"Facet {field!r} must be configured as terms or stats")
        # Robin excludes a facet's own filter when computing its buckets.
        other_filters = [item for item in filters if item["field"].split(".keyword")[0] != field]
        inner = {kind: {"field": field}}
        if kind == "terms":
            inner[kind]["size"] = limit
        aggs[field] = {"filter": _filter_bool(other_filters), "aggs": {field: inner}}
    return aggs


def build_query(config: SearchConfig, request: Mapping[str, Any], search_text: str | None = None) -> dict[str, Any]:
    """Build the product query sent as the body of an Elasticsearch msearch."""
    raw_text = request.get("search_query", "")
    text = (search_text if search_text is not None else raw_text).strip()
    filters = list(request.get("filters") or [])
    facets = list(request.get("facets") or [])
    page = int(request.get("page_num", 1))
    size = int(request.get("num_results", 16))
    if page < 1 or size < 1:
        raise ValueError("page_num and num_results must be positive")
    if page * size > 10000:
        raise ValueError("requested page exceeds Elasticsearch's default 10000-result window")

    bool_query: dict[str, Any] = {"must": [], "should": []}
    if text:
        bool_query["must"].append({"simple_query_string": {
            "query": text, "fields": list(config.search_fields), "minimum_should_match": "100%"
        }})
        bool_query["should"].extend(
            {"match_phrase": {field: text}} for field in config.search_fields
        )
    else:
        bool_query["must"].append({"match_all": {}})

    use_post_filter = bool(request.get("post_filter", config.post_filter)) or not text
    if filters and not use_post_filter:
        filter_bool = _filter_bool(filters)["bool"]
        bool_query.update(filter_bool)

    body: dict[str, Any] = {
        "query": {"bool": bool_query},
        "_source": list(config.source_fields),
        "from": (page - 1) * size,
        "size": size,
        "track_total_hits": True,
    }
    if filters and use_post_filter:
        body["post_filter"] = _filter_bool(filters)
    if facets:
        body["aggs"] = _facet_aggregations(
            facets, config.facet_types or {}, filters, int(request.get("facet_limit", 30))
        )
    if config.collapse_field:
        body["collapse"] = {"field": config.collapse_field}
        body.setdefault("aggs", {})["unique_recs_count"] = {
            "cardinality": {"field": config.collapse_field}
        }

    sort = request.get("sort_by") or []
    if isinstance(sort, dict):
        sort = [sort]
    sort = list(sort)
    if not any(item["field"].lower() in {"relevance", "_score"} for item in sort):
        sort.append({"field": "Relevance", "order": "desc"})
    body["sort"] = [
        {"_score" if item["field"].lower() == "relevance" else item["field"]: {
            "order": item.get("order", "asc").lower()
        }} for item in sort
    ]
    body["sort"].append({"mad_id": {"order": "desc"}})
    return body


def _autocorrect(client: Any, config: SearchConfig, text: str) -> str | None:
    """Use the same phrase-suggester fields as Robin when enabled."""
    suggest_body = {"suggest": {"_all_auto_correct": {
        "text": text,
        "phrase": {
            "field": "_all.trigram", "size": 1, "gram_size": 3,
            "direct_generator": [
                {"field": "_all.trigram", "suggest_mode": "missing", "min_word_length": 3},
                {"field": "_all.reverse", "suggest_mode": "missing", "min_word_length": 3},
            ],
        },
    }}}
    response = client.search(index=config.index, body=suggest_body, size=0)
    suggestions = response.get("suggest", {}).get("_all_auto_correct", [])
    options = suggestions[0].get("options", []) if suggestions else []
    return options[0]["text"] if options else None


def fetch_results(client: Any, config: SearchConfig, body: Mapping[str, Any]) -> Mapping[str, Any]:
    """Run the product query through msearch, as Robin's get_es_results does."""
    response = client.msearch(
        body=[{"index": config.index}, dict(body)], request_timeout=config.timeout
    )
    results = response.get("responses", [])
    if not results:
        raise RuntimeError("Elasticsearch msearch returned no responses")
    result = results[0]
    if result.get("error"):
        raise RuntimeError(f"Elasticsearch search failed: {result['error']}")
    return result


def assemble_response(
    result: Mapping[str, Any], request: Mapping[str, Any], corrected_text: str | None = None,
    collapse_field: str | None = None,
) -> dict[str, Any]:
    """Turn Elasticsearch hits and aggregations into the text-search result."""
    products = []
    for hit in result.get("hits", {}).get("hits", []):
        product = dict(hit.get("_source", {}))
        if "product_id" not in product and "ext_id" in product:
            product["product_id"] = product["ext_id"]
        product.pop("mad_id", None)
        products.append(product)

    total = result.get("hits", {}).get("total", 0)
    total = total.get("value", 0) if isinstance(total, dict) else total
    aggs = result.get("aggregations", {})
    if collapse_field:
        total = aggs.get("unique_recs_count", {}).get("value", total)
    facets: dict[str, Any] = {}
    range_facets: dict[str, Any] = {}
    for field in request.get("facets") or []:
        value = aggs.get(field, {}).get(field, {})
        if "buckets" in value:
            facets[field] = {
                bucket.get("key_as_string", bucket.get("key")): bucket.get("doc_count", 0)
                for bucket in value["buckets"]
            }
        elif value:
            range_facets[field] = {key: value.get(key) for key in ("min", "max", "avg")}

    size = int(request.get("num_results", 16))
    return {
        "Products": products,
        "query": {"actual": request.get("search_query", ""), "auto_corrected": corrected_text},
        "total_count": total,
        "num_of_pages": math.ceil(total / size),
        "page_num": int(request.get("page_num", 1)),
        "page_size": len(products),
        "facets": facets,
        "range_facet": range_facets,
    }


class TextSearcher:
    def __init__(self, client: Any, config: SearchConfig):
        self.client = client
        self.config = config

    def search(self, request: Mapping[str, Any]) -> dict[str, Any]:
        actual = str(request.get("search_query", "")).strip()
        corrected = None
        if actual and request.get("auto_correct", self.config.autocorrect):
            corrected = _autocorrect(self.client, self.config, actual)
        body = build_query(self.config, request, corrected or actual)
        result = fetch_results(self.client, self.config, body)
        return assemble_response(result, request, corrected, self.config.collapse_field)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run standalone Elasticsearch text search")
    parser.add_argument("request", help="Path to a JSON search request")
    parser.add_argument("--index", default=os.environ.get("ES_INDEX"), help="Elasticsearch index")
    parser.add_argument("--fields", default=os.environ.get("ES_SEARCH_FIELDS", "title.text"),
                        help="Comma-separated searchable fields")
    parser.add_argument("--source-fields", default=os.environ.get("ES_SOURCE_FIELDS", "title,price,ext_id,mad_id"),
                        help="Comma-separated product fields to return")
    parser.add_argument("--facet-types", default="{}", help='JSON mapping, e.g. {"brand.keyword":"terms"}')
    parser.add_argument("--collapse-field", default=None)
    args = parser.parse_args()
    if not args.index:
        parser.error("set --index or ES_INDEX")
    try:
        from elasticsearch import Elasticsearch
    except ImportError as exc:
        raise SystemExit("Install elasticsearch==7.13.0 first") from exc

    es_url = os.environ.get("ES_URL", "http://localhost:9200")
    es_user = os.environ.get("ES_USER")
    es_password = os.environ.get("ES_PASSWORD")
    auth = (es_user, es_password) if es_user and es_password else None
    client = Elasticsearch([es_url], http_auth=auth)
    config = SearchConfig(
        index=args.index,
        search_fields=tuple(field.strip() for field in args.fields.split(",") if field.strip()),
        source_fields=tuple(field.strip() for field in args.source_fields.split(",") if field.strip()),
        facet_types=json.loads(args.facet_types),
        collapse_field=args.collapse_field,
    )
    with open(args.request, encoding="utf-8") as file:
        request = json.load(file)
    json.dump(TextSearcher(client, config).search(request), sys.stdout, indent=2)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
