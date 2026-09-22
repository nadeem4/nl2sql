"""Config file options reach the context as paths, not strings.

``nl2sql run --policies-config <path>`` typed the option as ``str``. The
config loader calls ``.exists()`` on it, so the command crashed with
``AttributeError: 'str' object has no attribute 'exists'``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from nl2sql.cli import main as cli_main
from nl2sql.cli.main import app
from nl2sql.common.settings import settings

runner = CliRunner()
REPO_ROOT = Path(__file__).resolve().parents[4]


def _config_options(command, prefix: str = ""):
    """Yields (command path, option) for every option whose flag names a config file.

    Typer vendors its own click, so this duck-types rather than using
    ``isinstance`` against the ``click`` package.
    """
    if hasattr(command, "commands"):
        for name, sub in command.commands.items():
            yield from _config_options(sub, f"{prefix} {name}".strip())
        return
    for param in command.params:
        if param.param_type_name == "option" and any(
            opt == "--config" or opt.endswith("-config") for opt in param.opts
        ):
            yield prefix, param


CONFIG_OPTIONS = list(_config_options(typer.main.get_command(app)))


def test_every_command_exposes_some_config_options():
    commands = {command for command, _ in CONFIG_OPTIONS}
    assert {"run", "index", "benchmark", "trace replay"} <= commands


@pytest.mark.parametrize(
    "command,option",
    CONFIG_OPTIONS,
    ids=[f"{c}:{o.opts[0]}" for c, o in CONFIG_OPTIONS],
)
def test_config_options_are_typed_as_paths(command, option):
    assert option.type.name == "path", (
        f"`nl2sql {command} {option.opts[0]}` is typed {option.type!r}, not a path"
    )


def test_run_accepts_policies_config(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "schema_store_path", str(tmp_path / "schema_store.db"))
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    captured = {}
    monkeypatch.setattr(cli_main, "run_pipeline", lambda config, ctx: captured.update(ctx=ctx))
    secrets = tmp_path / "secrets.yaml"
    secrets.write_text("version: 1\nproviders: []\n", encoding="utf-8")
    configs = REPO_ROOT / "configs"

    result = runner.invoke(app, [
        "run", "How many customers are there?",
        "--config", str(configs / "datasources.demo.yaml"),
        "--llm-config", str(configs / "llm.demo.yaml"),
        "--secrets-config", str(secrets),
        "--policies-config", str(configs / "policies.demo.json"),
        "--vector-store", str(tmp_path / "vs"),
    ])

    assert result.exit_code == 0, repr(result.exception)
    assert "admin" in captured["ctx"].policies_cfg.roles
