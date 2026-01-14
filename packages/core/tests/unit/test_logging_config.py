
import logging
import json
import pytest
from unittest.mock import patch, MagicMock
from nl2sql.common.settings import Settings
from nl2sql.common.logger import configure_logging, trace_context, get_logger

class TestStructuredLogging:
    
    @patch("nl2sql.common.settings.Settings")
    def test_json_logging_enabled_with_otlp(self, mock_settings_cls):
        # Arrange
        mock_settings = MagicMock()
        mock_settings.observability_exporter = "otlp"
        mock_settings_cls.return_value = mock_settings
        
        # Act
        # Re-import to trigger side-effect or manually call
        configure_logging(json_format=True)
        logger = get_logger("test_json")
        
        # Verify Handler
        root = logging.getLogger()
        handler = root.handlers[0]
        assert isinstance(handler.formatter, logging.Formatter)
        
        # Verify Output
        record = logging.LogRecord("test_json", logging.INFO, "path", 1, "test msg", {}, None)
        with trace_context("trace-123"):
            # Inject trace_id filter logic manually since we are unit testing formatter primarily
            handler.filter(record) 
            formatted_json = handler.formatter.format(record)
            
        data = json.loads(formatted_json)
        assert data["message"] == "test msg"
        assert data["trace_id"] == "trace-123"
        assert data["level"] == "INFO"

    def test_trace_id_injection(self):
        # Arrange
        configure_logging(json_format=True)
        logger = get_logger("test_trace")
        
        # Act & Assert
        # We can't easy capture stdout in unit test without capsys, but we checked formatter above.
        # Let's verify the integration of filter + formatter
        
        record = logging.LogRecord("test_trace", logging.INFO, "path", 1, "test msg", {}, None)
        with trace_context("trace-999"):
            # The filter must run to set the attribute
            f = logging.getLogger().handlers[0].filters[0]
            f.filter(record)
            
            # The formatter must use that attribute
            formatter = logging.getLogger().handlers[0].formatter
            output = formatter.format(record)
            
        assert '"trace_id": "trace-999"' in output
