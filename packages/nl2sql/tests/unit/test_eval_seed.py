"""Seeding: draft eval cases from a datasource (wave_a.md, section 8, "Seeding: benchmark your own database"; D26).

The model is a ``FakeLLMServer`` answering the seeder's structured output
(``SeedBatch``) from a queue the test fills; nothing calls a real model. The
datasource is the vendored Chinook database, indexed once with fake
embeddings, so the seeder sees a live snapshot as it would in a project.
Everything after the model is the engine's own: the caller's scope, the
logical validator and the generator through the pinned-plan entry, and the
adapter running the SQL read-only.
"""
from __future__ import annotations

import json
import os
import pathlib
import tempfile

import pytest
import yaml
from langchain_core.embeddings import FakeEmbeddings

from nl2sql.api.evals_api import EvalsAPI
from nl2sql.config import Embeddings
from nl2sql.datasets import CHINOOK_DB_PATH
from nl2sql.evaluation.run import UnknownPriceError
from nl2sql.evaluation.seed import BATCH_SIZE, SeedReport
from nl2sql.evaluation.suite import CHINOOK_SUITE_PATH, load_suite
from nl2sql.indexing.orchestrator import IndexingOrchestrator
from nl2sql.policies import UserContext
from nl2sql.runtime import Runtime
from nl2sql.testing.config import make_config
from nl2sql.testing.fake_llm import FakeLLMServer, Rule

MODEL = "gpt-seed-test"  # priced by llm.prices only: $1 per million tokens in and out
GOLD = {c.id: c for c in load_suite(CHINOOK_SUITE_PATH).cases}
ANALYST = UserContext(roles=["analyst"])


def _gold(case_id: str) -> dict:
    case = GOLD[case_id]
    return {"question": case.question, "difficulty": case.difficulty or "easy", "plan": case.plan}


def _plan(table: str, column: str, where=None) -> dict:
    plan = {"tables": [{"name": table, "alias": "t1"}],
            "select_items": [{"alias": column.lower(), "expr": {"kind": "column", "alias": "t1",
                                                                  "column_name": column}}]}
    if where is not None:
        plan["where"] = {"kind": "binary", "op": "=", "left": {"kind": "column", "alias": "t1",
                                                                "column_name": where[0]},
                         "right": {"kind": "literal", "value": where[1]}}
    return plan


VALID = ["chinook_001", "chinook_003", "chinook_018", "chinook_019", "chinook_030", "chinook_038",
         "chinook_047", "chinook_025", "chinook_021", "chinook_004", "chinook_006", "chinook_014"]


def _valid(n: int, start: int = 0) -> list:
    return [_gold(case_id) for case_id in VALID[start:start + n]]


class _Seeder:
    """The fake model: each ``SeedBatch`` call answers the next queued batch, and keeps the prompt it was sent."""

    def __init__(self):
        self.batches: list = []
        self.prompts: list = []
        self.rule = Rule("SeedBatch", self._answer)
        self.server = FakeLLMServer([self.rule])

    def _answer(self, prompt: str) -> dict:
        self.prompts.append(prompt)
        return {"cases": self.batches.pop(0) if self.batches else []}

    def reset(self, *batches, usage=None):
        self.batches, self.prompts = list(batches), []
        self.server.calls.clear()
        self.rule.usage = usage


@pytest.fixture(scope="module")
def env():
    """One runtime over Chinook, indexed with fake embeddings, its LLM the fake seeder."""
    seeder = _Seeder()
    seeder.server.start()
    folder = pathlib.Path(tempfile.mkdtemp(prefix="nl2sql-seed-"))
    previous = os.environ.get("SEED_FAKE_KEY")
    os.environ["SEED_FAKE_KEY"] = "sk-fake-seed"
    config = make_config(
        folder,
        datasources=[{"id": "chinook", "type": "sqlite", "connection": {"database": CHINOOK_DB_PATH.as_posix()},
                      "index": {"sample_values": True}}],
        llm={"provider": "openai", "model": MODEL, "api_key": "${env:SEED_FAKE_KEY}",
             "base_url": seeder.server.base_url, "prices": {MODEL: {"input": 1.0, "output": 1.0}}},
        policies={"roles": {"admin": {"description": "everything", "allow": ["*"]},
                            "analyst": {"description": "no employees", "allow": ["chinook"],
                                        "deny": ["chinook.main.Employee"]},
                            "outsider": {"description": "another database", "allow": ["other"]}}},
        evals={"max_cost_usd": 2.0})
    ctx = Runtime.from_config(config.model_copy(update={"embeddings": Embeddings(instance=FakeEmbeddings(size=8))}))
    orchestrator = IndexingOrchestrator(ctx)
    for adapter in ctx.ds_registry.list_adapters():
        orchestrator.index_datasource(adapter)
    try:
        yield ctx, seeder
    finally:
        ctx.close()
        seeder.server.stop()
        if previous is None:
            os.environ.pop("SEED_FAKE_KEY", None)
        else:
            os.environ["SEED_FAKE_KEY"] = previous


def _seed(env, out, *batches, count=None, user=ANALYST, usage=None, **kwargs) -> SeedReport:
    ctx, seeder = env
    seeder.reset(*batches, usage=usage)
    count = count if count is not None else sum(len(b) for b in batches)
    return EvalsAPI(ctx).seed("chinook", count=count, out=out, user_context=user, **kwargs)


def _tool(call: dict) -> dict:
    """The JSON schema the seeder asked the model to answer in."""
    body = call["body"]
    if body.get("tools"):
        return body["tools"][0]["function"]["parameters"]
    return body["response_format"]["json_schema"]["schema"]


# ---------------------------------------------------------------- what is asked


def test_seed_asks_for_plans_not_sql(env, tmp_path):
    out = tmp_path / "seeded.yaml"
    report = _seed(env, out, _valid(2))

    _, seeder = env
    [call] = seeder.server.calls
    schema = json.dumps(_tool(call))
    # Each proposed case is a question and a typed plan; nothing in the answer can carry SQL.
    assert '"plan"' in schema and '"select_items"' in schema and '"question"' in schema
    assert '"sql"' not in schema.lower()
    assert "PlanModel" in schema or "select_items" in schema
    # The SQL in the drafts is the engine's own rendering of each plan.
    assert report.written == 2
    cases = load_suite(out).cases
    assert all(c.sql and c.sql.lstrip().upper().startswith("SELECT") for c in cases)
    assert "Customer" in cases[0].sql


def test_seed_asks_five_per_call(env, tmp_path):
    assert BATCH_SIZE == 5
    report = _seed(env, tmp_path / "seeded.yaml", _valid(5), _valid(5, 5), _valid(2, 10), count=12)

    _, seeder = env
    assert report.written == 12
    assert len(seeder.prompts) == 3
    asked = [next(line for line in p.splitlines() if "Write exactly" in line) for p in seeder.prompts]
    assert asked == ["Write exactly 5 questions.", "Write exactly 5 questions.", "Write exactly 2 questions."]
    # Each call is shown what is already written, so it is not asked again.
    assert GOLD["chinook_001"].question not in seeder.prompts[0]
    assert GOLD["chinook_001"].question in seeder.prompts[1]


def test_seed_stops_at_its_cost_cap(env, tmp_path):
    # One million input tokens at $1 per million: the first call spends $1 of the $0.50 cap.
    usage = {"prompt_tokens": 1_000_000, "completion_tokens": 1, "total_tokens": 1_000_001}
    out = tmp_path / "seeded.yaml"
    report = _seed(env, out, _valid(5), _valid(5, 5), count=10, usage=usage, max_cost_usd=0.5)

    _, seeder = env
    assert len(seeder.server.calls) == 1  # the second call is refused before it is sent
    assert report.stopped_at_cost_cap is True
    assert report.written == 5 and len(load_suite(out).cases) == 5  # what was seeded so far is written
    assert report.usage.total.calls == 1 and report.usage.total.cost == pytest.approx(1.000001)


def test_seed_takes_its_cost_cap_from_evals_yaml(env, tmp_path):
    usage = {"prompt_tokens": 2_500_000, "completion_tokens": 0, "total_tokens": 2_500_000}
    report = _seed(env, tmp_path / "seeded.yaml", _valid(5), _valid(5, 5), count=10, usage=usage)
    assert report.stopped_at_cost_cap is True  # evals.yaml's max_cost_usd: 2.0


def test_seed_refuses_an_unpriced_model_before_any_call(env, tmp_path):
    ctx, seeder = env
    seeder.reset(_valid(1))
    unpriced = ctx.derive({"llm.prices": {}})
    with pytest.raises(UnknownPriceError, match=MODEL):
        EvalsAPI(unpriced).seed("chinook", count=1, out=tmp_path / "s.yaml", user_context=ANALYST)
    assert seeder.server.calls == []


def test_seed_gives_up_when_the_model_keeps_answering_nothing(env, tmp_path):
    report = _seed(env, tmp_path / "seeded.yaml", count=5)
    _, seeder = env
    # At most three calls per batch of five, whatever the model answers.
    assert len(seeder.server.calls) == 3 and report.written == 0
    assert report.stopped_at_cost_cap is False


# ---------------------------------------------------------------- what is kept


def test_invalid_plan_is_dropped_and_counted(env, tmp_path):
    no_table = {"question": "How many widgets are there?", "difficulty": "easy", "plan": _plan("Widget", "Name")}
    no_column = {"question": "What colour is each artist?", "difficulty": "easy",
                 "plan": _plan("Artist", "Colour")}
    out = tmp_path / "seeded.yaml"
    report = _seed(env, out, [no_table, *_valid(1), no_column], count=3)

    assert report.dropped_invalid == 2 and report.written == 1
    assert [c.question for c in load_suite(out).cases] == [GOLD["chinook_001"].question]


def test_failing_or_empty_sql_is_dropped(env, tmp_path, monkeypatch):
    ctx, _ = env
    empty = {"question": "Which customers live in Atlantis?", "difficulty": "easy",
             "plan": _plan("Customer", "FirstName", where=("Country", "Atlantis"))}
    failing = {"question": "List every genre.", "difficulty": "easy", "plan": _plan("Genre", "Name")}
    real = ctx.ds_registry.get_adapter("chinook")

    class _Failing:
        """The real adapter, except the database rejects the genre query."""

        def __getattr__(self, name):
            return getattr(real, name)

        def execute(self, request):
            if "Genre" in request.payload["sql"]:
                return real.execute(request.model_copy(update={"payload": {"sql": "SELECT nope FROM Genre"}}))
            return real.execute(request)

    get_adapter = ctx.ds_registry.get_adapter
    monkeypatch.setattr(ctx.ds_registry, "get_adapter",
                        lambda ds: _Failing() if ds == "chinook" else get_adapter(ds))
    out = tmp_path / "seeded.yaml"
    report = _seed(env, out, [empty, failing, *_valid(1)], count=3)

    assert report.dropped_empty == 1 and report.dropped_failed == 1 and report.written == 1


def test_drafts_are_written_as_draft_and_seeded(env, tmp_path):
    out = tmp_path / "evals" / "seeded.yaml"
    report = _seed(env, out, _valid(2))

    assert report.written == 2 and report.path == out
    text = out.read_text(encoding="utf-8")
    assert text.startswith("#")  # a header saying what the file is and what to do with it
    [first, second] = load_suite(out).cases
    assert first.status == second.status == "draft"
    assert first.origin == "seeded" and first.datasources == ["chinook"]
    assert first.outcome == {"analyst": "allowed"}
    assert first.question == GOLD["chinook_001"].question and first.difficulty == GOLD["chinook_001"].difficulty
    assert first.tables == ["Customer"]
    assert "Customer.Country" in first.columns and "Customer.CustomerId" in first.columns
    assert first.plan and first.plan["tables"] == [{"name": "Customer", "alias": "t1"}]
    # Rows are never seeded: regenerate writes them once a person approves the case.
    assert first.rows is None and yaml.safe_load(text)["cases"][0]["rows"] is None
    assert first.id != second.id


def test_existing_question_is_skipped(env, tmp_path):
    out = tmp_path / "seeded.yaml"
    _seed(env, out, _valid(1))
    before = load_suite(out).cases

    again = dict(_gold("chinook_001"), question="  how many CUSTOMERS do we have, by country? ")
    report = _seed(env, out, [again, *_valid(1, 1)], count=2)

    assert report.dropped_duplicate == 1 and report.written == 1
    after = load_suite(out).cases
    assert after[0] == before[0] and len(after) == 2
    assert len({c.id for c in after}) == 2


def test_draft_cases_never_run(env, tmp_path):
    ctx, seeder = env
    out = tmp_path / "seeded.yaml"
    _seed(env, out, _valid(2))
    seeder.reset()

    report = EvalsAPI(ctx).run([out], mode="plans")

    assert report.configs["plans"]["completed_cases"] == 0
    assert seeder.server.calls == []


# ---------------------------------------------------------------- whose seeding


def test_seed_respects_the_callers_scope(env, tmp_path):
    employees = _gold("chinook_035")  # How many employees does the company have?
    out = tmp_path / "seeded.yaml"
    report = _seed(env, out, [employees, *_valid(1)], count=2)

    _, seeder = env
    # A table the role may not read is never shown to the model...
    assert "Employee" not in seeder.prompts[0] and "Customer" in seeder.prompts[0]
    # ...and a plan that reads it anyway is refused by the validator, as a question's would be.
    assert report.dropped_invalid == 1 and report.written == 1
    assert [c.question for c in load_suite(out).cases] == [GOLD["chinook_001"].question]


@pytest.mark.parametrize("user", [None, UserContext(roles=[])])
def test_seed_needs_a_caller_with_a_role(env, tmp_path, user):
    with pytest.raises(PermissionError, match="role"):
        _seed(env, tmp_path / "s.yaml", _valid(1), user=user)
    assert env[1].server.calls == []


@pytest.mark.parametrize("role", ["outsider", "no_such_role"])
def test_seed_refuses_a_role_that_cannot_read_the_datasource(env, tmp_path, role):
    with pytest.raises(PermissionError, match="chinook"):
        _seed(env, tmp_path / "s.yaml", _valid(1), user=UserContext(roles=[role]))
    assert env[1].server.calls == []


# ---------------------------------------------------------------- where drafts may go


def test_seed_never_writes_into_reviewed_cases(env, tmp_path):
    reviewed = tmp_path / "reviewed.yaml"
    reviewed.write_text(CHINOOK_SUITE_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    before = reviewed.read_bytes()
    with pytest.raises(ValueError, match="approved"):
        _seed(env, reviewed, _valid(1))
    with pytest.raises(ValueError, match="ships"):
        _seed(env, CHINOOK_SUITE_PATH, _valid(1))
    assert reviewed.read_bytes() == before and env[1].server.calls == []


def test_seed_never_writes_into_a_configured_suite(env, tmp_path):
    ctx, _ = env
    configured = ctx.derive({"evals.suites": [pathlib.Path("evals/mine.yaml")]})
    target = ctx.config.folder / "evals" / "mine.yaml"
    with pytest.raises(ValueError, match="evals.yaml"):
        EvalsAPI(configured).seed("chinook", count=1, out="evals/mine.yaml", user_context=ANALYST)
    assert not target.exists()


def test_seed_needs_a_cost_cap(env, tmp_path):
    ctx, _ = env
    uncapped = ctx.derive({"evals.max_cost_usd": None})
    with pytest.raises(ValueError, match="max_cost_usd"):
        EvalsAPI(uncapped).seed("chinook", count=1, out=tmp_path / "s.yaml", user_context=ANALYST)
