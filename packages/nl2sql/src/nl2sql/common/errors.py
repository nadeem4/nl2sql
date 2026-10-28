from enum import Enum, auto
from typing import Any, Dict, Optional
from pydantic import BaseModel, ConfigDict, Field


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
    # An empty result filtered on a value its column does not have.
    VALUE_NOT_FOUND = "VALUE_NOT_FOUND"
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
    # The model's structured answer still did not parse after the agent's parse_retries.
    MODEL_OUTPUT_INVALID = "MODEL_OUTPUT_INVALID"
    # Wave A (docs/architecture/wave_a.md). A hook denied the run or a part of it.
    HOOK_DENIED = "HOOK_DENIED"
    # A hook raised, returned something that is not a decision, or a decision its point does not allow.
    HOOK_FAILED = "HOOK_FAILED"
    # A hook asked for a person's approval; the result carries the requests.
    NEEDS_APPROVAL = "NEEDS_APPROVAL"
    # The question reached settings.pipeline.max_llm_calls or max_cost_usd.
    RUN_LIMIT_REACHED = "RUN_LIMIT_REACHED"
    # The decomposition needs more than settings.routing.max_sub_queries sub-queries.
    QUESTION_TOO_BROAD = "QUESTION_TOO_BROAD"
    # A join key's column cannot be coerced to its declared type; retried for another column.
    JOIN_KEY_TYPE_MISMATCH = "JOIN_KEY_TYPE_MISMATCH"
    # A warning: join key values that did not convert to the declared type and joined nothing.
    JOIN_KEY_UNCONVERTED = "JOIN_KEY_UNCONVERTED"
    # A join repeated the rows an aggregate counts; under COUNT retried once, else a warning.
    JOIN_FAN_OUT = "JOIN_FAN_OUT"
    # A warning: a result column whose every value is null.
    ALL_NULL_COLUMN = "ALL_NULL_COLUMN"
    # Wave B (docs/architecture/wave_b.md). The question can be read more than one way; the result
    # carries the options (INFO: nothing went wrong).
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
    # A ClarificationChoice that is not one of this run's requests, or names no option of it.
    CLARIFICATION_INVALID = "CLARIFICATION_INVALID"
    # The data or the glossary changed since the options were offered.
    CLARIFICATION_STALE = "CLARIFICATION_STALE"
    # The query is estimated to read more rows than the caller's cost rule allows, with over_limit: refuse.
    COST_LIMIT_EXCEEDED = "COST_LIMIT_EXCEEDED"
    # Wave C (docs/architecture/wave_c.md, section 11). The conversation agent's step did not validate after
    # its parse retries; the turn ends in error.
    MODEL_STEP_INVALID = "MODEL_STEP_INVALID"
    # A conversation already has settings conversations.max_turns turns.
    CONVERSATION_TOO_LONG = "CONVERSATION_TOO_LONG"
    # A chat tool raised or returned something that is not a ToolOutput.
    TOOL_FAILED = "TOOL_FAILED"
    # A chat tool raised ToolNotApplicable: it cannot apply to that turn (a warning).
    TOOL_NOT_APPLICABLE = "TOOL_NOT_APPLICABLE"
    # A tool name that is not enabled.
    UNKNOWN_CHAT_TOOL = "UNKNOWN_CHAT_TOOL"
    # A dashboard chart's stored plan no longer validates (the schema changed); fixing it is a chat turn.
    TILE_NEEDS_FIXING = "TILE_NEEDS_FIXING"



FATAL_ERRORS = {
    ErrorCode.SECURITY_VIOLATION,
    ErrorCode.SAFEGUARD_VIOLATION,
    ErrorCode.MISSING_DATASOURCE_ID,
    ErrorCode.MISSING_LLM,
    ErrorCode.INVALID_STATE,
    # The database refused or could not run the query (permission, timeout,
    # connection): a new plan would meet the same refusal. A query the
    # database rejected as wrong is EXECUTION_FAILED, which is retried.
    ErrorCode.DB_EXECUTION_ERROR,
    # A refinement retry would make the same call to the same provider.
    ErrorCode.PROVIDER_AUTH_FAILED,
    ErrorCode.PROVIDER_RATE_LIMITED,
    ErrorCode.PROVIDER_QUOTA_EXCEEDED,
    ErrorCode.PROVIDER_TIMEOUT,
    ErrorCode.PROVIDER_UNAVAILABLE,
    ErrorCode.PROVIDER_MODEL_UNAVAILABLE,
    # Each ends the run by design: a retry would meet the same hook, limit or question.
    ErrorCode.HOOK_DENIED,
    ErrorCode.HOOK_FAILED,
    ErrorCode.NEEDS_APPROVAL,
    ErrorCode.RUN_LIMIT_REACHED,
    ErrorCode.QUESTION_TOO_BROAD,
    # Wave B: a question asked back, a choice that is wrong or stale, and a refused cost are never
    # retried; planning the same question again rarely reads less (D54).
    ErrorCode.NEEDS_CLARIFICATION,
    ErrorCode.CLARIFICATION_INVALID,
    ErrorCode.CLARIFICATION_STALE,
    ErrorCode.COST_LIMIT_EXCEEDED,
    # Wave C: a step the model cannot make, a conversation that is full, and a stored plan that does not
    # validate are not helped by planning again.
    ErrorCode.MODEL_STEP_INVALID,
    ErrorCode.CONVERSATION_TOO_LONG,
    ErrorCode.TILE_NEEDS_FIXING,
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


class RunWarning(BaseModel):
    """Something a run's answer should say, without failing the run (``QueryResult.warnings``).

    Not ``Warning``, which is a Python builtin. ``code`` is an ``ErrorCode``
    value (``VALUE_NOT_FOUND``, ``JOIN_FAN_OUT``, ``ALL_NULL_COLUMN``,
    ``JOIN_KEY_UNCONVERTED``, ``RESULT_TRUNCATED``, ...), ``node`` the node that
    found it, ``message`` what the answer says, and ``sub_query_id`` the
    sub-query it is about, when it is about one. ``detail`` holds what a
    program may branch on; it never holds result rows or sample values.
    ``detail["recovered"]`` is True for an earlier attempt's error that a
    retry has since replaced: the trace of a retry, not something the answer
    says.
    """
    model_config = ConfigDict(extra="forbid")

    code: str
    node: str
    message: str
    sub_query_id: Optional[str] = None
    detail: Dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def of(cls, error: PipelineError, sub_query_id: Optional[str] = None) -> "RunWarning":
        """``error`` as a warning of a run that went on. Its ``details`` stay behind: they are not sent to callers."""
        recovered = error.severity in (ErrorSeverity.ERROR, ErrorSeverity.CRITICAL)
        return cls(code=getattr(error.error_code, "value", str(error.error_code)), node=error.node,
                   message=error.message, sub_query_id=sub_query_id,
                   detail={"recovered": True} if recovered else {})


# ---------------------------------------------------------------- Wave C facade exceptions
# (docs/architecture/wave_c.md, section 11). On nl2sql-api a LookupError is a 404 and a ValueError a 422.


class ConversationNotFound(LookupError):
    """No such conversation, or it is not the caller's: the two are the same (D105)."""


class ConversationBusy(LookupError):
    """The conversation is already running a turn (its state-store lock is held)."""


class DashboardNotFound(LookupError):
    """No such dashboard, or it is not the caller's."""


class WorkspaceNotFound(LookupError):
    """No such workspace, or it is not the caller's."""


class NotIndexed(ValueError):
    """A datasource has no live index build; the message names the command that builds it."""


class NoTiles(ValueError):
    """The starter dashboard has nothing to chart; the message names what is missing."""


class WorkspaceNotEmpty(ValueError):
    """A workspace that still holds dashboards cannot be deleted."""


class ScheduleTooFrequent(ValueError):
    """A schedule below dashboards.min_refresh_minutes."""


class SecretValueInConfig(ValueError):
    """A secret value would be written into YAML; secrets are written as references (D122)."""


class ConnectionInvalid(ValueError):
    """A datasource's ``connection`` does not validate against its adapter's connection model.

    ``str()`` is one line per problem; ``problems`` lists each as ``{"path", "message"}`` (``connection.port``),
    never a value, for a caller that shows them per field.
    """

    def __init__(self, problems: "list[dict[str, str]]"):
        self.problems = problems
        super().__init__(" ".join(f"{p['path']}: {p['message']}" for p in problems))
