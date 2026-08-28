from nl2sql.common.errors import PipelineError, ErrorCode, ErrorSeverity


def test_pipeline_error_retryability_contract():
    # Validates retry logic because fatal errors must not trigger retries.
    # Arrange
    fatal = PipelineError(
        node="test",
        message="missing datasource",
        severity=ErrorSeverity.ERROR,
        error_code=ErrorCode.MISSING_DATASOURCE_ID,
    )
    recoverable = PipelineError(
        node="test",
        message="planner failed",
        severity=ErrorSeverity.ERROR,
        error_code=ErrorCode.PLANNING_FAILURE,
    )

    # Act / Assert
    assert fatal.is_retryable is False
    assert recoverable.is_retryable is True
