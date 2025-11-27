import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.append(str(SRC))

from nl2sql.schemas import GraphState
from nl2sql.agents.validator import validator_node


def build_state(sql: str, plan=None):
    return GraphState(
        user_query="test",
        plan=plan or {},
        sql_draft={"sql": sql, "rationale": "", "limit_enforced": True, "draft_only": False},
        errors=[],
    )


def test_validator_allows_valid_limit():
    state = build_state("SELECT 1 LIMIT 5")
    state = validator_node(state, row_limit=10)
    assert "Missing LIMIT in SQL." not in state.errors
    assert "exceeds allowed" not in " ".join(state.errors)


def test_validator_rejects_missing_limit():
    state = build_state("SELECT 1")
    state = validator_node(state, row_limit=10)
    assert any("Missing LIMIT" in err for err in state.errors)


def test_validator_rejects_limit_exceeding_row_limit():
    state = build_state("SELECT 1 LIMIT 100")
    state = validator_node(state, row_limit=10)
    assert any("exceeds allowed" in err for err in state.errors)


def test_validator_enforces_order_by_when_plan_requests():
    plan = {"order_by": [{"expr": "id", "direction": "asc"}]}
    state = build_state("SELECT id FROM products LIMIT 5", plan=plan)
    state = validator_node(state, row_limit=10)
    assert any("ORDER BY" in err for err in state.errors)


def test_validator_blocks_union():
    state = build_state("SELECT 1 UNION SELECT 2 LIMIT 5")
    state = validator_node(state, row_limit=10)
    assert any("UNION detected" in err for err in state.errors)
