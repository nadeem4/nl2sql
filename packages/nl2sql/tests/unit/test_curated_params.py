"""The matcher's parameter checks: code, not the model, decides whether values are usable.

The model hands back every value as a string. Each is parsed as its declared
type and checked against ``min``/``max`` and ``choices``; a parameter the model
left out takes its default; a required one left out is ``param_missing``;
anything else wrong is ``param_invalid``.
"""
from __future__ import annotations

import pytest

from nl2sql.config.curated import VerifiedQuery
from nl2sql.pipeline.nodes.curated_matcher.params import ParamError, resolve_params
from nl2sql.pipeline.nodes.curated_matcher.schemas import ParamValue


def _query(**params):
    names = " ".join("{" + n + "}" for n in params)
    sql = "SELECT 1 FROM t WHERE " + " AND ".join(f"c = :{n}" for n in params) if params else "SELECT 1"
    return VerifiedQuery(id="q", description="d", questions=[f"question {names}"], params=params, sql=sql)


TOP = _query(n={"type": "int", "default": 10, "min": 1, "max": 100},
             year={"type": "int", "min": 2000, "max": 2100, "example": 2012})


def _values(**values):
    return [ParamValue(name=k, value=v) for k, v in values.items()]


def test_values_are_parsed_as_their_declared_types():
    assert resolve_params(TOP, _values(n="5", year="2012")) == {"n": 5, "year": 2012}


def test_a_left_out_param_takes_its_default():
    assert resolve_params(TOP, _values(year="2012")) == {"n": 10, "year": 2012}


def test_an_empty_value_counts_as_left_out():
    assert resolve_params(TOP, _values(n=" ", year="2012")) == {"n": 10, "year": 2012}


def test_a_required_param_left_out_is_missing():
    with pytest.raises(ParamError) as raised:
        resolve_params(TOP, _values(n="5"))
    assert raised.value.reason == "param_missing"
    assert "year" in str(raised.value)


def test_a_required_param_left_empty_is_missing():
    with pytest.raises(ParamError) as raised:
        resolve_params(TOP, _values(year=""))
    assert raised.value.reason == "param_missing"


@pytest.mark.parametrize("values", [
    {"n": "500", "year": "2012"},       # above max
    {"n": "0", "year": "2012"},         # below min
    {"n": "five", "year": "2012"},      # not an int
    {"n": "5.5", "year": "2012"},       # not an int either
    {"n": "5", "year": "2012", "x": "1"},  # an unknown name
])
def test_out_of_range_unparseable_or_unknown_values_are_invalid(values):
    with pytest.raises(ParamError) as raised:
        resolve_params(TOP, _values(**values))
    assert raised.value.reason == "param_invalid"


def test_the_same_name_twice_is_invalid():
    values = [ParamValue(name="n", value="5"), ParamValue(name="n", value="6"), ParamValue(name="year", value="2012")]
    with pytest.raises(ParamError) as raised:
        resolve_params(TOP, values)
    assert raised.value.reason == "param_invalid"


def test_floats_and_their_bounds():
    query = _query(rate={"type": "float", "min": 0, "max": 1})
    assert resolve_params(query, _values(rate="0.25")) == {"rate": 0.25}
    for bad in ("1.5", "nan", "inf", "x"):
        with pytest.raises(ParamError):
            resolve_params(query, _values(rate=bad))


def test_dates_are_iso_and_bound_as_iso_text():
    query = _query(since={"type": "date", "min": "2009-01-01", "max": "2013-12-31"})
    assert resolve_params(query, _values(since="2012-03-01")) == {"since": "2012-03-01"}
    for bad in ("2008-12-31", "2014-01-01", "March 2012", "2012-13-01"):
        with pytest.raises(ParamError) as raised:
            resolve_params(query, _values(since=bad))
        assert raised.value.reason == "param_invalid"


def test_strings_must_be_one_of_their_choices():
    query = _query(country={"type": "string", "choices": ["USA", "Canada"]})
    assert resolve_params(query, _values(country="USA")) == {"country": "USA"}
    with pytest.raises(ParamError) as raised:
        resolve_params(query, _values(country="Brazil"))
    assert raised.value.reason == "param_invalid"


def test_a_free_string_is_passed_as_it_is():
    """A hostile string is only ever a value: it is bound, never pasted into SQL."""
    query = _query(name={"type": "string"})
    assert resolve_params(query, _values(name="1; DROP TABLE x")) == {"name": "1; DROP TABLE x"}


def test_a_query_without_params_takes_none():
    assert resolve_params(_query(), []) == {}
