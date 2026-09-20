"""A failed run tells the user what went wrong, not how the code is laid out.

The first-run trial hit a missing API key and got a 40-line Python traceback
above the one line that mattered. The traceback is a debugging aid for the
maintainer, so it belongs behind ``--verbose``; the error itself is always
printed, and the command still exits 1.
"""

from __future__ import annotations

import pytest

from nl2sql.cli.commands import run as run_module
from nl2sql.cli.types import RunConfig
from nl2sql.pipeline.pipeline_runner import PipelineResult

ERROR = "LLM agent 'default' uses provider 'openai', which requires an API key"
TRACEBACK = (
    "Traceback (most recent call last):\n"
    '  File "registry.py", line 186, in _resolve_api_key\n'
    "ValueError: no key\n"
)


class _FailingRunner:
    """Stands in for PipelineRunner: always fails, with a traceback attached."""

    def __init__(self, ctx):
        self.ctx = ctx

    def run(self, **kwargs):
        return PipelineResult(success=False, error=ERROR, traceback=TRACEBACK)


@pytest.fixture(autouse=True)
def failing_pipeline(monkeypatch):
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.setattr(run_module, "PipelineRunner", _FailingRunner)


def _run(verbose: bool) -> None:
    config = RunConfig(query="How many employees are there?", verbose=verbose)
    with pytest.raises(SystemExit) as exc:
        run_module.run_pipeline(config, ctx=None)
    assert exc.value.code == 1


def test_pipeline_error_is_printed_without_a_traceback_by_default(capsys):
    _run(verbose=False)

    output = capsys.readouterr().out
    assert "requires an API key" in output
    assert "Traceback" not in output


def test_verbose_shows_the_traceback(capsys):
    _run(verbose=True)

    output = capsys.readouterr().out
    assert "requires an API key" in output
    assert "Traceback" in output
