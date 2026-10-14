"""Fixtures for the ``realdb`` tests: the test bed's servers, and an engine on one.

Every test here is marked ``realdb`` by ``tests/conftest.py``. Without a
server the fixture skips with the fix, or fails under ``REALDB_REQUIRED=1``;
see ``servers.py`` for the variables.
"""
from __future__ import annotations

import os
from typing import Any, Callable, Optional

import pytest

from nl2sql import NL2SQL
from nl2sql.testing.config import make_config
from nl2sql.testing.fake_llm import FakeLLMServer

from .servers import SERVERS, RealServer, datasource, require


@pytest.fixture(params=SERVERS)
def server(request) -> RealServer:
    return require(request.param, os.environ)


@pytest.fixture
def engine_on(server, tmp_path) -> Callable[..., NL2SQL]:
    """``engine_on(database=None, **fields)``: an engine on one database of ``server``, with a fake LLM."""
    fake = FakeLLMServer([]).start()
    engines = []

    def build(database: Optional[str] = None, **fields: Any) -> NL2SQL:
        source = datasource(server, database, **fields).model_dump(mode="json", exclude_defaults=True)
        config = make_config(tmp_path / f"engine{len(engines)}", datasources=[source],
                             llm={"provider": "openai", "model": "gpt-4o", "base_url": fake.base_url,
                                  "api_key": "sk-fake-realdb"})
        engine = NL2SQL(config)
        engines.append(engine)
        return engine

    yield build
    for engine in engines:
        engine.close()
    fake.stop()
