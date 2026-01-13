import pytest
import time
from unittest.mock import MagicMock, patch
from nl2sql.pipeline.graph import run_with_graph
from nl2sql.common.settings import settings
from nl2sql.common.errors import ErrorCode

def test_global_timeout_success():
    """Verifies that pipeline completes if faster than timeout."""
    
    # Mock settings to have a long timeout
    with patch("nl2sql.common.settings.settings.global_timeout_sec", 10):
        # Mock graph.invoke to return immediately
        mock_graph = MagicMock()
        mock_graph.invoke.return_value = {"status": "success"}
        
        with patch("nl2sql.pipeline.graph.build_graph") as mock_build:
            mock_build.return_value = mock_graph
            
            result = run_with_graph(
                registry=MagicMock(),
                llm_registry=MagicMock(),
                user_query="fast query"
            )
            
            assert result == {"status": "success"}

def test_global_timeout_failure():
    """Verifies that pipeline raises TimeoutError and handles it."""
    
    # Set a very short timeout
    short_timeout = 1
    
    # Define a slow mock function
    def slow_node(*args, **kwargs):
        time.sleep(1.5) # Sleep longer than timeout
        return {"status": "too slow"}

    with patch("nl2sql.common.settings.settings.global_timeout_sec", short_timeout):
        mock_graph = MagicMock()
        mock_graph.invoke.side_effect = slow_node
        
        with patch("nl2sql.pipeline.graph.build_graph") as mock_build:
            mock_build.return_value = mock_graph
            
            result = run_with_graph(
                registry=MagicMock(),
                llm_registry=MagicMock(),
                user_query="slow query"
            )
            
            # Check for error structure
            assert "errors" in result
            assert len(result["errors"]) == 1
            error = result["errors"][0]
            assert error.error_code == ErrorCode.PIPELINE_TIMEOUT
            assert "timed out" in error.message
            assert "final_answer" in result # Graceful fallback
