
import logging
import pytest
from unittest.mock import MagicMock
from nl2sql.pipeline.state import GraphState, UserContext
from nl2sql.pipeline.graph import traced_node
from nl2sql.common.logger import JsonFormatter, _tenant_id_ctx, configure_logging

class TestContextPropagation:
    
    def test_user_context_serialization(self):
        """Verifies UserContext serialization in GraphState."""
        ctx = UserContext(user_id="u123", tenant_id="t456", roles=["admin"])
        state = GraphState(user_query="test", user_context=ctx)
        
        # Check access
        assert state.user_context.user_id == "u123"
        assert state.user_context.tenant_id == "t456"
        
        # Check dict dump
        dump = state.model_dump()
        assert dump["user_context"]["tenant_id"] == "t456"

    def test_traced_node_propagates_tenant_id(self):
        """Verifies traced_node wrapper injects tenant_id into context."""
        
        mock_func = MagicMock()
        
        def actual_func(state):
            # Capture current tenant_id from contextvar
            tid = _tenant_id_ctx.get()
            mock_func(tid)
            return {"result": "ok"}
        
        wrapped = traced_node(actual_func)
        
        # Test with Object State
        ctx = UserContext(tenant_id="tenant-A")
        state_obj = GraphState(user_query="test", user_context=ctx)
        wrapped(state_obj)
        mock_func.assert_called_with("tenant-A")
        
        # Test with Dict State (LangGraph internal use)
        state_dict = {"trace_id": "tr-1", "user_context": {"tenant_id": "tenant-B"}}
        wrapped(state_dict)
        mock_func.assert_called_with("tenant-B")

    def test_logger_includes_tenant_id(self):
        """Verifies JSON logs include tenant_id when set in context."""
        configure_logging(json_format=True)
        handler = logging.getLogger().handlers[0]
        formatter = handler.formatter
        
        record = logging.LogRecord("test", logging.INFO, "path", 1, "msg", {}, None)
        
        # Manually set the contextvar (simulating proper context manager usage)
        token = _tenant_id_ctx.set("tenant-XYZ")
        try:
            # Filter Logic injection
            record.tenant_id = _tenant_id_ctx.get()
            
            # Formatter logic
            json_str = formatter.format(record)
        finally:
            _tenant_id_ctx.reset(token)
            
        assert '"tenant_id": "tenant-XYZ"' in json_str
