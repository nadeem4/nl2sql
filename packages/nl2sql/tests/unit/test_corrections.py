"""Corrections: a thumbs-down with a fix becomes a checked ``verified_query`` proposal (F1).

docs/architecture/wave_b.md, section 10, and D55. The engine is Q1's: a real
engine on a small SQLite database, indexed with a fake embedder, so nothing is
downloaded and no model is called. A run is a trace written to the engine's
trace store, as a run with traces kept leaves one.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from test_review_queue import ADMIN, ANALYST, _cli, _engine, _project

from nl2sql.feedback.corrections import CorrectionRefused
from nl2sql.hooks import Allow, Deny, HookBinding, HookRunner, NeedsApproval

QUESTION = "What is the revenue?"
WRONG_SQL = "SELECT SUM(id) AS revenue FROM orders"
FIX = "SELECT SUM(total) AS revenue FROM orders"
# A plan fix builds the run's model clients but calls none: a call to this address would fail the test.
LLM = {"provider": "openai", "model": "gpt-fake", "api_key": "sk-fake-corrections", "base_url": "http://127.0.0.1:9"}


def _plan(table="orders", column="total"):
    return {"tables": [{"name": table, "alias": "t1"}],
            "select_items": [{"alias": column.lower(), "expr": {"kind": "column", "alias": "t1",
                                                                  "column_name": column}}]}


def _run(engine, trace_id="t1", question=QUESTION, sql=WRONG_SQL, datasources=("shop",)):
    """A kept trace of a successful run on ``datasources``, as a run with traces on leaves it."""
    result = {"trace_id": trace_id, "status": "success", "source": {"type": "generated"},
              "sub_queries": [{"id": f"sq{i}", "sql": sql, "datasource_id": ds} for i, ds in enumerate(datasources)]}
    engine._runtime.traces.write(trace_id, {"trace_format_version": 1, "trace_id": trace_id,
                                            "request": {"question": question, "roles": ["admin"]},
                                            "nodes": [], "result": result})
    return trace_id


def _hooks(engine, monkeypatch, decide, points=("execute.before",)):
    seen = []

    def hook(hctx):
        seen.append((hctx.point, hctx))
        return decide(hctx)

    monkeypatch.setattr(engine._runtime, "hooks", HookRunner(
        [HookBinding(name="corp_review", hook=hook, points=frozenset(points), on_error="closed",
                     approver_roles=None)]))
    return seen


@pytest.fixture
def project(tmp_path):
    with _engine(_project(tmp_path, llm=LLM)) as engine:
        assert engine.index.build().ok
        yield engine, tmp_path


@pytest.fixture
def engine(project):
    return project[0]


# ---------------------------------------------------------------- a plan fix


def test_plan_fix_goes_through_validator_and_generator(engine, monkeypatch):
    seen = _hooks(engine, monkeypatch, lambda hctx: Allow(), points=("plan.after", "execute.before"))

    proposal = engine.feedback.correct(_run(engine), plan=_plan(), user_context=ANALYST)

    sql = proposal.change["sql"]
    assert "total" in sql and "orders" in sql.lower()  # the engine's rendering of the plan
    assert [point for point, _ in seen] == ["plan.after", "execute.before"]
    assert seen[1][1].sql.strip() == sql.strip() and seen[1][1].user == ANALYST
    assert proposal.kind == "verified_query" and proposal.origin == "correction"
    assert proposal.evidence["ran"] is True and proposal.evidence["columns"] == ["total"]


def test_plan_fix_may_be_json_text(engine):
    proposal = engine.feedback.correct(_run(engine), plan=json.dumps(_plan()), user_context=ADMIN)
    assert "total" in proposal.change["sql"]


def test_rejected_plan_fix_is_refused_with_the_messages(engine):
    with pytest.raises(CorrectionRefused) as error:
        engine.feedback.correct(_run(engine), plan=_plan(column="no_such_column"), user_context=ADMIN)
    assert error.value.code == "invalid_plan"
    assert error.value.messages and any("no_such_column" in m for m in error.value.messages)

    with pytest.raises(CorrectionRefused) as denied:  # the validator checks the corrector's scope
        engine.feedback.correct(_run(engine), plan=_plan(table="salaries", column="amount"), user_context=ANALYST)
    assert denied.value.code == "invalid_plan"
    assert not engine.proposals.list(user_context=ADMIN)


def test_malformed_plan_is_refused(engine):
    with pytest.raises(CorrectionRefused) as error:
        engine.feedback.correct(_run(engine), plan={"tables": "orders"}, user_context=ADMIN)
    assert error.value.code == "invalid_plan"


# ---------------------------------------------------------------- a SQL fix


@pytest.mark.parametrize("sql", [
    "DELETE FROM orders",
    "SELECT 1; SELECT 2",
    "UPDATE orders SET total = 0",
    "SELECT total FROM orders WHERE id = :id",  # it runs once, with no values to bind
    "SELEC nonsense",
])
def test_sql_fix_must_be_one_read_only_select(engine, sql):
    with pytest.raises(CorrectionRefused) as error:
        engine.feedback.correct(_run(engine), sql=sql, user_context=ADMIN)
    assert error.value.code == "not_read_only"
    assert not engine.proposals.list(user_context=ADMIN)


def test_sql_fix_on_a_denied_table_is_refused(engine, monkeypatch):
    seen = _hooks(engine, monkeypatch, lambda hctx: Allow())

    with pytest.raises(CorrectionRefused) as error:
        engine.feedback.correct(_run(engine), sql="SELECT SUM(amount) FROM salaries", user_context=ANALYST)

    assert error.value.code == "denied" and "salaries" in str(error.value)
    assert seen == []  # refused before any hook or run


def test_correction_denied_by_an_execute_before_hook_is_refused(engine, monkeypatch):
    seen = _hooks(engine, monkeypatch, lambda hctx: Deny(reason="No sums on Fridays."))

    with pytest.raises(CorrectionRefused) as error:
        engine.feedback.correct(_run(engine), sql=FIX, user_context=ADMIN)

    assert error.value.code == "hook_denied" and "No sums on Fridays." in str(error.value)
    [(_, hctx)] = seen
    assert hctx.tables == ["shop.main.orders"] and hctx.datasource_id == "shop" and hctx.question == QUESTION
    assert not engine.proposals.list(user_context=ADMIN)


def test_correction_needing_approval_is_refused(engine, monkeypatch):
    _hooks(engine, monkeypatch, lambda hctx: NeedsApproval(reason="A person reviews revenue queries."))

    with pytest.raises(CorrectionRefused) as error:
        engine.feedback.correct(_run(engine), sql=FIX, user_context=ADMIN)

    assert error.value.code == "needs_approval" and "A person reviews revenue queries." in str(error.value)
    assert not engine.proposals.list(user_context=ADMIN)


def test_failing_fix_is_refused_with_the_database_message(engine):
    with pytest.raises(CorrectionRefused) as error:
        engine.feedback.correct(_run(engine), sql="SELECT nope FROM orders", user_context=ADMIN)

    assert error.value.code == "failed" and "nope" in str(error.value)


def test_empty_result_is_allowed_and_recorded(engine):
    proposal = engine.feedback.correct(_run(engine), sql=FIX.replace("orders", "orders WHERE id < 0"),
                                       user_context=ADMIN)
    assert proposal.evidence["ran"] is True and proposal.evidence["row_count"] in (0, 1)


# ---------------------------------------------------------------- the run


def test_multi_datasource_run_is_refused(engine):
    trace_id = _run(engine, datasources=("shop", "hr"))

    with pytest.raises(CorrectionRefused) as error:
        engine.feedback.correct(trace_id, sql=FIX, user_context=ADMIN)

    assert error.value.code == "multi_datasource"
    assert engine.feedback.list() == []


def test_unknown_run_is_refused(engine):
    with pytest.raises(CorrectionRefused) as error:
        engine.feedback.correct("no-such-run", sql=FIX, user_context=ADMIN)
    assert error.value.code == "no_run"


def test_correct_needs_a_caller_and_exactly_one_fix(engine):
    trace_id = _run(engine)
    with pytest.raises(TypeError):
        engine.feedback.correct(trace_id, sql=FIX, user_context=None)
    with pytest.raises(ValueError):
        engine.feedback.correct(trace_id, user_context=ADMIN)
    with pytest.raises(ValueError):
        engine.feedback.correct(trace_id, sql=FIX, plan=_plan(), user_context=ADMIN)


def test_correction_records_a_down_rating(engine):
    trace_id = _run(engine)
    engine.feedback.record({"trace_id": trace_id, "status": "success", "sub_queries": []},
                           question=QUESTION, role="admin", rating="up")

    engine.feedback.correct(trace_id, sql=FIX, note="Revenue is the order total.", user_context=ADMIN)

    [row] = engine.feedback.list()
    assert row["trace_id"] == trace_id and row["rating"] == "down" and row["note"] == "Revenue is the order total."
    assert row["question"] == QUESTION


def test_down_rating_already_recorded_is_kept(engine):
    trace_id = _run(engine)
    engine.feedback.record({"trace_id": trace_id, "status": "success", "sub_queries": []},
                           question=QUESTION, role="admin", rating="down", note="Wrong column.")

    engine.feedback.correct(trace_id, sql=FIX, note="Use total.", user_context=ADMIN)

    [row] = engine.feedback.list()
    assert row["rating"] == "down" and row["note"] == "Wrong column."


def test_rated_run_without_a_trace_can_be_corrected(engine):
    engine.feedback.record({"trace_id": "t9", "status": "success",
                            "sub_queries": [{"sql": WRONG_SQL, "datasource_id": "shop"}]},
                           question=QUESTION, role="admin", rating="down")

    proposal = engine.feedback.correct("t9", sql=FIX, user_context=ADMIN)

    assert proposal.target == QUESTION and proposal.evidence["original_sql"] == WRONG_SQL


# ---------------------------------------------------------------- the proposal


def test_the_proposal_is_a_curated_change_with_its_evidence(engine):
    proposal = engine.feedback.correct(_run(engine), sql=FIX, question="What did we earn?", note="Sum the totals.",
                                       user_context=ADMIN)

    assert proposal.target == "What did we earn?"
    assert proposal.change == {"id": "what_did_we_earn", "description": "Sum the totals.",
                               "questions": ["What did we earn?"], "sql": FIX + "\n"}
    assert proposal.tables == ["shop.main.orders"]
    assert proposal.created_by == "ada" and proposal.status == "proposed"
    evidence = proposal.evidence
    assert evidence["trace_id"] == "t1" and evidence["original_sql"] == WRONG_SQL
    assert evidence["ran"] is True and evidence["row_count"] == 1 and evidence["columns"] == ["revenue"]
    assert engine.proposals.get(proposal.id, user_context=ADMIN) == proposal


def test_a_caller_denied_a_table_the_fix_names_does_not_see_it(engine):
    sql = "SELECT o.total, s.amount FROM orders o JOIN salaries s ON s.id = o.id"
    proposal = engine.feedback.correct(_run(engine), sql=sql, user_context=ADMIN)

    assert proposal.tables == ["shop.main.orders", "shop.main.salaries"]
    assert [p.id for p in engine.proposals.list(user_context=ADMIN)] == [proposal.id]
    assert engine.proposals.list(user_context=ANALYST) == []


def test_a_caller_denied_a_table_the_original_sql_names_does_not_see_it(engine):
    trace_id = _run(engine, sql="SELECT SUM(amount) AS revenue FROM salaries")
    proposal = engine.feedback.correct(trace_id, sql=FIX, user_context=ADMIN)

    assert "shop.main.salaries" in proposal.tables  # the evidence holds that SQL
    assert engine.proposals.list(user_context=ANALYST) == []


def test_nothing_is_written_before_accept(project):
    engine, folder = project
    before = {name: (folder / name).read_bytes() for name in ("curated.yaml", "glossary.yaml", "nl2sql.yaml")}

    proposal = engine.feedback.correct(_run(engine), sql=FIX, user_context=ADMIN)

    assert {name: (folder / name).read_bytes() for name in before} == before
    assert [q.id for q in engine.config.curated.datasources["shop"].verified_queries] == ["order_count"]

    report = engine.proposals.accept(proposal.id, by=ADMIN, edits={proposal.id: {"id": "revenue"}})

    assert report.accepted and [p.id for p in report.accepted] == [proposal.id]
    written = yaml.safe_load((folder / "curated.yaml").read_text(encoding="utf-8"))
    ids = [q["id"] for q in written["datasources"]["shop"]["verified_queries"]]
    assert ids == ["order_count", "revenue"]


# ---------------------------------------------------------------- the CLI


def test_feedback_correct_command(tmp_path):
    config = _project(tmp_path)
    with _engine(config) as engine:
        trace_id = _run(engine)
    (tmp_path / "fix.sql").write_text(FIX, encoding="utf-8")

    done = _cli(tmp_path, "feedback", "correct", trace_id, "--sql", str(tmp_path / "fix.sql"), "--as", "admin",
                "--note", "Sum the totals.")
    assert done.exit_code == 0, done.output
    with _engine(config) as engine:
        [proposal] = engine.proposals.list(user_context=ADMIN)
    assert proposal.id in done.output and "nl2sql review show" in done.output

    (tmp_path / "bad.sql").write_text("DELETE FROM orders", encoding="utf-8")
    refused = _cli(tmp_path, "feedback", "correct", trace_id, "--sql", str(tmp_path / "bad.sql"), "--as", "admin")
    assert refused.exit_code == 1 and "read-only" in refused.output

    both = _cli(tmp_path, "feedback", "correct", trace_id, "--as", "admin")
    assert both.exit_code == 2

