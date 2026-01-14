
import os
import json
import logging
import pytest
import shutil
from unittest.mock import MagicMock, patch
from nl2sql.common.event_logger import EventLogger
from nl2sql.services.callbacks.monitor import PipelineMonitorCallback
from langchain_core.outputs import LLMResult, Generation

class TestAuditLogging:
    
    @pytest.fixture
    def audit_file(self):
        path = "logs/test_audit.log"
        yield path
        if os.path.exists(path):
            os.remove(path)
        if os.path.exists("logs"):
            try:
                os.rmdir("logs")
            except:
                pass

    def test_event_logger_writes_file(self, audit_file):
        """Verifies EventLogger writes correct JSON structure."""
        
        # Reset the global logger handlers to ensure we don't conflict with previous runs
        # or the default initialization at import time.
        logger = EventLogger() # usage global instance or creating new wrapper doesn't matter, underlying is 'nl2sql.audit'
        raw_logger = logging.getLogger("nl2sql.audit")
        for h in raw_logger.handlers[:]:
            raw_logger.removeHandler(h)
            h.close()
            
        # Re-initialize with patched path
        with patch("nl2sql.common.settings.settings.audit_log_path", audit_file):
            # We must recreate the logic of __init__ effectively since we stripped handlers
            # Use a fresh instance of the class which runs the __init__ logic again
            fresh_logger = EventLogger()
            
            fresh_logger.log_event("test_event", {"key": "secret_value"}, trace_id="tr-1", tenant_id="tn-1")
            
            # Close handlers to flush and release file lock for Windows
            for h in raw_logger.handlers:
                h.close()
        
        assert os.path.exists(audit_file)
        with open(audit_file, "r") as f:
            line = f.readline()
            data = json.loads(line)
            assert data["trace_id"] == "tr-1"
            assert data["tenant_id"] == "tn-1"
            assert data["event_type"] == "test_event"
            
    def test_pii_redaction(self):
        logger = EventLogger()
        payload = {"api_key": "12345", "nested": {"password": "secret"}}
        redacted = logger._redact(payload, {"api_key", "password"})
        assert redacted["api_key"] == "***REDACTED***"
        assert redacted["nested"]["password"] == "***REDACTED***"

    @patch("nl2sql.common.event_logger.event_logger")
    @patch("nl2sql.common.logger._trace_id_ctx")
    @patch("nl2sql.common.logger._tenant_id_ctx")
    def test_monitor_integration(self, mock_tenant, mock_trace, mock_event_logger):
        """Verifies monitor callback triggers audit log."""
        presenter = MagicMock()
        monitor = PipelineMonitorCallback(presenter)
        
        mock_trace.get.return_value = "tr-integration"
        mock_tenant.get.return_value = "tn-integration"
        
        # Sim LLM Result
        gen = Generation(text="SQL Query")
        res = LLMResult(generations=[[gen]], llm_output={"model_name": "gpt-4"})
        
        monitor.on_llm_end(res)
        
        mock_event_logger.log_event.assert_called_once()
        args, kwargs = mock_event_logger.log_event.call_args
        assert kwargs["trace_id"] == "tr-integration"
        assert kwargs["tenant_id"] == "tn-integration"
        assert kwargs["payload"]["model"] == "gpt-4"
