"""``configure_logging``: the level the caller passes, else ``NL2SQL_LOG_LEVEL``, else INFO."""
import logging

import pytest

from nl2sql import configure_logging


@pytest.fixture(autouse=True)
def _restore_the_root_logger():
    root = logging.getLogger()
    level, handlers = root.level, root.handlers[:]
    yield
    root.setLevel(level)
    root.handlers[:] = handlers


def test_the_level_defaults_to_info(monkeypatch):
    monkeypatch.delenv("NL2SQL_LOG_LEVEL", raising=False)
    configure_logging()
    assert logging.getLogger().level == logging.INFO


def test_nl2sql_log_level_sets_the_level_when_the_caller_passes_none(monkeypatch):
    monkeypatch.setenv("NL2SQL_LOG_LEVEL", "DEBUG")
    configure_logging()
    assert logging.getLogger().level == logging.DEBUG


def test_a_level_the_caller_passes_wins(monkeypatch):
    monkeypatch.setenv("NL2SQL_LOG_LEVEL", "DEBUG")
    configure_logging(level="WARNING")
    assert logging.getLogger().level == logging.WARNING


# ---------------------------------------------------------------- the format: --log-format, NL2SQL_LOG_FORMAT

def _formatter():
    from nl2sql.common.logger import JsonFormatter

    return isinstance(logging.getLogger().handlers[-1].formatter, JsonFormatter)


def test_the_format_defaults_to_text(monkeypatch):
    monkeypatch.delenv("NL2SQL_LOG_FORMAT", raising=False)
    configure_logging()
    assert not _formatter()


def test_nl2sql_log_format_json_chooses_json(monkeypatch):
    monkeypatch.setenv("NL2SQL_LOG_FORMAT", "json")
    configure_logging()
    assert _formatter()


def test_a_format_the_caller_passes_wins(monkeypatch):
    monkeypatch.setenv("NL2SQL_LOG_FORMAT", "json")
    configure_logging(json_format=False)
    assert not _formatter()


def test_an_unknown_format_is_refused(monkeypatch):
    monkeypatch.setenv("NL2SQL_LOG_FORMAT", "xml")
    with pytest.raises(ValueError, match="text or json"):
        configure_logging()


def test_the_cli_takes_log_format_for_any_command(monkeypatch, tmp_path):
    from typer.testing import CliRunner

    from nl2sql.cli.main import app

    monkeypatch.delenv("NL2SQL_LOG_FORMAT", raising=False)
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(app, ["--log-format", "json", "list-adapters"])
    assert result.exit_code == 0, result.output
    assert _formatter()
