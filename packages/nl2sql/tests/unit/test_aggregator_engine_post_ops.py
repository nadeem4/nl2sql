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


# ---------------------------------------------------------------------------
# chinook_009, from the recorded plan in
# benchmarks/tier2/chinook/2026-09-23_de42c40_gpt-5.4.json.
#
# The decomposer split "Which customers bought jazz tracks but never rock?"
# into two sub-queries -- "customers who bought rock tracks" and "customers who
# bought jazz tracks" -- each selecting one column aliased `customer`, and
# combined them. There is no anti-join in the plan language, so the only
# two-input operations available are `join` and `compare`, both inner. The
# model reached for a right-hand column to negate against; `right.customer` is
# that invention, and an inner join keyed on `customer` does not keep it.
# ---------------------------------------------------------------------------

JAZZ = pl.DataFrame({"customer": ["Ann", "Bo"]})
ROCK = pl.DataFrame({"customer": ["Bo", "Cy"]})


def _joined(engine):
    return engine.combine("join", [("left", JAZZ), ("right", ROCK)],
                          [{"left": "customer", "right": "right.customer"}])


def test_the_inner_join_keeps_only_the_shared_key_column(engine):
    # The premise of every assertion below: polars drops the right-hand key
    # when `right_on` names it, so the combined frame is exactly ["customer"]
    # -- which is the `valid columns: ["customer"]` in the recorded failure.
    joined = _joined(engine)

    assert joined.columns == ["customer"]
    assert joined.rows() == [("Bo",)]  # the intersection: bought *both*


@pytest.mark.parametrize("operation, attributes", [
    ("filter", {"filters": [{"attribute": "right.customer", "operator": "!=", "value": "Bo"}]}),
    ("project", {"expected_schema": [{"name": "right.customer"}]}),
    ("sort", {"order_by": [{"attribute": "right.customer", "direction": "asc"}]}),
    ("aggregate", {"group_by": [{"attribute": "right.customer"}],
                   "metrics": [{"name": "customer", "aggregation": "count"}]}),
])
def test_a_side_qualified_post_combine_attribute_is_refused_with_an_explanation(
    engine, operation, attributes
):
    # The recorded crash was polars' own `unable to find column
    # "right.customer"; valid columns: ["customer"]`, which says nothing a
    # caller or a planner can act on.
    with pytest.raises(ValueError) as caught:
        engine.post_op(operation, _joined(engine), attributes)

    message = str(caught.value)
    assert "right.customer" in message
    assert "customer" in message  # what the frame actually has
    # The real reason, said plainly rather than as a missing column.
    assert "anti-join" in message
    assert "cannot be expressed" in message


def test_an_unknown_post_combine_attribute_names_what_is_available(engine):
    # Not side-qualified: an ordinary typo still fails clearly, without the
    # anti-join explanation, which would be wrong here.
    with pytest.raises(ValueError) as caught:
        engine.post_op("sort", JAZZ, {"order_by": [{"attribute": "spend"}]})

    message = str(caught.value)
    assert "spend" in message and "customer" in message
    assert "anti-join" not in message


def test_a_known_post_combine_attribute_is_untouched(engine):
    # The check must not reject anything that worked before.
    kept = engine.post_op("filter", JAZZ, {"filters": [{"attribute": "customer", "operator": "=", "value": "Ann"}]})

    assert kept.rows() == [("Ann",)]
