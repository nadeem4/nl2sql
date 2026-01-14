
import pytest
from unittest.mock import MagicMock, patch
from nl2sql.services.callbacks.token_handler import TokenHandler
from nl2sql.services.callbacks.node_handlers import NodeHandler
from nl2sql.common.metrics import TOKEN_LOG, LATENCY_LOG, reset_usage
from langchain_core.outputs import LLMResult

class TestMonitoring:
    
    @pytest.fixture(autouse=True)
    def setup_teardown(self):
        reset_usage()
        yield
        reset_usage()

    @patch("nl2sql.services.callbacks.token_handler.token_usage_counter")
    @patch("nl2sql.services.callbacks.token_handler.current_datasource_id")
    @patch("nl2sql.services.callbacks.token_handler.current_node_run_id")
    def test_token_handler_records_to_otel_and_legacy(self, mock_run_id, mock_ds_id, mock_counter):
        # Arrange
        handler = TokenHandler({})
        mock_run_id.get.return_value = "run-123"
        mock_ds_id.get.return_value = "ds-postgres"
        
        response = MagicMock(spec=LLMResult)
        response.llm_output = {"token_usage": {"total_tokens": 100, "prompt_tokens": 80, "completion_tokens": 20}}
        
        # Act
        handler.on_llm_end(response, agent_name="planner", model_name="gpt-4")
        
        # Assert - OTeL
        mock_counter.add.assert_called_once_with(
            100,
            attributes={
                "agent": "planner",
                "model": "gpt-4",
                "datasource_id": "ds-postgres",
                "type": "total"
            }
        )
        
        # Assert - Legacy
        assert len(TOKEN_LOG) == 1
        assert TOKEN_LOG[0]["total_tokens"] == 100
        assert TOKEN_LOG[0]["datasource_id"] == "ds-postgres"

    @patch("nl2sql.services.callbacks.node_handlers.node_duration_histogram")
    @patch("nl2sql.services.callbacks.node_handlers.current_datasource_id")
    def test_node_handler_records_to_otel_and_legacy(self, mock_ds_id, mock_histogram):
        # Arrange
        presenter = MagicMock()
        handler = NodeHandler(presenter)
        mock_ds_id.get.return_value = "ds-mysql"
        
        run_id = "run-456"
        handler.on_chain_start(run_id, None, "PlannerNode", {})
        
        # Act
        handler.on_chain_end(run_id)
        
        # Assert - OTeL
        # Note: on_chain_end calculates duration using time.perf_counter, so we just check it was called
        assert mock_histogram.record.call_count == 1
        args, kwargs = mock_histogram.record.call_args
        duration = args[0]
        attributes = kwargs["attributes"]
        
        assert isinstance(duration, float)
        assert attributes["node"] == "PlannerNode"
        assert attributes["datasource_id"] == "ds-mysql"
        
        # Assert - Legacy
        assert len(LATENCY_LOG) == 1
        assert LATENCY_LOG[0]["node"] == "PlannerNode"
