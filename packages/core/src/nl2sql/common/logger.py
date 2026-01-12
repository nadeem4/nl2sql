import logging
import json
import time
import contextvars
from contextlib import contextmanager
from typing import Any, Dict, Optional

_trace_id_ctx = contextvars.ContextVar("trace_id", default=None)

class TraceContextFilter(logging.Filter):
    """Injects trace_id from contextvar into the log record."""
    def filter(self, record):
        record.trace_id = _trace_id_ctx.get()
        return True

@contextmanager
def trace_context(trace_id: str):
    """Context manager to set the trace_id for the current context."""
    token = _trace_id_ctx.set(trace_id)
    try:
        yield
    finally:
        _trace_id_ctx.reset(token)

class JsonFormatter(logging.Formatter):
    """Formatter that outputs JSON strings after parsing the LogRecord."""

    def format(self, record: logging.LogRecord) -> str:
        """Formats the log record as a JSON string.

        Args:
           record (logging.LogRecord): The log record to format.

        Returns:
            str: The JSON-formatted log string.
        """
        log_record = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "name": record.name,
            "message": record.getMessage(),
        }
        
        if getattr(record, "trace_id", None):
            log_record["trace_id"] = record.trace_id

        # Standard LogRecord attributes to ignore
        standard_attrs = {
            "args", "asctime", "created", "exc_info", "exc_text", "filename",
            "funcName", "levelname", "levelno", "lineno", "module",
            "msecs", "message", "msg", "name", "pathname", "process",
            "processName", "relativeCreated", "stack_info", "thread", "threadName",
            "taskName", "trace_id"
        }

        for key, value in record.__dict__.items():
            if key not in standard_attrs and not key.startswith("_"):
                log_record[key] = value
            
        return json.dumps(log_record)


def configure_logging(level: str = "INFO", json_format: bool = False):
    """Configures the root logger.

    Args:
        level (str): The logging level (default: INFO).
        json_format (bool): Whether to use JSON formatting (default: False).
    """
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    
    # Remove existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
        
    handler = logging.StreamHandler()
    handler.addFilter(TraceContextFilter())
    
    if json_format:
        handler.setFormatter(JsonFormatter())
    else:
        # Include trace_id in standard format if present
        # This is a bit tricky with dynamic formatting, usually easier to check record in formatter
        # For simplicity, we stick to standard format but maybe prepend trace_id if possible?
        # We'll stick to a standard format for text logs for now, trace_id mainly for JSON/Production
        formatter = logging.Formatter(
            "%(asctime)s - [%(trace_id)s] - %(name)s - %(levelname)s - %(message)s"
        )
        handler.setFormatter(formatter)
        
    root_logger.addHandler(handler)
    
    # Silence noisy libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Gets a named logger.

    Args:
        name (str): The name of the logger.

    Returns:
        logging.Logger: The logger instance.
    """
    return logging.getLogger(name)
