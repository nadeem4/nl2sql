import pytest

from nl2sql.pipeline.nodes.global_planner.node import GlobalPlannerNode
from nl2sql.pipeline.nodes.decomposer.schemas import (
    DecomposerResponse,
    SubQuery,
    CombineGroup,
    CombineInput,
    PostCombineOp,
    ExpectedColumn,
)
from nl2sql.pipeline.state import GraphState


def test_global_planner_builds_execution_dag():
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

    node = GlobalPlannerNode(ctx=None)
    state = GraphState(user_query="q", decomposer_response=response)

    # Act
    result = node(state)
    dag = result["global_planner_response"].execution_dag

    # Assert
    node_ids = {n.node_id for n in dag.nodes}
    assert "sq1" in node_ids
    assert "sq2" in node_ids
    assert "combine_g1" in node_ids
    assert any(n.kind.startswith("post_") for n in dag.nodes)
    assert dag.dag_id


def test_global_planner_dag_layers_and_edges():
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

    node = GlobalPlannerNode(ctx=None)
    state = GraphState(user_query="q", decomposer_response=response)

    result = node(state)
    dag = result["global_planner_response"].execution_dag

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

    scan_schema = node_index["sq_base"].output_schema
    assert [c.name for c in scan_schema.columns] == ["base_id"]


def test_global_planner_scan_only_dag():
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

    node = GlobalPlannerNode(ctx=None)
    state = GraphState(user_query="q", decomposer_response=response)
    result = node(state)
    dag = result["global_planner_response"].execution_dag

    assert len(dag.edges) == 0
    assert dag.layers
    for node_id in dag.layers[0]:
        node_obj = next(n for n in dag.nodes if n.node_id == node_id)
        assert node_obj.kind == "scan"


def test_global_planner_multiple_combine_groups():
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

    node = GlobalPlannerNode(ctx=None)
    state = GraphState(user_query="q", decomposer_response=response)
    result = node(state)
    dag = result["global_planner_response"].execution_dag

    node_ids = {n.node_id for n in dag.nodes}
    assert "combine_g1" in node_ids
    assert "combine_g2" in node_ids


def test_global_planner_unknown_post_combine_group_returns_error():
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


def test_global_planner_unknown_subquery_in_combine_returns_error():
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
