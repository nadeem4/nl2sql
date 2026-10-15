"""The routing suite and its gate (wave_a.md, section 9, "The routing gate").

``suites/routing.yaml`` holds approved cases over the demo's three databases,
single and cross-database. In mode retrieval (no key, no model, the local
embedder) each case passes when routing ranks one of its datasources first
and shortlists every one of them, and the gate allows no case to flip from
pass to fail against the newest routing record in ``benchmarks/``.

The baseline is recorded on CI's runner (benchmarks/README.md, "The recording
rule"), so a local run on another platform can differ on a near tie.
"""
from __future__ import annotations

import pathlib
import shutil

import pytest

from nl2sql import NL2SQL, load_config
from nl2sql.config import init_project
from nl2sql.datasets.demo import DEMO_DATASOURCES
from nl2sql.evaluation.suite import CHINOOK_SUITE_PATH, ROUTING_SUITE_PATH, load_suite
from nl2sql.testing.config import make_demo_config

REPO = pathlib.Path(__file__).resolve().parents[4]
DEMO_IDS = {source["id"] for source in DEMO_DATASOURCES}


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    """``nl2sql init --demo``, indexed locally: no key, no model."""
    root = tmp_path_factory.mktemp("routing_demo")
    init_project(root, demo=True)
    engine = NL2SQL(load_config(root / "nl2sql.yaml"))
    result = engine.index.build()
    assert result.ok, result.errors
    yield engine
    engine.close()


def test_routing_suite_cases_are_approved_and_regenerated(tmp_path):
    suite = load_suite(ROUTING_SUITE_PATH)
    cases = suite.cases
    assert cases and all(c.status == "approved" and c.origin == "hand" for c in cases)
    assert all(c.outcome == {"admin": "allowed"} for c in cases)
    # Routing alone decides each case's status: no case names tables for retrieval recall to score.
    assert not [c.id for c in cases if c.tables or c.columns]
    named = [d for c in cases for d in c.datasources]
    assert set(named) <= DEMO_IDS
    # Every demo database, not only Chinook, and questions that need two of them.
    for ds in ("support", "webanalytics"):
        assert sum(c.datasources == [ds] for c in cases) >= 10, ds
    assert sum(len(c.datasources) > 1 for c in cases) >= 3
    # Each case is tagged with every datasource it names, so --cases support finds them all.
    assert all(set(c.datasources) <= set(c.tags) for c in cases)
    # A case without reviewed SQL is a routing case, left out of execution accuracy.
    assert all((c.kind == "routing") == (c.sql is None) for c in cases)
    # The Chinook suite's answerable questions are routed here too, where no recall miss hides a routing one.
    gold = {c.question for c in load_suite(CHINOOK_SUITE_PATH).cases if c.tables}
    assert gold <= {c.question for c in cases}

    # The rows are the adapter's: regenerating a copy changes nothing.
    answered = [c for c in cases if c.sql]
    assert {d for c in answered for d in c.datasources} == {"support", "webanalytics"}
    copy = tmp_path / "routing.yaml"
    shutil.copyfile(ROUTING_SUITE_PATH, copy)
    with NL2SQL(make_demo_config(tmp_path / "project")) as engine:
        report = engine.evals.regenerate(copy)
    assert report.failed == [] and report.written == len(answered)
    assert copy.read_bytes() == ROUTING_SUITE_PATH.read_bytes().replace(b"\r\n", b"\n")


def test_routing_recall_does_not_regress(demo):
    report = demo.evals.run(suites=[ROUTING_SUITE_PATH], mode="retrieval")

    board = report.configs["retrieval"]
    assert board["routing"]["cases"] == len(load_suite(ROUTING_SUITE_PATH).cases)
    assert report.database["datasource_id"] == "chinook+support+webanalytics"
    result = demo.evals.gate(report, REPO / "benchmarks")
    assert result.ok, (f"{result.regressions or result.series_break}. A routing change that moves this on "
                       "purpose re-records the baseline from CI: benchmarks/README.md")
