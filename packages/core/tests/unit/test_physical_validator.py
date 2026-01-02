import pytest
from unittest.mock import MagicMock
from nl2sql.pipeline.nodes.validator.physical_node import PhysicalValidatorNode
from nl2sql.pipeline.state import GraphState
from nl2sql.datasources import DatasourceRegistry
from nl2sql.common.errors import ErrorCode

def test_physical_validator_dry_run():
    registry = MagicMock(spec=DatasourceRegistry)
    adapter = MagicMock()
    adapter.capabilities.return_value.supports_dry_run = True
    
    # Mock invalid dry run
    mock_res = MagicMock()
    mock_res.is_valid = False
    mock_res.error_message = "Syntax Error near..."
    adapter.dry_run.return_value = mock_res
    
    registry.get_adapter.return_value = adapter
    
    node = PhysicalValidatorNode(registry)
    state = GraphState(
        user_query="q",
        sql_draft="SELECT * FROM users",
        selected_datasource_id="ds1",
        user_context={"allowed_tables": ["*"]}
    )
    result = node(state)
    
    assert result["errors"][0].error_code == ErrorCode.EXECUTION_ERROR
    assert "Dry Run Failed" in result["errors"][0].message

def test_physical_validator_perf_check():
    registry = MagicMock(spec=DatasourceRegistry)
    adapter = MagicMock()
    adapter.capabilities.return_value.supports_dry_run = False
    adapter.capabilities.return_value.supports_cost_estimation = True
    
    cost_mock = MagicMock()
    cost_mock.estimated_rows = 5000
    adapter.cost_estimate.return_value = cost_mock
    
    registry.get_adapter.return_value = adapter
    
    node = PhysicalValidatorNode(registry, row_limit=1000)
    state = GraphState(
        user_query="q",
        sql_draft="SELECT * FROM users",
        selected_datasource_id="ds1",
    )
    result = node(state)
    
    assert result["errors"][0].error_code == ErrorCode.PERFORMANCE_WARNING
    assert "exceeds limit 1000" in result["errors"][0].message
