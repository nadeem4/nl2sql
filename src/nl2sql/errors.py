from enum import Enum, auto
from dataclasses import dataclass
from typing import Optional, Any

class ErrorSeverity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"

class ErrorCode(str, Enum):
    MISSING_SQL = "MISSING_SQL"
    MISSING_DATASOURCE_ID = "MISSING_DATASOURCE_ID"
    MISSING_PLAN = "MISSING_PLAN"
    SCHEMA_RETRIEVAL_FAILED = "SCHEMA_RETRIEVAL_FAILED"
    SQL_GEN_FAILED = "SQL_GEN_FAILED"
    SECURITY_VIOLATION = "SECURITY_VIOLATION"
    SAFEGUARD_VIOLATION = "SAFEGUARD_VIOLATION"
    DB_EXECUTION_ERROR = "DB_EXECUTION_ERROR"
    EXECUTOR_CRASH = "EXECUTOR_CRASH"
    UNKNOWN_ERROR = "UNKNOWN_ERROR"

@dataclass
class PipelineError:
    node: str
    message: str
    severity: ErrorSeverity
    error_code: ErrorCode
    stack_trace: Optional[str] = None
    details: Optional[Any] = None
