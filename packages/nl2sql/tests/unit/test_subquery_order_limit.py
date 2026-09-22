"""A single sub-query's ranking, top-N and threshold reach its SQL.

The first gpt-5.4 tier 2 run (``benchmarks/tier2/chinook/2026-09-22_1a8101d_gpt-5.4.json``)
returned every row for ten "most" / "top N" / "more than" questions: chinook_003,
016, 017 (204 rows, gold 1), 026 (59 vs 5), 038 (1000 vs 5), 032, 029, 030, 005.
Their SQL had no plan LIMIT (only the row cap, ``LIMIT 1000``), and 003, 026
and 030 had no plan ORDER BY or HAVING either.

``SubQuery`` had no ``order_by`` or ``limit``, and the decomposer's only example
put them in ``post_combine_ops`` on a standalone group. The aggregator applied
those ops to the answer text, but the planner, which sees only the sub-query,
never did, so the SQL and the rows scored returned everything. Now:

* ``SubQuery`` carries ``order_by`` and ``limit``;
* filter/sort/limit ops on a group with one sub-query are folded into it;
* the planner gets the sub-query's metrics, filters, group_by, order_by and
  limit as its semantic context, and is told to apply them.
"""
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from nl2sql.pipeline.nodes.ast_planner.node import ASTPlannerNode
from nl2sql.pipeline.nodes.ast_planner.prompts import PLANNER_EXAMPLES, PLANNER_SYSTEM_PROMPT
from nl2sql.pipeline.nodes.ast_planner.schemas import PlanModel
from nl2sql.pipeline.nodes.datasource_resolver.schemas import DatasourceResolverResponse, ResolvedDatasource
from nl2sql.pipeline.nodes.decomposer.node import DecomposerNode
from nl2sql.pipeline.nodes.decomposer.prompts import DECOMPOSER_SYSTEM_PROMPT
from nl2sql.pipeline.nodes.decomposer.schemas import (
    CombineGroup, CombineInput, DecomposerResponse, ExpectedColumn, FilterSpec, GroupBySpec,
    MetricSpec, OrderBySpec, PostCombineOp, SubQuery,
)
from nl2sql.pipeline.plan_cache import PlanCache
from nl2sql.pipeline.state import GraphState, SubgraphExecutionState


def _sq(sq_id="sq_1", **kw):
    return SubQuery(
        id=sq_id, datasource_id="chinook", intent="number of albums per artist",
        metrics=[MetricSpec(name="album_count", aggregation="count")],
        group_by=[GroupBySpec(attribute="artist")],
        expected_schema=[ExpectedColumn(name="artist"), ExpectedColumn(name="album_count")],
        **kw,
    )


def _op(operation, **kw):
    return PostCombineOp(op_id=f"op_{operation}", target_group_id="cg_1", operation=operation, **kw)


def _decompose(response: DecomposerResponse) -> DecomposerResponse:
    node = DecomposerNode(SimpleNamespace(llm_registry=MagicMock()))
    node.chain = MagicMock()
    node.chain.invoke.return_value = response
    state = GraphState(
        user_query="Which artist has the most albums?",
        datasource_resolver_response=DatasourceResolverResponse(
            resolved_datasources=[ResolvedDatasource(datasource_id="chinook", metadata={})],
            allowed_datasource_ids=["chinook"],
        ),
    )
    return node(state)["decomposer_response"]


def _standalone(*ops, sub_queries=None, inputs=("sq_1",)):
    return DecomposerResponse(
        sub_queries=sub_queries or [_sq()],
        combine_groups=[CombineGroup(group_id="cg_1", operation="standalone",
                                     inputs=[CombineInput(subquery_id=i, role="base") for i in inputs])],
        post_combine_ops=list(ops),
    )


# --- the decomposer ---------------------------------------------------------------


def test_sort_and_limit_ops_on_a_standalone_sub_query_are_folded_into_it():
    # "Which artist has the most albums?", as the decomposer's example taught it.
    result = _decompose(_standalone(
        _op("sort", order_by=[OrderBySpec(attribute="album_count", direction="desc")]),
        _op("limit", limit=1),
    ))

    sq = result.sub_queries[0]
    assert sq.order_by == [OrderBySpec(attribute="album_count", direction="desc")]
    assert sq.limit == 1
    assert result.post_combine_ops == []


def test_a_filter_op_carrying_order_and_limit_is_folded_whole():
    # The decomposer prompt's own example: one "filter" op with order_by and limit.
    result = _decompose(_standalone(_op(
        "filter",
        filters=[FilterSpec(attribute="total_spent", operator=">", value=45)],
        order_by=[OrderBySpec(attribute="total_spent", direction="desc")],
        limit=10,
    )))

    sq = result.sub_queries[0]
    assert sq.filters == [FilterSpec(attribute="total_spent", operator=">", value=45)]
    assert sq.order_by[0].attribute == "total_spent" and sq.limit == 10
    assert result.post_combine_ops == []


def test_the_sub_querys_own_order_and_limit_are_kept():
    result = _decompose(_standalone(sub_queries=[_sq(order_by=[OrderBySpec(attribute="album_count",
                                                                            direction="desc")], limit=1)]))

    assert result.sub_queries[0].limit == 1
    assert result.sub_queries[0].order_by[0].direction == "desc"


def test_the_stable_id_depends_on_order_and_limit():
    top1 = _decompose(_standalone(sub_queries=[_sq(limit=1)])).sub_queries[0].id
    top5 = _decompose(_standalone(sub_queries=[_sq(limit=5)])).sub_queries[0].id

    assert top1 != top5


def test_ops_after_a_real_combine_stay_post_combine():
    two = _standalone(
        _op("limit", limit=3),
        sub_queries=[_sq("sq_1"), _sq("sq_2")],
        inputs=("sq_1", "sq_2"),
    )
    two.combine_groups[0] = CombineGroup(
        group_id="cg_1", operation="union",
        inputs=[CombineInput(subquery_id="sq_1", role="left"), CombineInput(subquery_id="sq_2", role="right")],
    )

    result = _decompose(two)

    assert len(result.post_combine_ops) == 1
    assert all(sq.limit is None for sq in result.sub_queries)


def test_an_aggregate_op_is_not_folded():
    result = _decompose(_standalone(
        _op("aggregate", metrics=[MetricSpec(name="album_count", aggregation="sum")]),
        _op("limit", limit=1),
    ))

    # A re-aggregation cannot become the sub-query's SQL; leave the group alone.
    assert len(result.post_combine_ops) == 2
    assert result.sub_queries[0].limit is None


def test_the_decomposer_prompt_puts_order_and_limit_on_the_sub_query():
    prompt = DECOMPOSER_SYSTEM_PROMPT
    example = json.loads(prompt[prompt.index("{{\n  \"sub_queries\""):prompt.index("VALIDATION:")]
                         .replace("{{", "{").replace("}}", "}"))

    sq = example["sub_queries"][0]
    assert sq["order_by"] and sq["limit"]
    assert example["post_combine_ops"] == []
    assert "most" in prompt and "top" in prompt


# --- the planner ----------------------------------------------------------------


def _plan_call(sub_query):
    node = ASTPlannerNode.__new__(ASTPlannerNode)
    node.node_name = "astplanner"
    node.plan_cache = PlanCache(None)
    node.chain = MagicMock()
    node.chain.invoke.return_value = PlanModel(tables=[])
    node(SubgraphExecutionState(trace_id="t", sub_query=sub_query))
    return node.chain.invoke.call_args.args[0]


def test_the_planner_is_given_the_sub_querys_order_and_limit():
    inputs = _plan_call(_sq(order_by=[OrderBySpec(attribute="album_count", direction="desc")], limit=1))

    context = json.loads(inputs["semantic_context"])
    assert context["order_by"] == [{"attribute": "album_count", "direction": "desc"}]
    assert context["limit"] == 1
    assert context["group_by"] == [{"attribute": "artist"}]


def test_the_planner_prompt_asks_for_order_by_and_limit():
    system = PLANNER_SYSTEM_PROMPT.lower()

    assert "limit" in system and "order_by" in system and "most" in system
    assert '"limit"' in PLANNER_EXAMPLES and '"order_by"' in PLANNER_EXAMPLES


# --- the plan cache ---------------------------------------------------------------


def test_the_plan_cache_key_tells_top_1_from_top_5():
    base = dict(schema_version="v1")
    key = PlanCache._key

    assert key(_sq(**base)) != key(_sq(limit=1, **base))
    assert key(_sq(limit=1, **base)) != key(_sq(limit=5, **base))
    assert key(_sq(**base))[0] == "number of albums per artist"  # old entries still hit
