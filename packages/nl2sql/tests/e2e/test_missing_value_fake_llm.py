"""An empty result on a value that does not exist, end to end.

"How many Jaz tracks are there?" planned as ``Genre.Name = 'Jaz'`` returns no
rows, and the answer used to report zero as fact. Now the executor probes the
literal, finds 'Jazz' is the closest stored value, and the planner, told so,
returns the plan that answers 130. A value with nothing close ("Polka") is not
retried: the answer carries a warning that there is no such genre.
"""
from __future__ import annotations

import json
import re

import pytest

from nl2sql.testing.fake_llm import Rule

from .conftest import _base_env, run_cli
from .recordings_chinook import ANSWERABLE
from .test_trace_fake_llm import _serve


def _col(alias, name):
    return {"kind": "column", "alias": alias, "column_name": name}


DECOMPOSER = {
    "sub_queries": [{
        "id": "sq1", "datasource_id": "chinook", "intent": "Number of tracks in the genre",
        "metrics": [{"name": "track_count", "aggregation": "count"}], "filters": [], "group_by": [],
        "expected_schema": [{"name": "track_count", "dtype": "int"}],
    }],
    "combine_groups": [{"group_id": "g1", "operation": "standalone",
                        "inputs": [{"subquery_id": "sq1"}], "join_keys": []}],
    "post_combine_ops": [], "unmapped_subqueries": [],
}


def _plan(genre):
    return {
        "query_type": "READ", "distinct": False,
        "tables": [{"name": "Track", "alias": "t1"}, {"name": "Genre", "alias": "t2"}],
        "joins": [{"left_alias": "t1", "right_alias": "t2", "join_type": "inner",
                   "condition": {"kind": "binary", "op": "=", "left": _col("t1", "GenreId"),
                                 "right": _col("t2", "GenreId")}}],
        "select_items": [{"alias": "track_count",
                          "expr": {"kind": "func", "func_name": "COUNT", "is_aggregate": True,
                                   "args": [_col("t1", "TrackId")]}}],
        "where": {"kind": "binary", "op": "=", "left": _col("t2", "Name"),
                  "right": {"kind": "literal", "value": genre}},
        "group_by": [{"expr": _col("t2", "Name")}], "order_by": [],
        "reasoning": f"Count the tracks of genre {genre}.",
    }


def _answer(prompt_text: str) -> dict:
    nums = re.findall(r"track_count[^0-9]*(\d+)", prompt_text)
    text = f"There are {nums[0]} tracks." if nums else "There are no such tracks."
    return {"summary": text, "format_type": "text", "content": text, "warnings": []}


def _run(demo_project, tmp_path, rules, name, question):
    server = _serve(demo_project, rules, name)
    env = _base_env()
    env.update({"OPENAI_API_KEY": "sk-" + "fake-missing-value", "TRACE_MODE": "always", "TRACE_DIR": str(tmp_path),
                "SQL_AGENT_RETRY_BASE_DELAY_SEC": "0", "SQL_AGENT_RETRY_JITTER_SEC": "0"})
    try:
        r = run_cli(demo_project, env, "run", "--llm-config", f"configs/{name}", question)
    finally:
        server.stop()
    assert r.returncode == 0, r.stdout + r.stderr
    [path] = list(tmp_path.glob("*.json"))
    return server, json.loads(path.read_text(encoding="utf-8"))["result"]


def _prompt(call) -> str:
    return "\n".join(str(m.get("content") or "") for m in call["body"].get("messages", []))


@pytest.mark.e2e
def test_a_misspelt_value_is_looked_up_and_the_retry_answers(demo_project, tmp_path):
    rules = [
        ANSWERABLE,
        Rule("DecomposerResponse", DECOMPOSER),
        Rule("PlanModel", _plan("Jazz"), when="closest existing values are 'Jazz'"),
        Rule("PlanModel", _plan("Jaz")),
        Rule("AggregatedResponse", _answer),
        Rule("plain", "The genre is stored as 'Jazz'."),
    ]
    server, result = _run(demo_project, tmp_path, rules, "llm.missing-value.yaml", "How many Jaz tracks are there?")

    [sub] = result["sub_queries"]
    assert result["status"] == "success", result["errors"]
    assert sub["retry_count"] == 1
    assert "'Jazz'" in sub["sql"]
    assert result["final_answer"]["summary"] == "There are 130 tracks."
    # The lookup the retry recovered from is not presented as a finding.
    assert result["final_answer"]["warnings"] == []
    planner_calls = [c for c in server.calls if c["name"] == "PlanModel"]
    assert len(planner_calls) == 2
    assert "No Genre.Name value is 'Jaz'" in _prompt(planner_calls[1])


@pytest.mark.e2e
def test_a_value_with_nothing_close_is_reported_as_missing(demo_project, tmp_path):
    rules = [
        ANSWERABLE,
        Rule("DecomposerResponse", DECOMPOSER),
        Rule("PlanModel", _plan("Polka")),
        Rule("AggregatedResponse", _answer),
        Rule("plain", "Keep the same plan."),
    ]
    server, result = _run(demo_project, tmp_path, rules, "llm.missing-value-none.yaml",
                          "How many Polka tracks are there?")

    [sub] = result["sub_queries"]
    assert result["status"] == "success", result["errors"]
    assert sub["retry_count"] == 0
    assert result["final_answer"]["warnings"] == ["There is no Genre with Name 'Polka'."]
    [synthesis] = [c for c in server.calls if c["name"] == "AggregatedResponse"]
    assert "There is no Genre with Name 'Polka'." in _prompt(synthesis)
