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
    DemoManager(Console(quiet=True), tmp_path).setup_chinook()
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


def test_doctor_reports_llm_key_present(demo_configs_in_cwd, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    result = runner.invoke(app, ["--env", "demo", "doctor"])

    assert result.exit_code == 0, result.output
    assert "LLM" in result.output and "OK" in result.output
    assert "MISSING" not in result.output
