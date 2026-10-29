"""Wave C's CLI stubs (docs/architecture/wave_c.md, row C0b): ``keys``, ``launch`` and ``dashboards``.

Each is registered with its final options. A command whose row has landed is tested in its own file (``launch``: `test_launch_command.py`).
"""
from __future__ import annotations

import re

from typer.testing import CliRunner

from nl2sql.cli.main import app

runner = CliRunner()
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _out(result) -> str:
    return re.sub(r"\s+", " ", ANSI.sub("", result.output))


def test_the_new_commands_are_registered():
    for command in (["keys"], ["dashboards"], ["launch"]):
        result = runner.invoke(app, [*command, "--help"])
        assert result.exit_code == 0, result.output
    assert "launch" in _out(runner.invoke(app, ["--help"]))
