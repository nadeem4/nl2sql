"""A demo question on the bus, with no key: no event carries rows (D15, docs/architecture/wave_a.md).

A capturing subscriber sees every event of a real run. None of them has a
``rows`` key at any depth, and the run's summary names what it read.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from .test_answer_preview_fake_llm import QUESTION, RULES  # noqa: E402
from .test_result_paging_fake_llm import _env  # noqa: E402


def _keys(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key
            yield from _keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _keys(child)


@pytest.mark.e2e
def test_no_bus_event_carries_rows(demo_project, fake_llm, monkeypatch):
    from nl2sql import NL2SQL, UserContext

    server, env = fake_llm(RULES)
    _env(monkeypatch, demo_project, env)
    with NL2SQL.from_config() as engine:
        seen = []
        engine._runtime.bus.subscribe(seen.append)
        result = engine.run_query(QUESTION, user_context=UserContext(user_id="ada", roles=["admin"]))

    assert result.status == "success", result.errors
    assert seen and seen[-1].name == "run.finished"
    for event in seen:
        assert "rows" not in set(_keys(event.model_dump(mode="json"))), event.name
    summary = seen[-1].data
    assert summary["tables"] and all(t.count(".") == 2 for t in summary["tables"]), summary["tables"]
    assert summary["datasources"] == sorted({sq.datasource_id for sq in result.sub_queries})
    assert summary["result_manifest"] == [r.model_dump(mode="json") for r in result.result_manifest]
