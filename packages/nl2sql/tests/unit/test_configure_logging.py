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
