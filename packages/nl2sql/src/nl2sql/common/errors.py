from enum import Enum, auto
from typing import Optional, Any
from pydantic import BaseModel, ConfigDict


class ErrorSeverity(str, Enum):
    """Severity levels for pipeline errors."""
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class ErrorCode(str, Enum):
    """Standardized error codes for the pipeline."""
    MISSING_LLM = "MISSING_LLM"
    MISSING_SQL = "MISSING_SQL"
    MISSING_DATASOURCE_ID = "MISSING_DATASOURCE_ID"
    MISSING_PLAN = "MISSING_PLAN"
    INVALID_STATE = "INVALID_STATE"
    INVALID_PLAN_STRUCTURE = "INVALID_PLAN_STRUCTURE"
    UNSUPPORTED_FUNCTION = "UNSUPPORTED_FUNCTION"
    SCHEMA_RETRIEVAL_FAILED = "SCHEMA_RETRIEVAL_FAILED"
    QUESTION_NOT_ANSWERABLE = "QUESTION_NOT_ANSWERABLE"
    SQL_GEN_FAILED = "SQL_GEN_FAILED"
    TABLE_NOT_FOUND = "TABLE_NOT_FOUND"
    COLUMN_NOT_FOUND = "COLUMN_NOT_FOUND"
    JOIN_TABLE_NOT_IN_PLAN = "JOIN_TABLE_NOT_IN_PLAN"
    SECURITY_VIOLATION = "SECURITY_VIOLATION"
    SAFEGUARD_VIOLATION = "SAFEGUARD_VIOLATION"
    DB_EXECUTION_ERROR = "DB_EXECUTION_ERROR"
    EXECUTOR_CRASH = "EXECUTOR_CRASH"
    PLANNING_FAILURE = "PLANNING_FAILURE"
    PLANNER_FAILED = "PLANNER_FAILED"
    VALIDATOR_CRASH = "VALIDATOR_CRASH"
    REFINER_FAILED = "REFINER_FAILED"
    PLAN_FEEDBACK = "PLAN_FEEDBACK"
    UNKNOWN_ERROR = "UNKNOWN_ERROR"
    AGGREGATOR_FAILED = "AGGREGATOR_FAILED"
    # A combine input reached the adapter's row cap, so it may be missing rows.
    RESULT_TRUNCATED = "RESULT_TRUNCATED"
    ORCHESTRATOR_CRASH = "ORCHESTRATOR_CRASH"
    PIPELINE_TIMEOUT = "PIPELINE_TIMEOUT"
    CANCELLED = "CANCELLED"
    EXECUTION_FAILED = "EXECUTION_FAILED"
    # The model provider refused or failed a call (nl2sql.llm.failures).
    PROVIDER_AUTH_FAILED = "PROVIDER_AUTH_FAILED"
    PROVIDER_RATE_LIMITED = "PROVIDER_RATE_LIMITED"
    PROVIDER_QUOTA_EXCEEDED = "PROVIDER_QUOTA_EXCEEDED"
    PROVIDER_TIMEOUT = "PROVIDER_TIMEOUT"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    PROVIDER_MODEL_UNAVAILABLE = "PROVIDER_MODEL_UNAVAILABLE"



FATAL_ERRORS = {
    ErrorCode.SECURITY_VIOLATION,
    ErrorCode.SAFEGUARD_VIOLATION,
    ErrorCode.MISSING_DATASOURCE_ID,
    ErrorCode.MISSING_LLM,
    ErrorCode.INVALID_STATE,
    # A refinement retry would make the same call to the same provider.
    ErrorCode.PROVIDER_AUTH_FAILED,
    ErrorCode.PROVIDER_RATE_LIMITED,
    ErrorCode.PROVIDER_QUOTA_EXCEEDED,
    ErrorCode.PROVIDER_TIMEOUT,
    ErrorCode.PROVIDER_UNAVAILABLE,
    ErrorCode.PROVIDER_MODEL_UNAVAILABLE,
}

SAFE_ERROR_MESSAGES = {
    ErrorCode.DB_EXECUTION_ERROR: "An internal database error occurred while executing the query.",
    ErrorCode.SAFEGUARD_VIOLATION: "The query result was blocked by data protection safeguards.",
    ErrorCode.EXECUTOR_CRASH: "The query execution service encountered an unexpected error.",
    ErrorCode.VALIDATOR_CRASH: "The validation service encountered an unexpected error.",
    ErrorCode.MISSING_DATASOURCE_ID: "Datasource configuration error."
}

class PipelineError(BaseModel):
    """Represents a structured error within the pipeline.

    Attributes:
        node (str): The node where the error occurred.
        message (str): A human-readable error message.
        severity (ErrorSeverity): The severity of the error.
        error_code (ErrorCode): The standardized error code.
        stack_trace (Optional[str]): Stack trace if applicable.
        details (Optional[Any]): Additional context or metadata. Not sent to
            callers.
        provider (Optional[str]): The model provider a ``PROVIDER_*`` error
            came from, as a person would name it ("OpenAI").
        provider_response (Optional[str]): The provider's own words behind ``message``,
            with anything key-like redacted, for a "provider response"
            disclosure. Sent to callers.
    """
    model_config = ConfigDict(extra="ignore")
    
    node: str
    message: str
    severity: ErrorSeverity
    error_code: ErrorCode
    stack_trace: Optional[str] = None
    details: Optional[Any] = None
    provider: Optional[str] = None
    provider_response: Optional[str] = None

    @property
    def is_retryable(self) -> bool:
        """Determines if this error should trigger a retry/refinement loop."""
        if self.severity == ErrorSeverity.CRITICAL:
            return False
        return self.error_code not in FATAL_ERRORS

    def get_safe_message(self) -> str:
        """Returns a sanitized error message safe for exposure to LLMs or users.

        If a safe mapping exists for the error code, it is returned.
        Otherwise, the original message is used (assuming it's safe).

        Returns:
            str: The sanitized error message.
        """
        return SAFE_ERROR_MESSAGES.get(self.error_code, self.message)

