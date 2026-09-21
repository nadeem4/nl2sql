"""``nl2sql run`` exits 1 when the graph finishes with a blocking error.

The runner only reports ``success=False`` when the graph raises. A run whose
final state carries an ERROR or CRITICAL error printed the "Pipeline Errors"
table and exited 0. Warnings alone still exit 0.
"""

from __future__ import annotations

import pytest

from nl2sql.cli.commands import run as run_module
from nl2sql.cli.types import RunConfig
from nl2sql.common.errors import ErrorCode, ErrorSeverity, PipelineError
from nl2sql.pipeline.pipeline_runner import PipelineResult


def _runner_returning(errors):
    class _Runner:
        def __init__(self, ctx):
            self.ctx = ctx

        def run(self, **kwargs):
            return PipelineResult(success=True, final_state={"errors": errors})

    return _Runner


def _error(severity: ErrorSeverity) -> PipelineError:
    return PipelineError(
        node="decomposer",
        message="Decomposition failed: boom",
        severity=severity,
        error_code=ErrorCode.ORCHESTRATOR_CRASH,
    )


@pytest.fixture(autouse=True)
def wide_console(monkeypatch):
    monkeypatch.setenv("COLUMNS", "200")


@pytest.mark.parametrize("severity", [ErrorSeverity.ERROR, ErrorSeverity.CRITICAL])
def test_a_blocking_error_in_the_final_state_exits_1(monkeypatch, capsys, severity):
    monkeypatch.setattr(run_module, "PipelineRunner", _runner_returning([_error(severity)]))

    with pytest.raises(SystemExit) as exc:
        run_module.run_pipeline(RunConfig(query="How many customers are there?"), ctx=None)

    assert exc.value.code == 1
    assert "Pipeline Errors" in capsys.readouterr().out


def test_warnings_alone_exit_0(monkeypatch):
    monkeypatch.setattr(
        run_module, "PipelineRunner", _runner_returning([_error(ErrorSeverity.WARNING)])
    )

    run_module.run_pipeline(RunConfig(query="How many customers are there?"), ctx=None)
