"""Mode retrieval on the generated demo: no key, no LLM, the local embedder.

Report-only: recall is known to be imperfect, so this checks that the run
covers every answerable case and reports recall and routing, not what they are.
"""
import pytest

from nl2sql import NL2SQL, load_config
from nl2sql.config import Storage
from nl2sql.evaluation.suite import CHINOOK_SUITE_PATH, load_suite

from .conftest import private_storage


def _private(demo_project, tmp_path):
    """The demo's config on a private copy of its index and state store: the shared ``state.db`` raced."""
    config = load_config(demo_project / "nl2sql.yaml")
    return config.model_copy(update={"storage": Storage(path=private_storage(demo_project, tmp_path / "store"))})


@pytest.mark.e2e
def test_mode_retrieval_scores_every_answerable_case(demo_project, tmp_path):
    with NL2SQL(_private(demo_project, tmp_path)) as engine:
        report = engine.evals.run(mode="retrieval", roles=["admin"])

    board = report.configs["retrieval"]
    recall = {r["id"]: r["scores"]["retrieval_recall"] for r in board["results"]}
    answerable = {c.id for c in load_suite(CHINOOK_SUITE_PATH).cases if c.tables}
    assert {i for i, s in recall.items() if s["passed"] is not None} == answerable
    for score in recall.values():
        if score["passed"] is not None:
            assert 0.0 <= score["detail"]["table_recall"] <= 1.0
    # The search was forced: with 11 tables, not every case was sent the full schema.
    assert min(s["detail"]["tables_sent"] for s in recall.values() if s["passed"] is not None) < 11
    assert report.settings["retrieval"]["full_snapshot_max_tables"] == 0
    assert 0.0 <= board["retrieval"]["column_recall"] <= 1.0
    # Routing ranks the demo's three databases; Chinook is among the top for most cases.
    routing = board["scores"]["routing"]
    assert routing["applied"] == len(answerable) and routing["passed"] >= len(answerable) // 2
    # The run is identified by the suite's datasource, not every registered one.
    assert report.database["datasource_id"] == "chinook"
