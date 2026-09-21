from unittest.mock import MagicMock

from nl2sql.services.callbacks.node_handlers import NodeHandler
from nl2sql.common.metrics import LATENCY_LOG, reset_usage


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


def test_monitor_leaves_token_counting_to_the_usage_callback():
    """One reader of token usage: TokenUsageCallback, attached by run_with_graph.

    The monitor used to keep its own count (TokenHandler + TOKEN_LOG), read only
    from ``llm_output`` and never reset, so the CLI summed tokens across runs.
    """
    import nl2sql.services.callbacks.token_handler as token_handler
    from nl2sql.services.callbacks.monitor import PipelineMonitorCallback

    assert not hasattr(token_handler, "TokenHandler")
    assert not hasattr(PipelineMonitorCallback(MagicMock()), "tokens")
