"""The model-free Pagila retrieval baseline (wave_c.md, section 17.5, C-W5).

The Pagila suite in mode retrieval, on the test bed's Pagila indexed with local embeddings: no model and no
key, the table selector off, so the run is the floor (vector search, glossary tables, join-path fill). The
floor has no target; it is a recorded baseline. The run is gated against the newest record under
``benchmarks/retrieval/pagila/``: a case that passed there must still pass. The report is written to
``REALDB_EVAL_OUT`` when set, so CI can upload it and ``nl2sql eval publish`` can record it from the runner
(benchmarks/README.md, "The recording rule"). PostgreSQL only: that is where the test bed has Pagila.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from nl2sql import NL2SQL
from nl2sql.config import Datasource
from nl2sql.evaluation.records import run_stamp
from nl2sql.evaluation.suite import SUITES_DIR
from nl2sql.testing.config import make_config

BENCHMARKS = Path(__file__).resolve().parents[4] / "benchmarks"


def test_pagila_retrieval_baseline(server, tmp_path):
    if server.name != "postgres":
        pytest.skip("The Pagila suite is written for the PostgreSQL test bed.")
    source = Datasource(id="pagila", type="postgres", connection=server.connection("pagila"))
    config = make_config(tmp_path / "project", datasources=[source.model_dump(mode="json", exclude_defaults=True)])

    with NL2SQL(config) as engine:
        built = engine.index.build()
        assert built.ok, built.errors
        report = engine.evals.run(suites=[SUITES_DIR / "pagila.yaml"], mode="retrieval")
        out = os.environ.get("REALDB_EVAL_OUT")
        if out:
            Path(out).write_text(json.dumps({**report.model_dump(mode="json"), "run": run_stamp()}, indent=2,
                                            ensure_ascii=False, default=str) + "\n", encoding="utf-8")
        result = engine.evals.gate(report, BENCHMARKS)

    assert result.series_break is None, result.series_break
    assert result.ok, result.regressions
