"""``--env X`` must put ``.env.X`` into the process environment.

pydantic-settings read the file into the settings object only. ``doctor``, the
LLM registry and ``${env:OPENAI_API_KEY}`` all read ``os.environ``, so a key
kept only in ``.env.demo`` was reported MISSING and never reached the
provider. A key exported in the shell must still win over the file.
"""

from __future__ import annotations

import os
import uuid

import pytest
from rich.console import Console
from typer.testing import CliRunner

from nl2sql.cli.demo.manager import DemoManager
from nl2sql.cli.main import app

runner = CliRunner()


def _fake_key(label: str) -> str:
    return "-".join(["sk", "proj", label + uuid.uuid4().hex])


@pytest.fixture()
def demo_with_key_in_file(tmp_path, monkeypatch):
    """A generated demo whose only copy of the key is in ``.env.demo``."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("COLUMNS", "200")
    for name in ("OPENAI_API_KEY", "OPENROUTER_API_KEY", "ENV", "ENV_FILE_PATH"):
        monkeypatch.delenv(name, raising=False)
    key = _fake_key("fileonly")
    DemoManager(Console(quiet=True), tmp_path).setup_demo(api_key=key)
    assert "OPENAI_API_KEY" not in os.environ
    return key


def test_doctor_finds_a_key_that_is_only_in_the_env_file(demo_with_key_in_file):
    result = runner.invoke(app, ["--env", "demo", "doctor"])

    assert result.exit_code == 0, result.output
    assert "key found" in result.output
    assert "MISSING: LLM" not in result.output
    assert demo_with_key_in_file not in result.output


def test_env_file_puts_the_key_into_the_environment(demo_with_key_in_file):
    result = runner.invoke(app, ["--env", "demo", "list-adapters"])

    assert result.exit_code == 0, result.output
    assert os.environ.get("OPENAI_API_KEY") == demo_with_key_in_file


def test_a_key_exported_in_the_shell_wins_over_the_env_file(
    demo_with_key_in_file, monkeypatch
):
    shell_key = _fake_key("shell")
    monkeypatch.setenv("OPENAI_API_KEY", shell_key)

    result = runner.invoke(app, ["--env", "demo", "list-adapters"])

    assert result.exit_code == 0, result.output
    assert os.environ["OPENAI_API_KEY"] == shell_key


def test_env_file_flag_also_loads_into_the_environment(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("NL2SQL_ENV_FILE_PROBE", raising=False)
    custom = tmp_path / "custom.env"
    custom.write_text("NL2SQL_ENV_FILE_PROBE=loaded\n", encoding="utf-8")

    result = runner.invoke(app, ["--env-file", str(custom), "list-adapters"])

    assert result.exit_code == 0, result.output
    assert os.environ.get("NL2SQL_ENV_FILE_PROBE") == "loaded"
