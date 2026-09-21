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
# The plan carries no ORDER BY and no literal filter, so it isolates the
# relationship check. The two shapes that used to fail -- ORDER BY over a
# function and an equality filter on a real value outside the sample -- are
# covered by ``RULES_TOP_GENRE`` and ``RULES_JAZZ_TRACKS`` below.
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


# ---------------------------------------------------------------------------
# The two shapes that `LogicalValidatorNode` used to block, one per defect.
# ---------------------------------------------------------------------------

TOP_GENRE_DECOMPOSER = {
    "sub_queries": [{
        "id": "sq1", "datasource_id": "chinook",
        "intent": "Tracks sold per genre, highest first",
        "metrics": [{"name": "tracks_sold", "aggregation": "count"}],
        "filters": [], "group_by": [{"attribute": "genre name"}],
        "expected_schema": [{"name": "genre_name", "dtype": "string"},
                            {"name": "tracks_sold", "dtype": "int"}],
    }],
    "combine_groups": [{"group_id": "g1", "operation": "standalone",
                        "inputs": [{"subquery_id": "sq1"}], "join_keys": []}],
    "post_combine_ops": [], "unmapped_subqueries": [],
}

# "Which genre sells the most tracks?" -- one of the twelve guided questions,
# and the "top N by <aggregate>" shape. Its ORDER BY is a function call, which
# is what used to reach `qualify()` unwrapped and abort validation with
# VALIDATOR_CRASH before any SQL was generated.
TOP_GENRE_PLAN = {
    "query_type": "READ", "distinct": False,
    "tables": [
        {"name": "InvoiceLine", "alias": "t1", "ordinal": 0},
        {"name": "Track", "alias": "t2", "ordinal": 1},
        {"name": "Genre", "alias": "t3", "ordinal": 2},
    ],
    "joins": [
        {"left_alias": "t1", "right_alias": "t2", "join_type": "inner", "ordinal": 0,
         "condition": {"kind": "binary", "op": "=",
                       "left": {"kind": "column", "alias": "t1", "column_name": "TrackId"},
                       "right": {"kind": "column", "alias": "t2", "column_name": "TrackId"}}},
        {"left_alias": "t2", "right_alias": "t3", "join_type": "inner", "ordinal": 1,
         "condition": {"kind": "binary", "op": "=",
                       "left": {"kind": "column", "alias": "t2", "column_name": "GenreId"},
                       "right": {"kind": "column", "alias": "t3", "column_name": "GenreId"}}},
    ],
    "select_items": [
        {"ordinal": 0, "alias": "genre_name",
         "expr": {"kind": "column", "alias": "t3", "column_name": "Name"}},
        {"ordinal": 1, "alias": "tracks_sold",
         "expr": {"kind": "func", "func_name": "COUNT", "is_aggregate": True,
                  "args": [{"kind": "column", "alias": "t1", "column_name": "InvoiceLineId"}]}},
    ],
    "group_by": [{"ordinal": 0, "expr": {"kind": "column", "alias": "t3", "column_name": "Name"}}],
    "order_by": [{"ordinal": 0, "direction": "desc",
                  "expr": {"kind": "func", "func_name": "COUNT", "is_aggregate": True,
                           "args": [{"kind": "column", "alias": "t1", "column_name": "InvoiceLineId"}]}}],
    "reasoning": "Count sold invoice lines per genre and sort descending.",
}


def top_genre_answer(prompt_text: str) -> dict:
    """Echoes the first row, so the assertion can see the real sort order."""
    m = re.search(r'"genre_name":\s*"([^"]+)"[^}]*"tracks_sold":\s*(\d+)', prompt_text)
    top = f"{m.group(1)} with {m.group(2)}" if m else "?"
    return {"summary": f"Top genre: {top}.", "format_type": "text",
            "content": f"Top genre: {top}.", "warnings": []}


RULES_TOP_GENRE = [
    Rule("DecomposerResponse", TOP_GENRE_DECOMPOSER),
    Rule("PlanModel", TOP_GENRE_PLAN),
    Rule("AggregatedResponse", top_genre_answer),
    Rule("plain", "Keep the same plan."),
]


JAZZ_TRACKS_DECOMPOSER = {
    "sub_queries": [{
        "id": "sq1", "datasource_id": "chinook",
        "intent": "How many tracks are in the Jazz genre",
        "metrics": [{"name": "track_count", "aggregation": "count"}],
        "filters": [{"attribute": "genre name", "operator": "=", "value": "Jazz"}],
        "group_by": [],
        "expected_schema": [{"name": "track_count", "dtype": "int"}],
    }],
    "combine_groups": [{"group_id": "g1", "operation": "standalone",
                        "inputs": [{"subquery_id": "sq1"}], "join_keys": []}],
    "post_combine_ops": [], "unmapped_subqueries": [],
}

# `Genre.Name = 'Jazz'` is the literal-filter shape, and 'Jazz' is genuinely
# outside the five values the adapter samples for that column: all 25 genres
# occur once, so `_get_sample_values`' "five most frequent" tie-breaks to
# 'World', 'TV Shows', 'Soundtrack', 'Science Fiction', 'Sci Fi & Fantasy'.
# The validator used to read that sample as the column's whole domain and
# reject this correct filter with INVALID_PLAN_STRUCTURE.
JAZZ_TRACKS_PLAN = {
    "query_type": "READ", "distinct": False,
    "tables": [
        {"name": "Track", "alias": "t1", "ordinal": 0},
        {"name": "Genre", "alias": "t2", "ordinal": 1},
    ],
    "joins": [{
        "left_alias": "t1", "right_alias": "t2", "join_type": "inner", "ordinal": 0,
        "condition": {"kind": "binary", "op": "=",
                      "left": {"kind": "column", "alias": "t1", "column_name": "GenreId"},
                      "right": {"kind": "column", "alias": "t2", "column_name": "GenreId"}},
    }],
    "select_items": [{"ordinal": 0, "alias": "track_count",
                      "expr": {"kind": "func", "func_name": "COUNT", "is_aggregate": True,
                               "args": [{"kind": "column", "alias": "t1", "column_name": "TrackId"}]}}],
    "where": {"kind": "binary", "op": "=",
              "left": {"kind": "column", "alias": "t2", "column_name": "Name"},
              "right": {"kind": "literal", "value": "Jazz"}},
    "group_by": [], "order_by": [],
    "reasoning": "Count Track rows whose genre is Jazz.",
}


def jazz_tracks_answer(prompt_text: str) -> dict:
    nums = re.findall(r"track_count[^0-9]*(\d+)", prompt_text)
    n = nums[0] if nums else "?"
    return {"summary": f"There are {n} jazz tracks.", "format_type": "text",
            "content": f"There are {n} jazz tracks.", "warnings": []}


RULES_JAZZ_TRACKS = [
    Rule("DecomposerResponse", JAZZ_TRACKS_DECOMPOSER),
    Rule("PlanModel", JAZZ_TRACKS_PLAN),
    Rule("AggregatedResponse", jazz_tracks_answer),
    Rule("plain", "Keep the same plan."),
]
