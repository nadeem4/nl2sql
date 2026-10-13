"""Tier 2 runs with curated answers off unless asked, and records each answer's source.

A verified query answers without a plan, so with curated answers on a tier 2
hit would be scored as if the model had planned it. Tier 2 therefore turns
them off by default, as tier 1 does, and every case record says whether its
answer was ``verified`` or ``generated`` so a run with them on can split its
accuracy by source.
"""
from types import SimpleNamespace

from nl2sql.api.query_api import AnswerSource, QueryResult
from nl2sql.configs.llm import AgentConfig, LLMFileConfig
from nl2sql.evaluation import tier2

QUESTIONS = [SimpleNamespace(id="q0", question="?", tags=["join"], difficulty="easy"),
             SimpleNamespace(id="q1", question="?", tags=["join"], difficulty="easy")]


def _row(question, role, status):
    return {"id": question.id, "question": "?", "role": role, "expected": "allowed", "status": status,
            "reason": "", "sql": "SELECT 1", "rows": 1, "gold_rows": 1}


def _fake_runner(seen, sources):
    """A runner whose q0 is answered by a verified query when curated answers are on."""
    class _Runner:
        def __init__(self, config, ctx, workers, before_case):
            self.ctx = ctx

        def cases(self):
            return [(q, "admin") for q in QUESTIONS]

        def run_case(self, question, role):
            seen.append(self.ctx.curated)
            verified = self.ctx.curated is not None and question.id == "q0"
            source = sources.get(question.id) if verified else None
            result = QueryResult(source=source or AnswerSource())
            return _row(question, role, "pass" if question.id == "q0" else "fail"), result
    return _Runner


def _ctx(curated):
    return SimpleNamespace(curated=curated, llm_registry=SimpleNamespace(replace_llms=lambda _agents: None))


def _run(monkeypatch, ctx, **kwargs):
    seen = []
    sources = {"q0": AnswerSource(type="verified", id="top_customers", datasource_id="chinook")}
    monkeypatch.setattr(tier2, "BenchmarkRunner", _fake_runner(seen, sources))
    cfg = LLMFileConfig(default=AgentConfig(provider="openai", model="gpt-5.4"))
    board = tier2.run_tier2(ctx, SimpleNamespace(dataset_path="gold.yaml", roles=["admin"]), {"a": cfg},
                            max_cost=1.0, **kwargs)
    return board, seen


def test_tier2_runs_with_curated_answers_off_by_default_and_restores_them(monkeypatch):
    configured = object()
    ctx = _ctx(configured)

    board, seen = _run(monkeypatch, ctx)

    assert seen == [None, None]
    assert ctx.curated is configured
    assert board["curated"] is False
    assert [r["source"] for r in board["configs"]["a"]["results"]] == ["generated", "generated"]


def test_use_curated_keeps_them_on_and_each_record_names_its_source(monkeypatch):
    configured = object()
    ctx = _ctx(configured)

    board, seen = _run(monkeypatch, ctx, use_curated=True)

    assert seen == [configured, configured]
    assert board["curated"] is True
    results = board["configs"]["a"]["results"]
    assert [r["source"] for r in results] == ["verified", "generated"]
    assert results[0]["verified_query"] == "top_customers" and results[1]["verified_query"] is None
    by_source = board["configs"]["a"]["accuracy"]["by_source"]
    assert by_source["verified"]["accuracy"] == 1.0 and by_source["verified"]["total"] == 1
    assert by_source["generated"]["accuracy"] == 0.0 and by_source["generated"]["total"] == 1


def test_a_record_written_before_sources_were_kept_still_scores():
    old = {"id": "q0", "question": "?", "role": "admin", "expected": "allowed", "status": "pass", "reason": "",
           "sql": "SELECT 1", "rows": 1, "gold_rows": 1, "pass": 1, "tags": ["join"], "difficulty": "easy",
           "cost": 0.0, "latency_s": 1.0, "rows_digest": "d", "error_codes": [], "refused_unanswerable": False,
           "retries": 0, "tokens_by_node": {}, "timings": {}}
    board = tier2.score_config([old], passes=1)
    assert board["accuracy"]["overall"] == 1.0
    assert board["accuracy"]["by_source"] == {"unknown": {"pass": 1, "lenient_pass": 1, "total": 1,
                                                          "accuracy": 1.0, "lenient_accuracy": 1.0}}
