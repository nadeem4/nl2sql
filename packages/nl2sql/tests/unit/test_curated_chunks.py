"""Verified queries become index entries: one per phrasing, plus the description.

A raw ``{n}`` embeds poorly, so every placeholder is filled with the param's
``example``, else its ``default``, else its own name. Ids are deterministic and
the metadata carries ``datasource_id`` and ``query_id``, the keys the
active-build filter, index health and the candidate search read.
"""
from __future__ import annotations

from nl2sql.configs.curated import CuratedDatasource, CuratedFileConfig
from nl2sql.indexing.curated import (
    CuratedChunkBuilder,
    curated_hash,
    curated_hashes,
    fill_placeholders,
)
from nl2sql.indexing.models import VerifiedQueryChunk


def _section() -> CuratedDatasource:
    return CuratedDatasource.model_validate({
        "verified_queries": [
            {
                "id": "top_customers_by_revenue",
                "description": "Customers ranked by invoiced revenue in a calendar year",
                "questions": [
                    "Who are our top {n} customers by revenue in {year}?",
                    "Best {n} customers in {year}",
                ],
                "params": {
                    "n": {"type": "int", "default": 10, "min": 1, "max": 100},
                    "year": {"type": "int", "min": 2000, "max": 2100, "example": 2012},
                },
                "sql": "SELECT 1 WHERE :n = :year",
            },
            {
                "id": "tracks_by_genre",
                "description": "Number of tracks per genre",
                "questions": ["How many tracks are in each genre?"],
                "sql": "SELECT 1",
            },
        ]
    })


def test_placeholders_take_the_example_then_the_default_then_the_name():
    params = _section().verified_queries[0].params
    assert fill_placeholders("Top {n} in {year}", params) == "Top 10 in 2012"

    params["year"].example = None
    assert fill_placeholders("Top {n} in {year}", params) == "Top 10 in year"


def test_an_example_wins_over_a_default():
    section = CuratedDatasource.model_validate({"verified_queries": [{
        "id": "q", "description": "d", "questions": ["Top {n}"],
        "params": {"n": {"type": "int", "default": 10, "example": 5}}, "sql": "SELECT :n",
    }]})
    assert fill_placeholders("Top {n}", section.verified_queries[0].params) == "Top 5"


def test_one_entry_per_phrasing_plus_the_description():
    chunks = CuratedChunkBuilder("chinook", _section(), "v1").build()

    assert all(isinstance(c, VerifiedQueryChunk) for c in chunks)
    texts = [c.get_page_content() for c in chunks]
    assert texts == [
        "Who are our top 10 customers by revenue in 2012?",
        "Best 10 customers in 2012",
        "Customers ranked by invoiced revenue in a calendar year",
        "How many tracks are in each genre?",
        "Number of tracks per genre",
    ]


def test_ids_are_deterministic_and_metadata_names_the_query():
    chunks = CuratedChunkBuilder("chinook", _section(), "v1").build()

    assert [c.id for c in chunks[:3]] == [
        "curated.verified_query:chinook:top_customers_by_revenue:0",
        "curated.verified_query:chinook:top_customers_by_revenue:1",
        "curated.verified_query:chinook:top_customers_by_revenue:2",
    ]
    assert [c.id for c in CuratedChunkBuilder("chinook", _section(), "v1").build()] == [c.id for c in chunks]
    md = chunks[0].get_metadata()
    assert md == {
        "id": "curated.verified_query:chinook:top_customers_by_revenue:0",
        "type": "curated.verified_query",
        "datasource_id": "chinook",
        "query_id": "top_customers_by_revenue",
        "schema_version": "v1",
    }


def test_no_section_builds_nothing():
    assert CuratedChunkBuilder("chinook", None, "v1").build() == []
    assert CuratedChunkBuilder("chinook", CuratedDatasource(), "v1").build() == []


def test_the_hash_follows_the_section_content_not_its_key_order():
    a = _section()
    b = CuratedDatasource.model_validate(a.model_dump())
    assert curated_hash(a) == curated_hash(b)

    b.verified_queries[1].questions.append("Tracks per genre")
    assert curated_hash(a) != curated_hash(b)
    assert curated_hash(None) == curated_hash(CuratedDatasource())


def test_curated_hashes_cover_every_datasource_asked_for():
    cfg = CuratedFileConfig(datasources={"chinook": _section()})

    hashes = curated_hashes(cfg, ["chinook", "sales"])

    assert hashes["chinook"] == curated_hash(_section())
    assert hashes["sales"] == curated_hash(None)
    assert curated_hashes(None, ["chinook"]) == {"chinook": curated_hash(None)}
