"""The execution DAG the decomposer builds from its own decomposition.

It was a graph node of its own until the DAG turned out to be a pure function
of ``DecomposerResponse``; these are that node's tests, against the function.
"""
import pytest

from nl2sql.pipeline.nodes.decomposer.dag import build_execution_dag
from nl2sql.pipeline.nodes.decomposer.schemas import (
    DecomposerResponse,
    SubQuery,
    CombineGroup,
    CombineInput,
    PostCombineOp,
    ExpectedColumn,
)


def test_the_dag_builds_execution_dag():
    # Validates DAG construction because aggregation depends on correct edges.
    # Arrange
    sub_queries = [
        SubQuery(
            id="sq1",
            intent="a",
            datasource_id="ds1",
            metrics=[],
            filters=[],
            group_by=[],
            expected_schema=[ExpectedColumn(name="id", dtype="int")],
        ),
        SubQuery(
            id="sq2",
            intent="b",
            datasource_id="ds1",
            metrics=[],
            filters=[],
            group_by=[],
            expected_schema=[ExpectedColumn(name="id", dtype="int")],
        ),
    ]
    combine_groups = [
        CombineGroup(
            group_id="g1",
            operation="union",
            inputs=[CombineInput(subquery_id="sq1", role="left"), CombineInput(subquery_id="sq2", role="right")],
        )
    ]
    post_ops = [
        PostCombineOp(
            op_id="op_stub",
            target_group_id="g1",
            operation="limit",
            limit=5,
            filters=[],
            metrics=[],
            group_by=[],
            order_by=[],
            expected_schema=[],
            metadata={},
        )
    ]
    response = DecomposerResponse(
        sub_queries=sub_queries,
        combine_groups=combine_groups,
        post_combine_ops=post_ops,
        unmapped_subqueries=[],
    )

    dag = build_execution_dag(response)

    # Assert
    node_ids = {n.node_id for n in dag.nodes}
    assert "sq1" in node_ids
    assert "sq2" in node_ids
    assert "combine_g1" in node_ids
    assert any(n.kind.startswith("post_") for n in dag.nodes)


def test_the_dag_dag_layers_and_edges():
    # Validates layer rules and acyclic DAG for deterministic planning.
    sub_queries = [
        SubQuery(
            id="sq_base",
            intent="base",
            datasource_id="ds1",
            metrics=[],
            filters=[],
            group_by=[],
            expected_schema=[ExpectedColumn(name="base_id", dtype="int")],
        ),
        SubQuery(
            id="sq_left",
            intent="left",
            datasource_id="ds1",
            metrics=[],
            filters=[],
            group_by=[],
            expected_schema=[ExpectedColumn(name="left_id", dtype="int")],
        ),
        SubQuery(
            id="sq_right",
            intent="right",
            datasource_id="ds1",
            metrics=[],
            filters=[],
            group_by=[],
            expected_schema=[ExpectedColumn(name="right_id", dtype="int")],
        ),
    ]
    combine_groups = [
        CombineGroup(
            group_id="g_join",
            operation="join",
            inputs=[
                CombineInput(subquery_id="sq_left", role="left"),
                CombineInput(subquery_id="sq_right", role="right"),
            ],
            join_keys=[{"left": "left_id", "right": "right_id"}],
        )
    ]
    post_ops = [
        PostCombineOp(
            op_id="op_stub",
            target_group_id="g_join",
            operation="limit",
            limit=10,
            filters=[],
            metrics=[],
            group_by=[],
            order_by=[],
            expected_schema=[ExpectedColumn(name="left_id", dtype="int")],
            metadata={},
        )
    ]
    response = DecomposerResponse(
        sub_queries=sub_queries,
        combine_groups=combine_groups,
        post_combine_ops=post_ops,
        unmapped_subqueries=[],
    )

    dag = build_execution_dag(response)

    node_index = {n.node_id: n for n in dag.nodes}
    assert set(node_index) == {"sq_base", "sq_left", "sq_right", "combine_g_join", "op_stub"}

    layer0 = dag.layers[0]
    for node_id in layer0:
        assert node_index[node_id].kind == "scan"

    edge_pairs = {(e.from_id, e.to_id) for e in dag.edges}
    assert ("sq_left", "combine_g_join") in edge_pairs
    assert ("sq_right", "combine_g_join") in edge_pairs
    assert ("combine_g_join", "op_stub") in edge_pairs

    for edge in dag.edges:
        assert edge.from_id in node_index
        assert edge.to_id in node_index

    # A scan node carries nothing of its own: its id is its sub-query's id,
    # so everything about it is one lookup away in the decomposer response.
    assert node_index["sq_base"].attributes == {}


def test_the_dag_scan_only_dag():
    # Validates scan-only plans because some queries skip combine/post stages.
    sub_queries = [
        SubQuery(
            id="sq1",
            intent="a",
            datasource_id="ds1",
            metrics=[],
            filters=[],
            group_by=[],
            expected_schema=[ExpectedColumn(name="id", dtype="int")],
        ),
        SubQuery(
            id="sq2",
            intent="b",
            datasource_id="ds2",
            metrics=[],
            filters=[],
            group_by=[],
            expected_schema=[ExpectedColumn(name="name", dtype="string")],
        ),
    ]
    response = DecomposerResponse(
        sub_queries=sub_queries,
        combine_groups=[],
        post_combine_ops=[],
        unmapped_subqueries=[],
    )

    dag = build_execution_dag(response)

    assert len(dag.edges) == 0
    assert dag.layers
    for node_id in dag.layers[0]:
        node_obj = next(n for n in dag.nodes if n.node_id == node_id)
        assert node_obj.kind == "scan"


def test_the_dag_multiple_combine_groups():
    # Validates multi-group plans because complex queries may have multiple combines.
    sub_queries = [
        SubQuery(
            id="sq1",
            intent="a",
            datasource_id="ds1",
            metrics=[],
            filters=[],
            group_by=[],
            expected_schema=[ExpectedColumn(name="id", dtype="int")],
        ),
        SubQuery(
            id="sq2",
            intent="b",
            datasource_id="ds1",
            metrics=[],
            filters=[],
            group_by=[],
            expected_schema=[ExpectedColumn(name="id", dtype="int")],
        ),
        SubQuery(
            id="sq3",
            intent="c",
            datasource_id="ds1",
            metrics=[],
            filters=[],
            group_by=[],
            expected_schema=[ExpectedColumn(name="id", dtype="int")],
        ),
    ]
    combine_groups = [
        CombineGroup(
            group_id="g1",
            operation="union",
            inputs=[
                CombineInput(subquery_id="sq1", role="left"),
                CombineInput(subquery_id="sq2", role="right"),
            ],
        ),
        CombineGroup(
            group_id="g2",
            operation="union",
            inputs=[
                CombineInput(subquery_id="sq2", role="left"),
                CombineInput(subquery_id="sq3", role="right"),
            ],
        ),
    ]
    response = DecomposerResponse(
        sub_queries=sub_queries,
        combine_groups=combine_groups,
        post_combine_ops=[],
        unmapped_subqueries=[],
    )

    dag = build_execution_dag(response)

    node_ids = {n.node_id for n in dag.nodes}
    assert "combine_g1" in node_ids
    assert "combine_g2" in node_ids


def test_the_dag_unknown_post_combine_group_returns_error():
    # Validates error handling when post-ops reference unknown groups.
    with pytest.raises(ValueError, match="PostCombineOp references unknown combine group"):
        DecomposerResponse(
            sub_queries=[
                SubQuery(
                    id="sq1",
                    intent="a",
                    datasource_id="ds1",
                    metrics=[],
                    filters=[],
                    group_by=[],
                    expected_schema=[ExpectedColumn(name="id", dtype="int")],
                )
            ],
            combine_groups=[],
            post_combine_ops=[
                PostCombineOp(
                    op_id="op_stub",
                    target_group_id="missing",
                    operation="limit",
                    limit=5,
                    filters=[],
                    metrics=[],
                    group_by=[],
                    order_by=[],
                    expected_schema=[],
                    metadata={},
                )
            ],
            unmapped_subqueries=[],
        )


def test_the_dag_unknown_subquery_in_combine_returns_error():
    # Validates error handling for edges pointing to unknown nodes.
    with pytest.raises(ValueError, match="CombineGroup references unknown subquery"):
        DecomposerResponse(
            sub_queries=[
                SubQuery(
                    id="sq1",
                    intent="a",
                    datasource_id="ds1",
                    metrics=[],
                    filters=[],
                    group_by=[],
                    expected_schema=[ExpectedColumn(name="id", dtype="int")],
                )
            ],
            combine_groups=[
                CombineGroup(
                    group_id="g1",
                    operation="union",
                    inputs=[CombineInput(subquery_id="missing", role="left")],
                )
            ],
            post_combine_ops=[],
            unmapped_subqueries=[],
        )


def test_a_dag_that_cannot_be_built_ends_the_run_with_its_error():
    """A DAG failure is reported, never raised through the graph.

    Two sub-queries the model wrote identically get the same content-addressed
    id, so the graph they describe has one node where the combine group expects
    two. That was the global planner's own error path; it is now the
    decomposer's, which keeps the decomposition, emits no ``execution_dag`` --
    the layer router ends a run without one -- and reports the cause.
    """
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from nl2sql.api.query_api import result_from_state
    from nl2sql.common.errors import ErrorCode
    from nl2sql.pipeline.nodes.datasource_resolver.schemas import (
        DatasourceResolverResponse,
        ResolvedDatasource,
    )
    from nl2sql.pipeline.nodes.decomposer.node import DecomposerNode
    from nl2sql.pipeline.state import GraphState

    twins = DecomposerResponse(
        sub_queries=[
            SubQuery(id="a", intent="customers per country", datasource_id="chinook"),
            SubQuery(id="b", intent="customers per country", datasource_id="chinook"),
        ],
        combine_groups=[CombineGroup(
            group_id="g1", operation="union",
            inputs=[CombineInput(subquery_id="a", role="left"),
                    CombineInput(subquery_id="b", role="right")],
        )],
    )

    node = DecomposerNode(SimpleNamespace(llm_registry=MagicMock()))
    node.chain = MagicMock()
    node.chain.invoke.return_value = twins

    result = node(GraphState(
        user_query="q",
        datasource_resolver_response=DatasourceResolverResponse(
            resolved_datasources=[ResolvedDatasource(datasource_id="chinook", schema_version="v1")],
            allowed_datasource_ids=["chinook"],
        ),
    ))

    [error] = result["errors"]
    assert error.error_code == ErrorCode.PLANNER_FAILED
    assert error.node == "decomposer"
    assert result.get("execution_dag") is None
    # The decomposition itself survives, so the failure names what was decomposed.
    assert result["decomposer_response"].sub_queries

    query_result = result_from_state({**result, "trace_id": "t"})
    assert query_result.status == "error"
    assert query_result.errors[0]["error_code"] == "PLANNER_FAILED"
