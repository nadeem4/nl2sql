from __future__ import annotations

from typer.testing import CliRunner

from nl2sql_cli.main import app

runner = CliRunner()


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
    assert "--lite" in result.output and "--docker" in result.output
