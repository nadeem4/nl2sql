from types import SimpleNamespace

from nl2sql.pipeline.nodes.refiner.node import RefinerNode
from nl2sql.pipeline.state import SubgraphExecutionState
from nl2sql.common.errors import ErrorCode


def test_refiner_requires_llm():
    # Validates configuration guard because refiner must fail if LLM is missing.
    # Arrange
    ctx = SimpleNamespace(llm_registry=SimpleNamespace(get_llm=lambda _name: None))
    node = RefinerNode(ctx)
    state = SubgraphExecutionState(trace_id="t")

    # Act
    result = node(state)

    # Assert
    assert result["errors"][0].error_code == ErrorCode.MISSING_LLM


def test_refiner_sends_the_failed_plan_as_compact_json():
    # The plan used to reach the prompt as a pydantic repr: json.dumps() always
    # raised on a PlanModel and a bare except fell back to str(plan).
    import json

    from nl2sql.pipeline.nodes.ast_planner.schemas import ASTPlannerResponse, Expr, PlanModel, SelectItem, TableRef

    plan = PlanModel(
        tables=[TableRef(name="Customers", alias="t1")],
        select_items=[SelectItem(expr=Expr(kind="column", alias="t1", column_name="CustomerId"))],
        joins=[],
    )
    captured = {}

    class _Chain:
        def invoke(self, inputs):
            captured.update(inputs)
            return "Use the table Customer."

    ctx = SimpleNamespace(llm_registry=SimpleNamespace(get_llm=lambda _name: None))
    node = RefinerNode(ctx)
    node.chain = _Chain()

    result = node(SubgraphExecutionState(trace_id="t", ast_planner_response=ASTPlannerResponse(plan=plan)))

    assert result["refiner_response"].feedback == "Use the table Customer."
    sent = captured["failed_plan"]
    assert json.loads(sent)["tables"][0]["name"] == "Customers"
    assert json.loads(sent)["select_items"][0]["expr"]["column_name"] == "CustomerId"
    assert "\n" not in sent  # compact: every token of the refiner prompt is paid on each retry
    assert ":null" not in sent  # no null-valued keys
