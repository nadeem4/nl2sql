"""``nl2sql init [DIR] [--demo] [--provider NAME] [--force]`` (configuration v2, section 10)."""
from __future__ import annotations

from typer.testing import CliRunner

from nl2sql.cli.main import app
from nl2sql.config import load_config

runner = CliRunner()


def test_init_writes_a_config_that_loads(tmp_path):
    result = runner.invoke(app, ["init", str(tmp_path)])

    assert result.exit_code == 0, result.output
    config = load_config(tmp_path / "nl2sql.yaml")
    assert [d.id for d in config.datasources] == ["example"]


def test_init_prints_the_next_steps(tmp_path):
    result = runner.invoke(app, ["init", str(tmp_path), "--provider", "anthropic"])

    assert result.exit_code == 0, result.output
    assert "datasources/example.yaml" in result.output
    assert "ANTHROPIC_API_KEY" in result.output
    assert f"--config {(tmp_path / 'nl2sql.yaml').as_posix()} index" in result.output


def test_init_demo_writes_the_demo(tmp_path):
    result = runner.invoke(app, ["init", str(tmp_path), "--demo"])

    assert result.exit_code == 0, result.output
    config = load_config(tmp_path / "nl2sql.yaml")
    assert [d.id for d in config.datasources] == ["chinook", "support", "webanalytics"]


def test_init_refuses_to_overwrite_without_force(tmp_path):
    runner.invoke(app, ["init", str(tmp_path)])

    again = runner.invoke(app, ["init", str(tmp_path)])

    assert again.exit_code == 1
    assert "--force" in again.output
    assert "nl2sql.yaml" in again.output


def test_init_overwrites_with_force(tmp_path):
    runner.invoke(app, ["init", str(tmp_path)])

    again = runner.invoke(app, ["init", str(tmp_path), "--provider", "anthropic", "--force"])

    assert again.exit_code == 0, again.output
    assert load_config(tmp_path / "nl2sql.yaml").llm.provider == "anthropic"


def test_init_names_the_providers_when_one_is_unknown(tmp_path):
    result = runner.invoke(app, ["init", str(tmp_path), "--provider", "antropic"])

    assert result.exit_code == 1
    assert "openai" in result.output
