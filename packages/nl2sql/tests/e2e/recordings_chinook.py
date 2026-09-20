"""Fake-LLM payloads for the Chinook demo, keyed the way `FakeLLMServer` dispatches.

Every table, column and value below is real Chinook: `Customer` has 59 rows and
`Album` carries a declared foreign key to `Artist`, which is what makes the
second scenario worth having. The manufacturing DDL these replaced declared no
foreign keys at all, so no end-to-end test ever drove a join through
`LogicalValidatorNode`'s relationship check.
"""
import re

from nl2sql.testing.fake_llm import Rule

COUNT_CUSTOMERS_DECOMPOSER = {
    "sub_queries": [{
        "id": "sq1", "datasource_id": "chinook",
        "intent": "How many customers are there",
        "metrics": [{"name": "customer_count", "aggregation": "count"}],
        "filters": [], "group_by": [],
        "expected_schema": [{"name": "customer_count", "dtype": "int"}],
    }],
    "combine_groups": [{"group_id": "g1", "operation": "standalone",
                        "inputs": [{"subquery_id": "sq1"}], "join_keys": []}],
    "post_combine_ops": [], "unmapped_subqueries": [],
}

COUNT_CUSTOMERS_PLAN = {
    "query_type": "READ", "distinct": False,
    "tables": [{"name": "Customer", "alias": "t1", "ordinal": 0}],
    "joins": [],
    "select_items": [{"ordinal": 0, "alias": "customer_count",
                      "expr": {"kind": "func", "func_name": "COUNT", "is_aggregate": True,
                               "args": [{"kind": "column", "alias": "t1", "column_name": "CustomerId"}]}}],
    "group_by": [], "order_by": [],
    "reasoning": "Count rows in Customer.",
}


def count_customers_answer(prompt_text: str) -> dict:
    nums = re.findall(r"customer_count[^0-9]*(\d+)", prompt_text)
    n = nums[0] if nums else "?"
    return {"summary": f"There are {n} customers.", "format_type": "text",
            "content": f"There are {n} customers.", "warnings": []}


RULES_COUNT_CUSTOMERS = [
    Rule("DecomposerResponse", COUNT_CUSTOMERS_DECOMPOSER),
    Rule("PlanModel", COUNT_CUSTOMERS_PLAN),
    Rule("AggregatedResponse", count_customers_answer),
    Rule("plain", "Keep the same plan."),
]


ALBUMS_PER_ARTIST_DECOMPOSER = {
    "sub_queries": [{
        "id": "sq1", "datasource_id": "chinook",
        "intent": "Number of albums per artist",
        "metrics": [{"name": "album_count", "aggregation": "count"}],
        "filters": [], "group_by": [{"attribute": "artist name"}],
        "expected_schema": [{"name": "artist_name", "dtype": "string"},
                            {"name": "album_count", "dtype": "int"}],
    }],
    "combine_groups": [{"group_id": "g1", "operation": "standalone",
                        "inputs": [{"subquery_id": "sq1"}], "join_keys": []}],
    "post_combine_ops": [], "unmapped_subqueries": [],
}

# Album.ArtistId -> Artist.ArtistId is a real FOREIGN KEY in the vendored
# database, so `LogicalValidatorNode` must match this join against a declared
# relationship instead of rejecting it. The manufacturing DDL declared none, so
# a passing relationship match could not be told from a broken one end to end.
#
# The plan deliberately carries no ORDER BY and no literal filter: both crash
# or are wrongly rejected by the validator today, independently of the join.
# Those two defects are pinned in
# ``tests/unit/test_logical_validator_known_defects.py``.
ALBUMS_PER_ARTIST_PLAN = {
    "query_type": "READ", "distinct": False,
    "tables": [
        {"name": "Album", "alias": "t1", "ordinal": 0},
        {"name": "Artist", "alias": "t2", "ordinal": 1},
    ],
    "joins": [{
        "left_alias": "t1", "right_alias": "t2", "join_type": "inner", "ordinal": 0,
        "condition": {"kind": "binary", "op": "=",
                      "left": {"kind": "column", "alias": "t1", "column_name": "ArtistId"},
                      "right": {"kind": "column", "alias": "t2", "column_name": "ArtistId"}},
    }],
    "select_items": [
        {"ordinal": 0, "alias": "artist_name",
         "expr": {"kind": "column", "alias": "t2", "column_name": "Name"}},
        {"ordinal": 1, "alias": "album_count",
         "expr": {"kind": "func", "func_name": "COUNT", "is_aggregate": True,
                  "args": [{"kind": "column", "alias": "t1", "column_name": "AlbumId"}]}},
    ],
    "group_by": [{"ordinal": 0, "expr": {"kind": "column", "alias": "t2", "column_name": "Name"}}],
    "order_by": [],
    "reasoning": "Join Album to Artist on the declared foreign key and count albums per artist.",
}


def albums_per_artist_answer(_prompt_text: str) -> dict:
    return {"summary": "Album counts per artist.", "format_type": "text",
            "content": "Album counts per artist.", "warnings": []}


RULES_ALBUMS_PER_ARTIST = [
    Rule("DecomposerResponse", ALBUMS_PER_ARTIST_DECOMPOSER),
    Rule("PlanModel", ALBUMS_PER_ARTIST_PLAN),
    Rule("AggregatedResponse", albums_per_artist_answer),
    Rule("plain", "Keep the same plan."),
]
