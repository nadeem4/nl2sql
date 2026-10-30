"""Wave A, R2: the sub-query limit (D29) and join-key coercion (D30, B30).

A join across datasources on keys of different types used to fail in the
aggregator, terminally, or silently match nothing (B30). Now the decomposer
declares each key's type, the aggregator coerces both sides to it in polars,
and the logical validator rejects only a column that cannot be coerced
(temporal against number or boolean). See docs/architecture/wave_a.md,
"Limits" and "The join-key type check". The model is ``FakeLLMServer``: no key,
no network.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import polars as pl
import pytest
from pydantic import ValidationError

from nl2sql.aggregation.engines.polars_duckdb import PolarsDuckdbEngine
from nl2sql.common.errors import ErrorCode, ErrorSeverity
from nl2sql.llm.wires import WIRES
from nl2sql.pipeline.nodes.ast_planner.schemas import ASTPlannerResponse, Expr, PlanModel, SelectItem, TableRef
from nl2sql.pipeline.nodes.datasource_resolver.schemas import DatasourceResolverResponse, ResolvedDatasource
from nl2sql.pipeline.nodes.decomposer.node import DecomposerNode
from nl2sql.pipeline.nodes.decomposer.schemas import DecomposerResponse, ExpectedColumn, SubQuery
from nl2sql.pipeline.nodes.schema_retriever.schema import Column, Table
from nl2sql.pipeline.nodes.validator.node import LogicalValidatorNode
from nl2sql.pipeline.state import GraphState, SubgraphExecutionState
from nl2sql.policies import UserContext
from nl2sql.testing.fake_llm import FakeLLMServer, Rule
from nl2sql.testing.runtime import fake_runtime, stub_ds_registry

QUESTION = "Which customers with an open support ticket spent the most on music?"


def _sub_query(sq_id, ds, columns):
    return {"id": sq_id, "datasource_id": ds, "intent": f"intent of {sq_id}", "metrics": [], "filters": [],
            "group_by": [{"attribute": "customer"}],
            "expected_schema": [{"name": name, **({"dtype": dtype} if dtype else {})} for name, dtype in columns]}


def _join(left_columns, right_columns, keys=(("customer_id", "customer_id"),)):
    return {
        "sub_queries": [_sub_query("sq_music", "chinook", left_columns),
                        _sub_query("sq_tickets", "support", right_columns)],
        "combine_groups": [{"group_id": "g1", "operation": "join",
                            "inputs": [{"subquery_id": "sq_music", "role": "left"},
                                       {"subquery_id": "sq_tickets", "role": "right"}],
                            "join_keys": [{"left": left, "right": right} for left, right in keys]}],
        "post_combine_ops": [{"op_id": "op1", "target_group_id": "g1", "operation": "project",
                             "expected_schema": [{"name": left_columns[0][0], "dtype": "int"}]}],
        "unmapped_subqueries": [],
    }


GOOD = _join([("customer_id", "int"), ("spend", "float")], [("customer_id", "string"), ("tickets", "int")])


def _decompose(rules, settings=None):
    server = FakeLLMServer(rules).start()
    try:
        llm = WIRES["openai"].build_client("gpt-5.4", 0.0, api_key="-".join(["fake", "decomposer", "key"]),
                                           base_url=server.base_url, tags=["decomposer"])
        ctx = fake_runtime(settings=settings, llm_registry=SimpleNamespace(get_llm=lambda _name: llm))
        state = GraphState(
            user_query=QUESTION,
            datasource_resolver_response=DatasourceResolverResponse(
                resolved_datasources=[ResolvedDatasource(datasource_id=ds, metadata={})
                                      for ds in ("chinook", "support")],
                allowed_datasource_ids=["chinook", "support"],
            ),
        )
        result = DecomposerNode(ctx)(state)
    finally:
        server.stop()
    return result, server


# --- D29: the sub-query limit -----------------------------------------------

def test_decomposition_over_the_limit_is_question_too_broad():
    payload = _join([("customer_id", "int")], [("customer_id", "int")])
    payload["sub_queries"].append(_sub_query("sq_extra", "chinook", [("n", "int")]))
    payload["combine_groups"].append({"group_id": "g2", "operation": "standalone",
                                      "inputs": [{"subquery_id": "sq_extra"}], "join_keys": []})

    result, server = _decompose([Rule("DecomposerResponse", payload)], settings={"routing": {"max_sub_queries": 2}})

    [error] = result["errors"]
    assert error.error_code == ErrorCode.QUESTION_TOO_BROAD
    assert error.message == "This question needs more than 2 separate queries. Ask about fewer things at once."
    assert not error.is_retryable
    # No DAG, so the layer router ends the run before any sub-query executes;
    # and the model is not asked again.
    assert "execution_dag" not in result
    assert len(server.calls) == 1


def test_the_limit_is_stated_in_the_prompt():
    _, server = _decompose([Rule("DecomposerResponse", GOOD)], settings={"routing": {"max_sub_queries": 4}})

    prompt = "\n".join(str(m.get("content") or "") for m in server.calls[0]["body"]["messages"])
    assert "at most 4 sub_queries" in prompt


# --- D30, part 1: the decomposer declares the type --------------------------

def test_join_key_without_dtype_is_retried_by_parse_retry():
    bad = _join([("customer_id", None)], [("customer_id", "int")])
    rules = [Rule("DecomposerResponse", GOOD, when="did not match the schema"), Rule("DecomposerResponse", bad)]

    result, server = _decompose(rules)

    assert not result.get("errors"), result.get("errors")
    assert len(server.calls) == 2
    retry = "\n".join(str(m.get("content") or "") for m in server.calls[1]["body"]["messages"])
    assert "customer_id" in retry and "dtype" in retry


def test_a_join_key_missing_from_its_sides_expected_schema_is_refused():
    with pytest.raises(ValidationError, match="right join key 'customer'"):
        DecomposerResponse.model_validate(_join([("customer_id", "int")], [("customer_id", "int")],
                                                keys=[("customer_id", "customer")]))


def test_join_key_dtypes_that_cannot_be_coerced_are_refused():
    with pytest.raises(ValidationError, match="cannot be joined"):
        DecomposerResponse.model_validate(_join([("customer_id", "int")], [("customer_id", "date")]))


def test_side_qualified_join_keys_resolve_to_the_expected_schema():
    DecomposerResponse.model_validate(_join([("customer_id", "int")], [("customer_id", "int")],
                                            keys=[("left.customer_id", "sq_tickets.customer_id")]))


def test_join_key_flag_is_not_in_the_tool_schema():
    _, server = _decompose([Rule("DecomposerResponse", GOOD)])

    tools = json.dumps(server.calls[0]["body"]["tools"])
    assert "expected_schema" in tools and "dtype" in tools
    assert "join_key" not in tools.replace("join_keys", "")
    assert "left_dtype" not in tools and "right_dtype" not in tools


def test_the_node_marks_the_join_keys_on_its_own_copy():
    # The model cannot set the flag: a column it marks is unmarked again.
    payload = _join([("customer_id", "int"), ("spend", "float")], [("customer_id", "string")])
    payload["sub_queries"][0]["expected_schema"][1]["join_key"] = True

    result, _ = _decompose([Rule("DecomposerResponse", payload)])

    response = result["decomposer_response"]
    flags = {(sq.datasource_id, c.name): c.join_key for sq in response.sub_queries for c in sq.expected_schema}
    assert flags == {("chinook", "customer_id"): True, ("chinook", "spend"): False,
                     ("support", "customer_id"): True}
    [pair] = response.combine_groups[0].join_keys
    assert (pair.left_dtype, pair.right_dtype) == ("int", "string")
    # The declared types reach the aggregator through the DAG.
    [combine] = [n for n in result["execution_dag"].nodes if n.kind == "combine"]
    assert combine.attributes["join_keys"][0]["right_dtype"] == "string"


# --- D30, part 2: the aggregator coerces -----------------------------------

def _engine():
    return PolarsDuckdbEngine.__new__(PolarsDuckdbEngine)


KEY = {"left": "customer_id", "right": "customer_id", "left_dtype": "int", "right_dtype": "string"}


def test_text_and_integer_keys_join_after_coercion():
    # B30: an integer key from one database, a text one from another.
    music = pl.LazyFrame({"customer_id": [45, 37, 7], "spend": [49.62, 43.62, 42.62]})
    tickets = pl.LazyFrame({"customer_id": ["45", " 7", "99"], "tickets": [2, 1, 3]})

    joined = _engine().combine("join", [("left", music), ("right", tickets)], [KEY]).collect()

    assert sorted(joined.select("customer_id", "tickets").rows()) == [(7, 1), (45, 2)]


def test_unconverted_key_values_are_a_warning():
    music = pl.LazyFrame({"customer_id": [45, 37]})
    tickets = pl.LazyFrame({"customer_id": ["45", "n/a", "x", None]})

    [warning] = _engine().unconverted_join_keys([("sq_music", music), ("sq_tickets", tickets)], [KEY])

    assert warning == {"sub_query_id": "sq_tickets", "column": "customer_id", "failed": 2, "total": 3,
                       "dtype": "int"}


def test_keys_of_one_type_produce_no_warning():
    frame = pl.LazyFrame({"customer_id": [1, 2]})
    key = {**KEY, "right_dtype": "int"}

    assert _engine().unconverted_join_keys([("a", frame), ("b", frame)], [key]) == []


def test_text_keys_join_dates_after_coercion():
    orders = pl.LazyFrame({"day": ["2024-01-02", "2024-01-03"], "orders": [3, 4]})
    visits = pl.LazyFrame({"day": pl.Series(["2024-01-02", "2024-01-05"]).str.to_date(), "visits": [10, 11]})
    key = {"left": "day", "right": "day", "left_dtype": "string", "right_dtype": "date"}

    joined = _engine().combine("join", [("left", orders), ("right", visits)], [key]).collect()

    assert joined.select("orders", "visits").rows() == [(3, 10)]


def test_number_keys_declared_boolean_coerce_zero_and_one():
    left = pl.LazyFrame({"flag": [0, 1, 2], "n": [5, 6, 7]})
    right = pl.LazyFrame({"flag": [True, False], "m": [8, 9]})
    key = {"left": "flag", "right": "flag", "left_dtype": "bool", "right_dtype": "bool"}

    joined = _engine().combine("join", [("left", left), ("right", right)], [key]).collect()

    assert sorted(joined.select("n", "m").rows()) == [(5, 9), (6, 8)]


def test_the_aggregator_node_reports_unconverted_values_with_the_datasource(tmp_path):
    from nl2sql.pipeline.nodes.aggregator.node import EngineAggregatorNode
    from nl2sql.pipeline.nodes.decomposer.dag import build_execution_dag

    response = DecomposerResponse.model_validate(GOOD)
    [group] = response.combine_groups
    response = response.model_copy(update={"combine_groups": [group.model_copy(update={"join_keys": [
        group.join_keys[0].model_copy(update={"left_dtype": "int", "right_dtype": "string"})]})]})
    ctx = fake_runtime(settings={"tenant_id": "t1"}, results=tmp_path)
    store = ctx.artifact_store
    frames = {"sq_music": pl.DataFrame({"customer_id": [45, 37], "spend": [1.0, 2.0]}),
              "sq_tickets": pl.DataFrame({"customer_id": ["45", "n/a", "37"], "tickets": [1, 2, 3]})}
    refs = {i: store.write_lazy(f.lazy(), store.run_metadata("t1", "t", i)) for i, f in frames.items()}
    out = EngineAggregatorNode(ctx)(GraphState(user_query="q", trace_id="t", decomposer_response=response,
                          execution_dag=build_execution_dag(response), artifact_refs=refs))

    assert not out.get("errors"), out.get("errors")
    [warning] = [w for w in out["warnings"] if w.code == ErrorCode.JOIN_KEY_UNCONVERTED.value]
    assert warning.message == "1 of 3 support.customer_id values are not whole numbers and joined nothing."
    assert warning.sub_query_id == "sq_tickets"
    assert warning.detail == {"column": "customer_id", "failed": 1, "total": 3, "dtype": "int"}


# --- D30, part 3: the validator rejects only what cannot be coerced ---------

def _validate(column_type, declared, *, join_key=True, expr=None):
    table = Table(name="Orders", columns=[Column(name="OrderKey", type=column_type)])
    plan = PlanModel(tables=[TableRef(name="Orders", alias="t1")],
                     select_items=[SelectItem(alias="order_key",
                                              expr=expr or Expr(kind="column", alias="t1", column_name="OrderKey"))])
    sub_query = SubQuery(id="sq1", datasource_id="ds1", intent="q",
                         expected_schema=[ExpectedColumn(name="order_key", dtype=declared, join_key=join_key)])
    adapter = SimpleNamespace(get_dialect=lambda: "postgres")
    ctx = fake_runtime(ds_registry=stub_ds_registry(adapter), policies={"allow_all": True})
    state = SubgraphExecutionState(trace_id="t", sub_query=sub_query, relevant_tables=[table],
                                   ast_planner_response=ASTPlannerResponse(plan=plan),
                                   user_context=UserContext(roles=["admin"]))
    return [e for e in LogicalValidatorNode(ctx)(state)["errors"]
            if e.error_code == ErrorCode.JOIN_KEY_TYPE_MISMATCH]


def test_validator_rejects_a_number_key_declared_temporal():
    [error] = _validate("integer", "date")

    assert error.severity == ErrorSeverity.ERROR and error.is_retryable
    assert error.message == ("The join key order_key is declared a date, but Orders.OrderKey is a number. "
                             "Select a column that holds a date for this key.")
    assert "CAST" not in error.message.upper()


@pytest.mark.parametrize("column_type, declared", [
    ("timestamp", "int"), ("date", "bool"), ("boolean", "datetime"),
])
def test_validator_rejects_the_other_uncoercible_pairs(column_type, declared):
    assert len(_validate(column_type, declared)) == 1


def test_validator_accepts_a_text_key_declared_numeric():
    assert _validate("character varying(20)", "int") == []


@pytest.mark.parametrize("column_type, declared", [
    ("integer", "string"), ("integer", "bool"), ("timestamp", "string"), ("text", "date"), ("boolean", "int"),
])
def test_validator_accepts_every_coercible_pair(column_type, declared):
    assert _validate(column_type, declared) == []


@pytest.mark.parametrize("column_type", ["geography", "a type nobody knows", None])
def test_unknown_type_is_not_checked(column_type):
    assert _validate(column_type, "date") == []


def test_a_column_that_is_not_a_join_key_is_not_checked():
    assert _validate("integer", "date", join_key=False) == []


def test_a_computed_join_key_is_not_checked():
    count = Expr(kind="func", func_name="COUNT", is_aggregate=True,
                 args=[Expr(kind="column", alias="t1", column_name="OrderKey")])
    assert _validate("integer", "date", expr=count) == []


# --- the review of #322: every coercion either joins or is counted ----------

import datetime as dt  # noqa: E402


def _key(left_dtype, right_dtype, name="k"):
    return {"left": name, "right": name, "left_dtype": left_dtype, "right_dtype": right_dtype}


def _join_rows(left, right, key, columns=("a", "b")):
    joined = _engine().combine("join", [("left", pl.LazyFrame(left)), ("right", pl.LazyFrame(right))], [key])
    return sorted(joined.collect().select(*columns).rows())


def _unconverted(left, right, key):
    return _engine().unconverted_join_keys([("l", pl.LazyFrame(left)), ("r", pl.LazyFrame(right))], [key])


UTC = dt.timezone.utc


@pytest.mark.parametrize("left, right", [
    # postgres timestamptz against a naive timestamp
    (pl.Series("k", [dt.datetime(2024, 1, 2, 1, tzinfo=UTC)]), pl.Series("k", [dt.datetime(2024, 1, 2, 1)])),
    # nanoseconds against microseconds
    (pl.Series("k", [dt.datetime(2024, 1, 2, 1)], dtype=pl.Datetime("ns")),
     pl.Series("k", [dt.datetime(2024, 1, 2, 1)], dtype=pl.Datetime("us"))),
    # milliseconds, zoned elsewhere
    (pl.Series("k", [dt.datetime(2024, 1, 2, 1, tzinfo=UTC)]).cast(pl.Datetime("ms", "Europe/Berlin")),
     pl.Series("k", [dt.datetime(2024, 1, 2, 1)])),
])
def test_datetime_keys_of_any_unit_and_zone_join(left, right):
    rows = _join_rows({"k": left, "a": [1]}, {"k": right, "b": [2]}, _key("datetime", "datetime"))

    assert rows == [(1, 2)]


def test_a_zoned_datetime_joins_a_date():
    left = {"k": pl.Series([dt.datetime(2024, 1, 2, tzinfo=UTC)]), "a": [1]}
    right = {"k": [dt.date(2024, 1, 2)], "b": [2]}

    assert _join_rows(left, right, _key("datetime", "date")) == [(1, 2)]


@pytest.mark.parametrize("text, expected", [
    ("2024-01-02T01:00:00+00:00", [(1, 2)]),
    ("2024-01-02T03:00:00+02:00", [(1, 2)]),
    ("2024-01-02T01:00:00Z", [(1, 2)]),
    ("2024-01-02 01:00:00", [(1, 2)]),
    ("2024-01-02T01:00:00.000", [(1, 2)]),
    ("2024-01-02 01:00", [(1, 2)]),
    ("not a date", []),
])
def test_text_parses_against_iso_formats_or_is_counted(text, expected):
    left = {"k": [text], "a": [1]}
    right = {"k": [dt.datetime(2024, 1, 2, 1)], "b": [2]}
    key = _key("string", "datetime")

    assert _join_rows(left, right, key) == expected
    assert len(_unconverted(left, right, key)) == (0 if expected else 1)


def test_text_that_is_not_an_iso_date_is_counted_not_a_failure():
    # Format inference used to fail on the first value and end the run.
    left = {"k": ["2024-01-02 13:00:00", "2024-01-03"], "a": [1, 2]}
    right = {"k": [dt.date(2024, 1, 3)], "b": [3]}
    key = _key("string", "date")

    assert _join_rows(left, right, key) == [(2, 3)]
    [found] = _unconverted(left, right, key)
    assert (found["failed"], found["total"]) == (1, 2)


def test_a_time_key_declared_datetime_is_counted_not_a_failure():
    left = {"k": [dt.time(1, 0)], "a": [1]}
    right = {"k": [dt.datetime(2024, 1, 2, 1)], "b": [2]}
    key = _key("datetime", "datetime")

    assert _join_rows(left, right, key) == []
    assert _unconverted(left, right, key)[0]["failed"] == 1


@pytest.mark.parametrize("values, dtype", [([7.9, 8.0], pl.Float64), ([7.5, 8.0], pl.Decimal(10, 2))])
def test_a_number_that_is_not_whole_is_counted_not_rounded(values, dtype):
    left = {"k": pl.Series(values, dtype=dtype), "a": [1, 2]}
    right = {"k": [7, 8], "b": [3, 4]}
    key = _key("float", "int")

    assert _join_rows(left, right, key) == [(2, 4)]
    [found] = _unconverted(left, right, key)
    assert (found["sub_query_id"], found["failed"], found["dtype"]) == ("l", 1, "int")


def test_large_ids_do_not_collide_when_an_int_meets_a_float():
    big = 2 ** 53
    left = {"k": [big, big + 1], "a": [1, 2]}
    right = {"k": [float(big)], "b": [3]}

    assert _join_rows(left, right, _key("int", "float")) == [(1, 3)]


def test_large_ids_declared_float_on_both_sides_join_as_integers():
    big = 2 ** 53
    left = {"k": [big, big + 1], "a": [1, 2]}
    right = {"k": [big + 1], "b": [3]}

    assert _join_rows(left, right, _key("float", "float")) == [(2, 3)]


def test_text_keys_are_stripped_on_both_sides():
    left = {"k": ["7", " 8 "], "a": [1, 2]}
    right = {"k": [" 7", "8"], "b": [3, 4]}

    assert _join_rows(left, right, _key("string", "string")) == [(1, 3), (2, 4)]


def test_zero_padded_text_matches_the_integer():
    # Documented: text parsed as a number loses its padding, so 007 and 7 are both 7.
    left = {"k": ["007", "7"], "a": [1, 2]}
    right = {"k": [7], "b": [3]}

    assert _join_rows(left, right, _key("string", "int")) == [(1, 3), (2, 3)]


@pytest.mark.parametrize("roles", [("left", "left"), ("left", "base"), ("right", "compare")])
def test_a_join_needs_one_left_and_one_right_input(roles):
    payload = _join([("cust_a", "int")], [("cust_b", "int")], keys=[("cust_a", "cust_b")])
    for inp, role in zip(payload["combine_groups"][0]["inputs"], roles):
        inp["role"] = role

    with pytest.raises(ValidationError, match="one left-hand input"):
        DecomposerResponse.model_validate(payload)


def test_a_join_of_three_inputs_is_refused():
    payload = _join([("customer_id", "int")], [("customer_id", "int")])
    payload["sub_queries"].append(_sub_query("sq_extra", "chinook", [("customer_id", "int")]))
    payload["combine_groups"][0]["inputs"].append({"subquery_id": "sq_extra", "role": "right"})

    with pytest.raises(ValidationError, match="one left-hand input"):
        DecomposerResponse.model_validate(payload)


def test_same_rank_roles_are_retried_not_a_crash():
    bad = _join([("cust_a", "int")], [("cust_b", "int")], keys=[("cust_a", "cust_b")])
    for inp in bad["combine_groups"][0]["inputs"]:
        inp["role"] = "left"
    good = _join([("cust_a", "int")], [("cust_b", "int")], keys=[("cust_a", "cust_b")])
    rules = [Rule("DecomposerResponse", good, when="did not match the schema"), Rule("DecomposerResponse", bad)]

    result, server = _decompose(rules)

    assert not result.get("errors"), result.get("errors")
    assert len(server.calls) == 2


def test_mark_join_keys_leaves_a_key_it_cannot_find():
    from nl2sql.pipeline.nodes.decomposer.node import mark_join_keys

    response = DecomposerResponse.model_validate(GOOD)
    [group] = response.combine_groups
    stray = group.model_copy(update={"join_keys": [group.join_keys[0].model_copy(update={"left": "nope"})]})

    _, [marked] = mark_join_keys(response.sub_queries, [stray])

    assert marked.join_keys[0].left_dtype is None


def test_the_join_key_flag_does_not_change_the_sub_query_id():
    plain = _join([("customer_id", "int")], [("customer_id", "int")])
    flagged = json.loads(json.dumps(plain))
    flagged["sub_queries"][0]["expected_schema"][0]["join_key"] = True

    ids = [sorted(sq.id for sq in _decompose([Rule("DecomposerResponse", p)])[0]["decomposer_response"].sub_queries)
           for p in (plain, flagged)]

    assert ids[0] == ids[1]


def test_the_planner_prompt_does_not_carry_the_join_key_flag():
    from unittest.mock import MagicMock

    from nl2sql.pipeline.nodes.ast_planner.node import ASTPlannerNode

    ctx = fake_runtime(llm_registry=MagicMock())
    node = ASTPlannerNode(ctx)
    node.chain = MagicMock()
    node.chain.invoke.return_value = None
    sub_query = SubQuery(id="sq1", datasource_id="ds1", intent="q",
                         expected_schema=[ExpectedColumn(name="customer_id", dtype="int", join_key=True)])

    node(SubgraphExecutionState(trace_id="t", sub_query=sub_query))

    [inputs] = node.chain.invoke.call_args.args
    assert inputs["expected_schema"] == [{"name": "customer_id", "dtype": "int"}]
