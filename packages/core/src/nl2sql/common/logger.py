import logging
import json
import time
from typing import Any, Dict


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
        
        # Standard LogRecord attributes to ignore
        standard_attrs = {
            "args", "asctime", "created", "exc_info", "exc_text", "filename",
            "funcName", "levelname", "levelno", "lineno", "module",
            "msecs", "message", "msg", "name", "pathname", "process",
            "processName", "relativeCreated", "stack_info", "thread", "threadName",
            "taskName"
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
    
    if json_format:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
        
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
