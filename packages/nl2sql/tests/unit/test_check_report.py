"""``CheckReport.ready`` and ``CheckReport.problems``: what ``/ready`` and ``nl2sql doctor`` judge by (D208, G3b)."""
from __future__ import annotations

import pytest

from nl2sql import CheckReport, DatasourceStatus

READY = DatasourceStatus(id="shop", status="ready", required=True)
FAILED = DatasourceStatus(id="ledger", status="failed", required=True, stage="auth", reason="bad password: hunter2",
                          fix="Check the password.")
OPTIONAL_FAILED = DatasourceStatus(id="extra", status="failed", stage="network", reason="timed out")


def report(index="ok", datasources=(READY,), llm=None) -> CheckReport:
    return CheckReport(datasources=list(datasources), llm=llm or {}, index=index)


@pytest.mark.parametrize("index", ["ok", "stale"])
def test_ready_counts_stale_as_ready(index):
    assert report(index=index).ready(keys_optional=False)


@pytest.mark.parametrize("index", ["empty", "missing"])
def test_an_unbuilt_index_is_not_ready(index):
    assert not report(index=index).ready(keys_optional=True)


def test_ready_with_optional_keys():
    unkeyed = report(llm={"planner": "OPENAI_API_KEY is not set"})

    assert not unkeyed.ready(keys_optional=False)
    assert unkeyed.ready(keys_optional=True)


def test_a_failed_required_datasource_is_never_ready():
    assert not report(datasources=[READY, FAILED]).ready(keys_optional=True)


def test_a_failed_optional_datasource_leaves_it_ready_while_one_is_up():
    assert report(datasources=[READY, OPTIONAL_FAILED]).ready(keys_optional=False)


def test_no_datasource_up_is_not_ready():
    assert not report(datasources=[OPTIONAL_FAILED]).ready(keys_optional=False)
    assert not report(datasources=[]).ready(keys_optional=False)


def test_problems_name_each_failure_and_stage():
    problems = report(index="missing", datasources=[READY, FAILED],
                      llm={"planner": "OPENAI_API_KEY is not set"}).problems()

    assert any("ledger" in p and "auth" in p for p in problems)
    assert any("planner" in p for p in problems)
    assert any("index" in p.lower() for p in problems)
    assert not any("shop" in p for p in problems)


def test_problems_name_no_secret():
    problems = report(datasources=[FAILED], llm={"planner": "key sk-live-123 rejected"}).problems()

    assert problems and not any("hunter2" in p or "sk-live-123" in p for p in problems)


def test_a_ready_report_has_no_problems():
    assert report().problems() == []
