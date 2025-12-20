from enum import Enum
from dataclasses import dataclass, field
from typing import Optional, Any, Dict
import time

class ErrorSeverity(str, Enum):
    WARNING = "warning"   # Non-blocking, recovered (e.g. decomposition fallback)
    ERROR = "error"       # Blocking for a branch, but graph continues (e.g. 1/3 DBs failed)
    CRITICAL = "critical" # Pipeline crash (e.g. Config missing)

class ErrorCode(str, Enum):
    SECURITY_VIOLATION = "SECURITY_VIOLATION"
    PLANNING_FAILURE = "PLANNING_FAILURE"
    MISSING_PLAN = "MISSING_PLAN"
    INVALID_PLAN_STRUCTURE = "INVALID_PLAN_STRUCTURE"
    TABLE_NOT_FOUND = "TABLE_NOT_FOUND"
    COLUMN_NOT_FOUND = "COLUMN_NOT_FOUND"
    INVALID_ALIAS_USAGE = "INVALID_ALIAS_USAGE"
    MISSING_GROUP_BY = "MISSING_GROUP_BY"
    JOIN_TABLE_NOT_IN_PLAN = "JOIN_TABLE_NOT_IN_PLAN"
    JOIN_MISSING_ON_CLAUSE = "JOIN_MISSING_ON_CLAUSE"
    HAVING_MISSING_EXPRESSION = "HAVING_MISSING_EXPRESSION"
    INVALID_DATE_FORMAT = "INVALID_DATE_FORMAT"
    INVALID_NUMERIC_VALUE = "INVALID_NUMERIC_VALUE"
    VALIDATOR_CRASH = "VALIDATOR_CRASH"
    SQL_GEN_FAILED = "SQL_GEN_FAILED"
    AGGREGATOR_FAILED = "AGGREGATOR_FAILED"
    PLAN_FEEDBACK = "PLAN_FEEDBACK"
    SUMMARIZER_FAILED = "SUMMARIZER_FAILED"
    
    EXECUTION_FAILURE = "EXECUTION_FAILURE"
    MISSING_SQL = "MISSING_SQL"
    MISSING_DATASOURCE_ID = "MISSING_DATASOURCE_ID"
    EMPTY_DATASOURCE_SET = "EMPTY_DATASOURCE_SET"
    MULTIPLE_DATASOURCES_WARNING = "MULTIPLE_DATASOURCES_WARNING"
    DB_EXECUTION_ERROR = "DB_EXECUTION_ERROR"
    EXECUTOR_CRASH = "EXECUTOR_CRASH"
    
    SCHEMA_RETRIEVAL_FAILED = "SCHEMA_RETRIEVAL_FAILED"
    INTENT_EXTRACTION_FAILED = "INTENT_EXTRACTION_FAILED"
    ORCHESTRATOR_CRASH = "ORCHESTRATOR_CRASH"
    MISSING_LLM = "MISSING_LLM"
    UNKNOWN_ERROR = "UNKNOWN_ERROR"

@dataclass
class PipelineError:
    node: str
    message: str
    severity: ErrorSeverity
    error_code: str = ErrorCode.UNKNOWN_ERROR
    details: Optional[str] = None
    stack_trace: Optional[str] = None
    timestamp: float = field(default_factory=time.time)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "node": self.node,
            "message": self.message,
            "severity": self.severity.value,
            "error_code": self.error_code,
            "details": self.details,
            "timestamp": self.timestamp
        }
