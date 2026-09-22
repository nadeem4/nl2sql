"""Aggregator crashes and dropped fields from the first gpt-5.4 tier 2 run.

* chinook_011 ("playlists with tracks from more than three genres") crashed
  with ``'DataFrame' object has no attribute 'groupby'``: polars 1.x calls it
  ``group_by``, so every grouped post-combine aggregate failed.
* chinook_009 ("customers who bought jazz but never rock") crashed with
  ``unable to find column "right.customer"``: the model wrote the join key
  with its side as a prefix.
* A post-combine op applied only the field its ``operation`` names, so the
  decomposer's own example, a ``filter`` op with ``order_by`` and ``limit``,
  lost its ordering and limit.
"""
import polars as pl
import pytest

from nl2sql.aggregation.engines.polars_duckdb import PolarsDuckdbEngine


@pytest.fixture
def engine():
    return PolarsDuckdbEngine.__new__(PolarsDuckdbEngine)  # no artifact store needed


PLAYLIST_GENRES = pl.DataFrame({
    "playlist": ["Music", "Music", "Music", "Music", "Movies"],
    "genre": ["Rock", "Jazz", "Metal", "Blues", "Rock"],
})


def test_a_grouped_aggregate_uses_polars_group_by(engine):
    counted = engine.post_op("aggregate", PLAYLIST_GENRES, {
        "group_by": [{"attribute": "playlist"}],
        "metrics": [{"name": "genre", "aggregation": "count"}],
    })

    assert sorted(counted.rows()) == [("Movies", 1), ("Music", 4)]


def test_an_aggregate_op_applies_its_filter_after_aggregating(engine):
    # chinook_011's shape: count genres per playlist, keep those with more than three.
    kept = engine.post_op("aggregate", PLAYLIST_GENRES, {
        "group_by": [{"attribute": "playlist"}],
        "metrics": [{"name": "genre", "aggregation": "count"}],
        "filters": [{"attribute": "genre", "operator": ">", "value": 3}],
    })

    assert kept.rows() == [("Music", 4)]


def test_a_filter_op_also_applies_its_order_by_and_limit(engine):
    spend = pl.DataFrame({"customer": ["a", "b", "c", "d"], "total": [50.0, 40.0, 70.0, 60.0]})

    top = engine.post_op("filter", spend, {
        "filters": [{"attribute": "total", "operator": ">", "value": 45}],
        "order_by": [{"attribute": "total", "direction": "desc"}],
        "limit": 2,
    })

    assert top.rows() == [("c", 70.0), ("d", 60.0)]


def test_a_sort_op_applies_its_limit(engine):
    frame = pl.DataFrame({"artist": ["x", "y", "z"], "albums": [3, 9, 5]})

    top = engine.post_op("sort", frame, {"order_by": [{"attribute": "albums", "direction": "desc"}], "limit": 1})

    assert top.rows() == [("y", 9)]


@pytest.mark.parametrize("left, right", [
    ("customer", "right.customer"),
    ("left.customer", "right.customer"),
    ("sq_jazz.customer", "sq_rock.customer"),
])
def test_join_keys_written_with_a_side_prefix_resolve_to_the_column(engine, left, right):
    jazz = pl.DataFrame({"customer": ["Ann", "Bo"]})
    rock = pl.DataFrame({"customer": ["Bo", "Cy"]})

    both = engine.combine("join", [("left", jazz), ("right", rock)], [{"left": left, "right": right}])

    assert both.rows() == [("Bo",)]


def test_an_unknown_join_key_still_fails_clearly(engine):
    frame = pl.DataFrame({"customer": ["Ann"]})

    with pytest.raises(Exception, match="nope"):
        engine.combine("join", [("left", frame), ("right", frame)], [{"left": "customer", "right": "nope"}])
