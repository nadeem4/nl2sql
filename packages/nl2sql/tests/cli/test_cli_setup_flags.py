"""`nl2sql setup --demo` scaffolds the one demo dataset there is.

`--lite` and `--docker` chose between the manufacturing SQLite files and the
manufacturing Compose stack. Both datasets are gone, so both flags are gone:
the tests below pin that they are rejected rather than silently ignored.
"""

from __future__ import annotations

import re

from typer.testing import CliRunner

from nl2sql.cli.main import app

runner = CliRunner()

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _plain(output: str) -> str:
    """Strip ANSI styling from CLI output.

    Rich highlights option names in error text and splits the token while doing
    so -- ``--lite`` is emitted as ``-`` + reset + ``-lite``. A raw substring
    check therefore passes locally, where colour is off because stdout is not a
    terminal, and fails in CI, where colour is on.
    """
    return _ANSI.sub("", output)


def test_demo_flag_reaches_the_command(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "nl2sql.cli.main.setup_command", lambda **kwargs: captured.update(kwargs)
    )

    result = runner.invoke(app, ["setup", "--demo"])

    assert result.exit_code == 0, result.output
    assert captured == {"demo": True, "api_key": None}


def test_setup_without_demo_runs_the_wizard(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "nl2sql.cli.main.setup_command", lambda **kwargs: captured.update(kwargs)
    )

    result = runner.invoke(app, ["setup"])

    assert result.exit_code == 0, result.output
    assert captured["demo"] is False


def test_lite_is_no_longer_an_option(monkeypatch):
    monkeypatch.setattr("nl2sql.cli.main.setup_command", lambda **kwargs: None)

    result = runner.invoke(app, ["setup", "--demo", "--lite"])

    assert result.exit_code != 0
    assert "--lite" in _plain(result.output)


def test_docker_is_no_longer_an_option(monkeypatch):
    monkeypatch.setattr("nl2sql.cli.main.setup_command", lambda **kwargs: None)

    result = runner.invoke(app, ["setup", "--demo", "--docker"])

    assert result.exit_code != 0
    assert "--docker" in _plain(result.output)
