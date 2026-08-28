from unittest.mock import MagicMock

from langchain_core.outputs import LLMResult, Generation

from nl2sql.services.callbacks.token_handler import TokenHandler
from nl2sql.services.callbacks.node_handlers import NodeHandler
from nl2sql.common.metrics import TOKEN_LOG, LATENCY_LOG, reset_usage


def test_token_handler_records_usage():
    # Validates token metrics because LLM usage must be observable.
    # Arrange
    reset_usage()
    handler = TokenHandler(node_metrics={})
    response = LLMResult(
        generations=[[Generation(text="ok")]],
        llm_output={"token_usage": {"total_tokens": 5, "prompt_tokens": 2, "completion_tokens": 3}},
    )

    # Act
    handler.on_llm_end(response, agent_name="planner", model_name="gpt")

    # Assert
    assert TOKEN_LOG[-1]["total_tokens"] == 5
    assert TOKEN_LOG[-1]["agent"] == "planner"


def test_node_handler_records_latency():
    # Validates latency metrics because node performance must be tracked.
    # Arrange
    reset_usage()
    presenter = MagicMock()
    handler = NodeHandler(presenter)

    # Act
    run_id = "run-1"
    handler.on_chain_start(run_id, None, "PlannerNode", {})
    handler.on_chain_end(run_id)

    # Assert
    assert LATENCY_LOG[-1]["node"] == "PlannerNode"
