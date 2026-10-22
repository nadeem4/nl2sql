"""The answer model sees a preview of the result, and a cut result says so, end to end.

"List every genre" returns Chinook's 25 genres. The answer synthesizer's prompt
carries the first ``ANSWER_PREVIEW_ROWS`` (20) of them plus the count, never all
25. Run again with ``RESULT_ROW_LIMIT=10``, the result reaches the ceiling: the
sub-query is flagged as truncated, and the answer says the result was cut at
10 rows and to narrow the question.
"""
from __future__ import annotations

import json
import re

import pytest

from nl2sql.testing.fake_llm import Rule

from .conftest import _base_env, run_cli
from .recordings_chinook import ANSWERABLE
from .test_trace_fake_llm import _serve

QUESTION = "List every genre."

DECOMPOSER = {
    "sub_queries": [{
        "id": "sq1", "datasource_id": "chinook", "intent": "Names of all genres",
        "metrics": [], "filters": [], "group_by": [],
        "expected_schema": [{"name": "genre_name", "dtype": "string"}],
    }],
    "combine_groups": [{"group_id": "g1", "operation": "standalone",
                        "inputs": [{"subquery_id": "sq1"}], "join_keys": []}],
    "post_combine_ops": [], "unmapped_subqueries": [],
}

PLAN = {
    "query_type": "READ", "distinct": False,
    "tables": [{"name": "Genre", "alias": "g"}], "joins": [],
    "select_items": [{"alias": "genre_name", "expr": {"kind": "column", "alias": "g", "column_name": "Name"}}],
    "where": None, "group_by": [],
    "order_by": [{"expr": {"kind": "column", "alias": "g", "column_name": "GenreId"}, "direction": "asc"}],
    "reasoning": "Every genre's name.",
}

ANSWER = {"summary": "Chinook has these genres; the full list is in the table.", "format_type": "text",
          "content": "The full list is in the table.", "warnings": []}

RULES = [
    ANSWERABLE,
    Rule("DecomposerResponse", DECOMPOSER),
    Rule("PlannerAnswer", PLAN),
    Rule("AggregatedResponse", ANSWER),
    Rule("plain", "Keep the same plan."),
]


def _run(demo_project, tmp_path, name, settings=None):
    server = _serve(demo_project, RULES, name, store=tmp_path, mode="always", settings=settings)
    env = _base_env()
    env["OPENAI_API_KEY"] = "sk-" + "fake-answer-preview"
    try:
        r = run_cli(demo_project, env, "run", QUESTION, config=name)
    finally:
        server.stop()
    assert r.returncode == 0, r.stdout + r.stderr
    [path] = list((tmp_path / "traces").glob("*.json"))
    [synthesis] = [c for c in server.calls if c["name"] == "AggregatedResponse"]
    prompt = "\n".join(str(m.get("content") or "") for m in synthesis["body"].get("messages", []))
    return json.loads(path.read_text(encoding="utf-8"))["result"], prompt


@pytest.mark.e2e
def test_the_answer_prompt_holds_twenty_rows_and_the_count(demo_project, tmp_path):
    result, prompt = _run(demo_project, tmp_path, "answer-preview.yaml")

    [sub] = result["sub_queries"]
    assert result["status"] == "success", result["errors"]
    assert sub["rows"]["total_rows"] == 25
    assert len(re.findall(r'"genre_name":', prompt)) == 20
    assert '"row_count": 25' in prompt and '"partial": true' in prompt
    # GenreId 1 is Rock, 25 is Opera: the first rows are sent, the last are not.
    assert '"Rock"' in prompt and '"Opera"' not in prompt
    assert sub["truncated"] is False and result["truncated"] is False
    assert result["final_answer"]["truncated"] is False


@pytest.mark.e2e
def test_a_result_at_the_ceiling_is_flagged_and_the_answer_says_so(demo_project, tmp_path):
    result, prompt = _run(demo_project, tmp_path, "answer-ceiling.yaml", settings={"results": {"row_limit": 10}})

    [sub] = result["sub_queries"]
    assert result["status"] == "success", result["errors"]
    assert "LIMIT 10" in sub["sql"]
    assert sub["rows"]["total_rows"] == 10
    assert sub["truncated"] is True and sub["row_cap"] == 10
    assert result["truncated"] is True
    assert "cut at 10 rows" in prompt
    final = result["final_answer"]
    assert final["truncated"] is True
    assert any("cut at 10 rows" in w and "narrow" in w.lower() for w in final["warnings"])
