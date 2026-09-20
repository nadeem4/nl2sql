import pytest

from .conftest import run_cli
from .recordings_manufacturing import RULES_COUNT_EMPLOYEES


@pytest.mark.e2e
def test_count_employees_end_to_end(demo_project, fake_llm):
    server, env = fake_llm(RULES_COUNT_EMPLOYEES)
    r = run_cli(demo_project, env, "run", "--llm-config", "configs/llm.fake.yaml", "How many employees are there?")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "COUNT(" in r.stdout and "employees" in r.stdout
    assert "500" in r.stdout
    assert [c["name"] for c in server.calls] == ["DecomposerResponse", "PlanModel", "AggregatedResponse"]


@pytest.mark.e2e
def test_no_exec_prints_sql_and_never_executes(demo_project, fake_llm):
    server, env = fake_llm(RULES_COUNT_EMPLOYEES)
    r = run_cli(demo_project, env, "run", "--no-exec", "--llm-config", "configs/llm.fake.yaml", "How many employees are there?")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "COUNT(" in r.stdout
    assert "500" not in r.stdout
    assert "AggregatedResponse" not in [c["name"] for c in server.calls]
