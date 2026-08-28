import json
import logging

from nl2sql.common.logger import JsonFormatter, TraceContextFilter, trace_context, tenant_context


def test_json_formatter_includes_trace_and_tenant():
    # Validates trace propagation because observability requires correlation IDs.
    # Arrange
    formatter = JsonFormatter()
    record = logging.LogRecord("test", logging.INFO, "path", 1, "hello", {}, None)

    # Act
    with trace_context("trace-123"):
        with tenant_context("tenant-abc"):
            TraceContextFilter().filter(record)
            payload = json.loads(formatter.format(record))

    # Assert
    assert payload["message"] == "hello"
    assert payload["trace_id"] == "trace-123"
    assert payload["tenant_id"] == "tenant-abc"
