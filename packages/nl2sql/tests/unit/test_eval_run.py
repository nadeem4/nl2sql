"""``engine.evals.run``: the modes, the cases and roles it runs, curated answers, configs and the report.

Key-free. The pipeline run is replaced by canned results (``run._ask``), so
these test what the runner decides: which cases run, on which derived
runtime, how they are scored and reported. The real pipeline is
``tests/e2e/test_evals_plans.py`` (mode plans) and
``tests/e2e/test_evals_full_fake_llm.py`` (mode full on a fake LLM).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from nl2sql.api.query_api import AnswerSource, QueryResult, RowSample, SubQueryResult
from nl2sql.config import LLM
from nl2sql.evaluation import run as run_module
from nl2sql.evaluation.run import UnknownPriceError, run_evals
from nl2sql.evaluation.suite import EvalSuite
from nl2sql.llm.models import AgentConfig, LLMFileConfig
from nl2sql.registry import Registry
from nl2sql.testing.runtime import fake_runtime

ROWS = [{"n": 1}]


def _case(cid, *, status="approved", outcome=None, tags=(), sql="SELECT 1", rows=ROWS, plan=None):
    return {"id": cid, "question": f"question {cid}", "datasources": ["db"], "status": status,
            "tags": list(tags), "outcome": outcome or {"admin": "allowed"}, "tables": ["T"], "columns": ["T.n"],
            "sql": sql, "rows": rows, "plan": plan}


def _suite(*cases) -> EvalSuite:
    return EvalSuite.model_validate({"cases": list(cases)})


def _answer(rows=((1,),), source=None, status="success", datasource="db") -> QueryResult:
    sample = RowSample(columns=["n"], rows=[list(r) for r in rows], total_rows=len(rows))
    return QueryResult(status=status, source=source or AnswerSource(),
                       sub_queries=[SubQueryResult(id="sq1", rows=sample, datasource_id=datasource)])


class _Asker:
    """Stands in for one pipeline run: records what the run's runtime looked like and answers."""

    def __init__(self, answer=None):
        self.seen = []
        self.answer = answer or (lambda run, case, role: _answer())

    def __call__(self, run, case, role):
        self.seen.append(SimpleNamespace(case=case.id, role=role, curated=run.curated,
                                         plan_cache=run.settings.pipeline.plan_cache,
                                         names_tables=run.config.policies.refusal_names_tables, run=run))
        return self.answer(run, case, role), None


def _ctx(**attrs):
    attrs.setdefault("curated", object())
    attrs.setdefault("llm_registry", SimpleNamespace(replace_llms=lambda _agents: None))
    return fake_runtime(policies={"refusal_names_tables": True}, settings={"pipeline": {"plan_cache": True}},
                        registry=Registry.default(), **attrs)


def _run(monkeypatch, suites, *, asker=None, mode="full", scorers=("result_match", "outcome"), **kwargs):
    asker = asker or _Asker()
    monkeypatch.setattr(run_module, "_ask", asker)
    if mode == "full":
        kwargs.setdefault("llm", {"a": LLMFileConfig(default=AgentConfig(provider="openai", model="gpt-5.4"))})
        kwargs.setdefault("max_cost_usd", 1.0)
    ctx = kwargs.pop("ctx", None) or _ctx()
    report = run_evals(ctx, suites, mode=mode, scorers=list(scorers), curated=kwargs.pop("curated", True),
                       **kwargs)
    return report, asker, ctx


def test_only_approved_cases_run(monkeypatch):
    suite = _suite(_case("a"), _case("d", status="draft"), _case("r", status="retired"))
    report, asker, _ = _run(monkeypatch, [suite])
    assert [s.case for s in asker.seen] == ["a"]
    assert [r["id"] for r in report.configs["a"]["results"]] == ["a"]


def test_each_case_runs_once_per_role_in_its_outcome_and_roles_narrow_it(monkeypatch):
    suite = _suite(_case("a", outcome={"admin": "allowed", "viewer": "refused"}))
    _, asker, _ = _run(monkeypatch, [suite])
    assert [(s.case, s.role) for s in asker.seen] == [("a", "admin"), ("a", "viewer")]
    _, asker, _ = _run(monkeypatch, [suite], roles=["viewer"])
    assert [(s.case, s.role) for s in asker.seen] == [("a", "viewer")]


def test_cases_narrows_by_id_or_tag(monkeypatch):
    suite = _suite(_case("a", tags=["join"]), _case("b"), _case("c", tags=["join"]))
    _, asker, _ = _run(monkeypatch, [suite], cases=["join"])
    assert [s.case for s in asker.seen] == ["a", "c"]
    with pytest.raises(ValueError, match="nope"):
        _run(monkeypatch, [suite], cases=["nope"])


def test_passes_runs_every_case_that_many_times(monkeypatch):
    report, asker, _ = _run(monkeypatch, [_suite(_case("a"))], passes=2)
    assert len(asker.seen) == 2
    assert report.configs["a"]["accuracy"]["pass_k"]["k"] == 2


def test_a_raising_scorer_marks_the_case_error(monkeypatch):
    class Broken:
        interface_version = 1
        modes = frozenset({"full"})

        from nl2sql.evaluation.scorers import NoConfig as config_model

        @classmethod
        def from_config(cls, cfg):
            return cls()

        def score(self, run):
            raise RuntimeError("boom")

    ctx = _ctx()
    ctx.registry.add_scorer("broken", Broken)
    report, _, _ = _run(monkeypatch, [_suite(_case("a"), _case("b"))], ctx=ctx,
                        scorers=("result_match", "broken"))
    results = report.configs["a"]["results"]
    assert [r["status"] for r in results] == ["error", "error"]
    assert results[0]["scores"]["broken"]["passed"] is None
    assert "boom" in results[0]["scores"]["broken"]["detail"]["error"]
    assert results[0]["scores"]["result_match"]["passed"] is True
    summary = report.configs["a"]["summary"]["total"]
    assert summary["error"] == 2 and summary["fail"] == 0


def test_a_case_passes_only_when_every_scorer_that_applies_passes(monkeypatch):
    asker = _Asker(lambda run, case, role: _answer(rows=((2,),)) if case.id == "b" else _answer())
    report, _, _ = _run(monkeypatch, [_suite(_case("a"), _case("b"))], asker=asker)
    results = {r["id"]: r for r in report.configs["a"]["results"]}
    assert results["a"]["status"] == "pass"
    assert results["b"]["status"] == "fail" and "rows" in results["b"]["reason"]
    assert report.configs["a"]["accuracy"]["overall"] == 0.5


def test_a_case_that_raises_is_a_failure_not_an_abort(monkeypatch):
    def answer(run, case, role):
        if case.id == "a":
            raise RuntimeError("driver gone")
        return _answer()
    report, _, _ = _run(monkeypatch, [_suite(_case("a"), _case("b"))], asker=_Asker(answer))
    results = {r["id"]: r for r in report.configs["a"]["results"]}
    assert results["a"]["status"] == "fail" and "driver gone" in results["a"]["reason"]
    assert results["b"]["status"] == "pass"


@pytest.mark.parametrize("mode", ["plans", "retrieval"])
def test_modes_plans_and_retrieval_run_with_curated_off(monkeypatch, mode):
    seen = []
    monkeypatch.setattr(run_module, "_measure_retrieval", lambda run, case, role: (seen.append(run.curated), ({}, []))[1])
    plan = {"tables": [{"name": "T", "alias": "t1"}],
            "select_items": [{"alias": "n", "expr": {"kind": "column", "alias": "t1", "column_name": "n"}}]}
    monkeypatch.setattr(run_module, "GoldPlanLLM", _FakeGold)
    report, asker, ctx = _run(monkeypatch, [_suite(_case("a", plan=plan))], mode=mode, curated=True,
                              scorers=("result_match", "retrieval_recall"))
    curated = seen if mode == "retrieval" else [s.curated for s in asker.seen]
    assert curated == [None]
    assert report.curated is False
    assert ctx.curated is not None
    assert list(report.configs) == [mode]


class _FakeGold:
    def __init__(self, plans):
        self.plans = plans

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def agent_config(self):
        return AgentConfig(provider="openai", model="gpt-4o", base_url="http://fake", api_key="k")

    def select(self, case):
        pass


def test_mode_plans_needs_a_plan_for_every_answerable_case(monkeypatch):
    with pytest.raises(ValueError, match="no gold plan.*a"):
        _run(monkeypatch, [_suite(_case("a"))], mode="plans")


def test_mode_full_turns_the_plan_cache_off_and_pins_the_generic_refusal(monkeypatch):
    _, asker, ctx = _run(monkeypatch, [_suite(_case("a"))])
    assert [(s.plan_cache, s.names_tables) for s in asker.seen] == [(False, False)]
    # The engine's own config is untouched.
    assert ctx.settings.pipeline.plan_cache is True and ctx.config.policies.refusal_names_tables is True


def test_mode_full_compares_named_llm_configs(monkeypatch):
    swapped = []
    registry = SimpleNamespace(replace_llms=lambda agents: swapped.append(agents["default"].model))
    configs = {"big": LLMFileConfig(default=AgentConfig(provider="openai", model="gpt-5.4")),
               "small": LLM(provider="openai", model="gpt-5.4-mini")}

    def answer(run, case, role):
        # The small config gets b wrong.
        return _answer(rows=((2,),)) if swapped[-1] == "gpt-5.4-mini" and case.id == "b" else _answer()

    report, asker, _ = _run(monkeypatch, [_suite(_case("a"), _case("b"))], llm=configs, asker=_Asker(answer),
                            ctx=_ctx(llm_registry=registry))
    assert swapped == ["gpt-5.4", "gpt-5.4-mini"]
    # Each config runs on a runtime of its own.
    assert asker.seen[0].run is not asker.seen[2].run
    assert report.configs["big"]["accuracy"]["overall"] == 1.0
    assert report.configs["small"]["accuracy"]["overall"] == 0.5
    assert report.comparison["differences"] == [{"id": "b", "role": "admin",
                                                  "outcomes": {"big": "pass", "small": "fail"}}]


def test_mode_full_refuses_an_unpriced_model(monkeypatch):
    asker = _Asker()
    llm = {"x": LLMFileConfig(default=AgentConfig(provider="openai", model="gpt-9-turbo"))}
    with pytest.raises(UnknownPriceError, match="gpt-9-turbo"):
        _run(monkeypatch, [_suite(_case("a"))], llm=llm, asker=asker)
    assert asker.seen == []


def test_mode_full_needs_a_cost_cap(monkeypatch):
    with pytest.raises(ValueError, match="max_cost_usd"):
        _run(monkeypatch, [_suite(_case("a"))], max_cost_usd=None)


def test_mode_full_stops_before_a_case_that_could_pass_the_cap(monkeypatch):
    monkeypatch.setattr(run_module, "question_cost", lambda usage, cfg, prices: 0.4)
    report, asker, _ = _run(monkeypatch, [_suite(*(_case(c) for c in "abcde"))], max_cost_usd=1.0)
    # 0.4, 0.8, then 0.8 + the dearest so far (0.4) could pass 1.0.
    assert len(asker.seen) == 2
    assert report.stopped == "max_cost" and report.spent == pytest.approx(0.8)
    assert report.configs["a"]["completed_cases"] == 2 and report.configs["a"]["planned_cases"] == 5


def test_curated_hit_is_also_scored_generated(monkeypatch):
    verified = AnswerSource(type="verified", id="top", datasource_id="db")

    def answer(run, case, role):
        if run.curated is not None:   # the verified query answers, right
            return _answer(source=verified)
        return _answer(rows=((2,),))  # the model, on its own, gets it wrong

    report, asker, _ = _run(monkeypatch, [_suite(_case("a"))], asker=_Asker(answer), curated=True)
    assert [s.curated is not None for s in asker.seen] == [True, False]
    [record] = report.configs["a"]["results"]
    assert record["source"] == "verified" and record["verified_query"] == "top"
    assert record["status"] == "pass" and record["generated"]["status"] == "fail"


def test_report_has_accuracy_and_accuracy_generated(monkeypatch):
    verified = AnswerSource(type="verified", id="top", datasource_id="db")

    def answer(run, case, role):
        if case.id == "a" and run.curated is not None:
            return _answer(source=verified)
        return _answer(rows=((2,),)) if case.id == "a" else _answer()

    report, _, _ = _run(monkeypatch, [_suite(_case("a"), _case("b"))], asker=_Asker(answer), curated=True)
    accuracy = report.configs["a"]["accuracy"]
    assert accuracy["overall"] == 1.0          # what users see: a verified answer, and b
    assert accuracy["generated"] == 0.5        # the model's own skill: a is wrong on the generated path
    assert report.accuracy == 1.0 and report.accuracy_generated == 0.5
    assert accuracy["by_source"]["verified"]["total"] == 1 and accuracy["by_source"]["generated"]["total"] == 1
    assert accuracy["generated_by_source"]["verified"]["accuracy"] == 0.0


def test_curated_off_never_runs_a_case_twice(monkeypatch):
    _, asker, _ = _run(monkeypatch, [_suite(_case("a"))], curated=False)
    assert [s.curated for s in asker.seen] == [None]


def test_clarify_cases_run_only_with_clarify(monkeypatch):
    suite = _suite(_case("a"), _case("c", outcome={"admin": "clarify"}, sql=None, rows=None))
    report, asker, _ = _run(monkeypatch, [suite])
    assert [s.case for s in asker.seen] == ["a"] and report.skipped == 1 and report.clarify is False

    clarifying = _Asker(lambda run, case, role: QueryResult(status="needs_clarification")
                        if case.id == "c" else _answer())
    report, asker, _ = _run(monkeypatch, [suite], asker=clarifying, clarify=True)
    assert [s.case for s in asker.seen] == ["a", "c"] and report.skipped == 0
    assert {r["id"]: r["status"] for r in report.configs["a"]["results"]} == {"a": "pass", "c": "pass"}


def test_every_case_publishes_eval_case_and_the_run_eval_finished(monkeypatch):
    ctx = _ctx()
    events = []
    ctx.bus.subscribe(events.append, names=["eval.case", "eval.finished"])
    _run(monkeypatch, [_suite(_case("a"), _case("b"))], ctx=ctx)
    assert [e.name for e in events] == ["eval.case", "eval.case", "eval.finished"]
    assert events[0].data["case_id"] == "a" and events[0].data["pass"] is True
    assert events[0].data["scores"] == {"result_match": True, "outcome": True}
    assert events[-1].data["cases"] == 2 and events[-1].data["accuracy"] == 1.0


def test_mode_retrieval_cases_run_under_their_own_run_scope(monkeypatch):
    monkeypatch.setattr(run_module, "_measure_retrieval", lambda run, case, role: ({}, []))
    ctx = _ctx()
    events = []
    ctx.bus.subscribe(events.append, names=["run.started", "run.finished", "run.failed"])
    _run(monkeypatch, [_suite(_case("a"), _case("b"))], mode="retrieval", ctx=ctx,
         scorers=("retrieval_recall",))
    assert [e.name for e in events] == ["run.started", "run.finished"] * 2
    assert events[0].data["question"] == "question a"


def test_on_case_is_called_after_each_case_with_the_spend_so_far(monkeypatch):
    calls = []
    _run(monkeypatch, [_suite(_case("a"), _case("b"))], on_case=lambda name, record, spent: calls.append(
        (name, record["id"], spent)))
    assert [(n, i) for n, i, _ in calls] == [("a", "a"), ("a", "b")]


def test_the_report_names_each_suite_by_its_digest(monkeypatch, tmp_path):
    from nl2sql.evaluation.suite import load_suite, write_suite

    path = tmp_path / "mine.yaml"
    write_suite(path, {"version": 1, "cases": [_case("a")]})
    report, _, _ = _run(monkeypatch, [load_suite(path)])
    [suite] = report.suites
    assert suite["name"] == "mine" and len(suite["digest"]) == 64


def test_an_unknown_mode_is_refused(monkeypatch):
    with pytest.raises(ValueError, match="mode"):
        _run(monkeypatch, [_suite(_case("a"))], mode="tier3")
