"""``engine.evals``: load, run, regenerate and scorers, configured by ``evals.yaml``.

A real engine over a small SQLite file and a fake embedder; the run itself is
replaced where it would ask a model (``test_eval_run.py`` covers the runner).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
import yaml
from langchain_core.embeddings import DeterministicFakeEmbedding

import nl2sql
from nl2sql import NL2SQL, Embeddings, EvalReport, EvalSuite
from nl2sql.config.models import DEFAULT_SCORERS
from nl2sql.evaluation import run as run_module
from nl2sql.testing.config import make_config

SHOP = {"id": "shop", "type": "sqlite", "description": "Customers", "connection": {"database": "shop.db"}}
CASE = {"id": "c1", "question": "How many customers?", "datasources": ["shop"], "status": "approved",
        "outcome": {"admin": "allowed"}, "tables": ["customers"], "sql": "SELECT COUNT(*) AS n FROM customers",
        "rows": [{"n": 2}]}


def _engine(tmp_path: Path, **sections) -> NL2SQL:
    with sqlite3.connect(tmp_path / "shop.db") as connection:
        connection.execute("CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT)")
        connection.execute("INSERT INTO customers VALUES (1, 'Ada'), (2, 'Grace')")
    cfg = make_config(tmp_path, datasources=[SHOP], **sections)
    return NL2SQL(cfg.model_copy(update={"embeddings": Embeddings(instance=DeterministicFakeEmbedding(size=8))}))


def _suite_file(path: Path, *cases) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"version": 1, "cases": list(cases or [CASE])}), encoding="utf-8")
    return path


def test_load_reads_the_suites_named_in_evals_yaml_relative_to_the_config(tmp_path):
    _suite_file(tmp_path / "evals" / "shop.yaml")
    with _engine(tmp_path, evals={"suites": ["evals/shop.yaml"]}) as engine:
        [suite] = engine.evals.load()
        assert isinstance(suite, EvalSuite) and suite.name == "shop" and [c.id for c in suite.cases] == ["c1"]
        # Or one file, wherever it is.
        other = _suite_file(tmp_path / "elsewhere.yaml", {**CASE, "id": "c2"})
        assert [c.id for s in engine.evals.load(other) for c in s.cases] == ["c2"]


def test_load_with_no_suite_configured_says_how_to_add_one(tmp_path):
    with _engine(tmp_path) as engine:
        with pytest.raises(ValueError, match="evals.yaml"):
            engine.evals.load()


def test_run_takes_its_defaults_from_evals_yaml(tmp_path, monkeypatch):
    _suite_file(tmp_path / "evals" / "shop.yaml")
    seen = {}

    def fake_run(ctx, suites, **kwargs):
        seen.update(kwargs, suites=[s.name for s in suites])
        return EvalReport(mode=kwargs["mode"])

    monkeypatch.setattr(run_module, "run_evals", fake_run)
    evals = {"suites": ["evals/shop.yaml"], "scorers": ["result_match"], "curated": False, "max_cost_usd": 2.5}
    with _engine(tmp_path, evals=evals) as engine:
        report = engine.evals.run()
        assert isinstance(report, EvalReport)
        assert seen["suites"] == ["shop"] and seen["mode"] == "full"
        assert (seen["scorers"], seen["curated"], seen["max_cost_usd"]) == (["result_match"], False, 2.5)
        assert seen["clarify"] is False  # Wave B turns clarification on; evals run with it off by default

        engine.evals.run(mode="plans", scorers=["outcome"], curated=True, max_cost_usd=1.0, clarify=True,
                         cases=["c1"], roles=["admin"], passes=2)
        assert (seen["mode"], seen["scorers"], seen["curated"], seen["max_cost_usd"]) == ("plans", ["outcome"],
                                                                                         True, 1.0)
        assert (seen["clarify"], seen["cases"], seen["roles"], seen["passes"]) == (True, ["c1"], ["admin"], 2)


def test_mode_retrieval_runs_on_the_engine_with_no_model(tmp_path):
    _suite_file(tmp_path / "evals" / "shop.yaml")
    with _engine(tmp_path, evals={"suites": ["evals/shop.yaml"]}) as engine:
        engine.index.build()
        report = engine.evals.run(mode="retrieval")
    [record] = report.configs["retrieval"]["results"]
    assert record["scores"]["retrieval_recall"]["detail"]["tables_sent"] >= 1
    assert record["scores"]["routing"]["passed"] is True
    assert report.database["datasource_id"] == "shop"


def test_scorers_lists_the_registered_names(tmp_path):
    with _engine(tmp_path) as engine:
        assert set(DEFAULT_SCORERS) <= set(engine.evals.scorers())


def test_regenerate_runs_the_cases_sql_through_the_engines_adapter(tmp_path):
    path = _suite_file(tmp_path / "evals" / "shop.yaml", {**CASE, "rows": []})
    with _engine(tmp_path) as engine:
        report = engine.evals.regenerate(path)
    assert report.written == 1 and report.failed == []
    assert yaml.safe_load(path.read_text(encoding="utf-8"))["cases"][0]["rows"] == [{"n": 2}]


def test_benchmark_is_gone():
    assert not hasattr(NL2SQL, "benchmark")
    for name in ("BenchmarkAPI", "BenchmarkConfig"):
        assert name not in nl2sql.__all__
        with pytest.raises(AttributeError):
            getattr(nl2sql, name)
