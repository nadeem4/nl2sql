from __future__ import annotations

import re

from typer.testing import CliRunner

from nl2sql_cli.main import app

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


def test_lite_flag_is_accepted(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "nl2sql_cli.main.setup_command", lambda **kwargs: captured.update(kwargs)
    )

    result = runner.invoke(app, ["setup", "--demo", "--lite"])

    assert result.exit_code == 0, result.output
    assert captured["lite"] is True
    assert captured["docker"] is False


def test_lite_defaults_to_true_without_docker(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "nl2sql_cli.main.setup_command", lambda **kwargs: captured.update(kwargs)
    )

    result = runner.invoke(app, ["setup", "--demo"])

    assert result.exit_code == 0, result.output
    assert captured["lite"] is True


def test_docker_flag_disables_lite(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "nl2sql_cli.main.setup_command", lambda **kwargs: captured.update(kwargs)
    )

    result = runner.invoke(app, ["setup", "--demo", "--docker"])

    assert result.exit_code == 0, result.output
    assert captured["lite"] is False
    assert captured["docker"] is True


def test_lite_and_docker_together_is_an_error(monkeypatch):
    monkeypatch.setattr("nl2sql_cli.main.setup_command", lambda **kwargs: None)

    result = runner.invoke(app, ["setup", "--demo", "--lite", "--docker"])

    assert result.exit_code != 0
    output = _plain(result.output)
    assert "mutually exclusive" in output
    assert "--lite" in output and "--docker" in output
