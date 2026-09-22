"""``nl2sql doctor`` must answer the first question a stuck new user has.

The PyPI install trial got as far as ``nl2sql setup --demo`` and then
ran a query, which died on a missing ``OPENAI_API_KEY``. ``doctor`` reported
Python, the adapters and connectivity -- everything except the one thing that
was actually wrong -- so the user had no way to find out from the tool which
variable to set, or which file to set it in.
"""

from __future__ import annotations

import pytest
from rich.console import Console
from typer.testing import CliRunner

from nl2sql.cli.demo.manager import DemoManager
from nl2sql.cli.main import app

runner = CliRunner()


@pytest.fixture()
def demo_configs_in_cwd(tmp_path, monkeypatch):
    """A throwaway project root holding a freshly generated Chinook demo.

    ``--env demo`` resolves every config path out of the generated
    ``.env.demo``, so the command has to run with that directory as the cwd,
    the way ``test_doctor_connectivity.py`` does.
    """
    monkeypatch.chdir(tmp_path)
    # Rich wraps to the terminal width; a narrow default would split the
    # strings these tests look for across lines.
    monkeypatch.setenv("COLUMNS", "200")
    DemoManager(Console(quiet=True), tmp_path).setup_demo()
    return tmp_path


def test_doctor_reports_missing_llm_key_and_names_the_env_file(
    demo_configs_in_cwd, monkeypatch
):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    result = runner.invoke(app, ["--env", "demo", "doctor"])

    assert result.exit_code == 0, result.output
    assert "OPENAI_API_KEY" in result.output
    assert ".env.demo" in result.output
    assert "MISSING" in result.output


def test_doctor_reports_a_key_present_without_printing_it(tmp_path, monkeypatch):
    """Settings hold the key as SecretStr; doctor still sees it and never prints it."""
    fake_key = "-".join(["sk", "proj", "doctorneverprint" + "d" * 24 + "8c2e"])
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.setenv("OPENAI_API_KEY", fake_key)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    DemoManager(Console(quiet=True), tmp_path).setup_demo(api_key=fake_key)

    result = runner.invoke(app, ["--env", "demo", "doctor"])

    assert result.exit_code == 0, result.output
    assert "key found" in result.output
    assert "MISSING: LLM" not in result.output
    assert fake_key not in result.output


def test_doctor_reports_llm_key_present(demo_configs_in_cwd, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    result = runner.invoke(app, ["--env", "demo", "doctor"])

    assert result.exit_code == 0, result.output
    assert "LLM" in result.output and "OK" in result.output
    assert "MISSING" not in result.output


def _use_anthropic(root):
    import yaml

    path = root / "configs" / "llm.demo.yaml"
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    cfg["default"].update(provider="anthropic", model="claude-opus-5", temperature=None,
                          api_key="${env:ANTHROPIC_API_KEY}")
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")


def test_doctor_checks_anthropic_api_key_for_a_claude_config(demo_configs_in_cwd, monkeypatch):
    _use_anthropic(demo_configs_in_cwd)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    result = runner.invoke(app, ["--env", "demo", "doctor"])

    assert result.exit_code == 0, result.output
    assert "ANTHROPIC_API_KEY" in result.output
    assert "MISSING" in result.output


def test_doctor_reports_a_missing_anthropic_extra(demo_configs_in_cwd, monkeypatch):
    import sys

    _use_anthropic(demo_configs_in_cwd)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "-".join(["not", "a", "real", "key"]))
    monkeypatch.setitem(sys.modules, "langchain_anthropic", None)

    result = runner.invoke(app, ["--env", "demo", "doctor"])

    assert result.exit_code == 0, result.output
    assert "nl2sql-engine[anthropic]" in " ".join(result.output.split())
