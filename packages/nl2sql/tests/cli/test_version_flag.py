"""``nl2sql --version`` exists, because bug reports need a version number.

Every other CLI has it, so a user filing an issue reaches for it first; before
this it produced a Typer "no such option" error.
"""

from __future__ import annotations

from importlib.metadata import version

from typer.testing import CliRunner

from nl2sql.cli.main import app

runner = CliRunner()


def test_version_flag_prints_the_installed_version():
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0, result.output
    assert version("nl2sql-engine") in result.output
