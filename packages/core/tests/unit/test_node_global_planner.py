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
