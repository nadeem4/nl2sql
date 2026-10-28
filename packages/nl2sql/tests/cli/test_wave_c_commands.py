"""Wave C's CLI stubs (docs/architecture/wave_c.md, row C0b): ``keys``, ``launch`` and ``dashboards``.

Each is registered with its final options and, until its row lands, says which row fills it and exits 1.
"""
from __future__ import annotations

import re

import pytest
from typer.testing import CliRunner

from nl2sql.cli.main import app

runner = CliRunner()
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _out(result) -> str:
    return re.sub(r"\s+", " ", ANSI.sub("", result.output))


@pytest.mark.parametrize("command, row", [
    (["launch", ".", "--provider", "openai", "--as", "admin", "--draft", "--yes", "--no-ports"], "L3"),
    (["dashboards", "refresh", "--due"], "D2"),
    (["dashboards", "refresh", "--dashboard", "abc", "--as", "admin"], "D2"),
])
def test_a_stub_command_names_its_row_and_exits_1(command, row):
    result = runner.invoke(app, command)

    assert result.exit_code == 1, result.output
    assert f"arrives with {row}" in _out(result)


def test_the_new_commands_are_registered():
    for command in (["keys"], ["dashboards"], ["launch"]):
        result = runner.invoke(app, [*command, "--help"])
        assert result.exit_code == 0, result.output
    assert "launch" in _out(runner.invoke(app, ["--help"]))
