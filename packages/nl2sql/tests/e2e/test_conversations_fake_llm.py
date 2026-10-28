"""``engine.conversations`` end to end on the demo databases, with the fake model (wave_c.md, section 3; row C1).

The conversation agent and the pipeline's agents are all the fake server; an ``ask`` step is the real run
through the planner, validator and executor. The unit tests (``tests/unit/test_conversations.py``) cover the
loop with canned runs.
"""
from __future__ import annotations

import pytest

from nl2sql.hooks import Allow, NeedsApproval
from nl2sql.testing.fake_llm import Rule

from .recordings_chinook import RULES_COUNT_CUSTOMERS
from .test_approval_fake_llm import COUNT
from .test_result_paging_fake_llm import _env


class Review:
    """Customer data needs a person's yes; records the conversation id it was shown."""

    name = "review"
    points = frozenset({"run.before", "execute.before"})

    def __init__(self):
        self.seen = []

    def __call__(self, ctx):
        self.seen.append((ctx.point, ctx.conversation_id))
        return NeedsApproval(reason="Reads customer data") if "Customer" in (ctx.sql or "") else Allow()


def _asker():
    from nl2sql import UserContext

    return UserContext(user_id="ana", tenant_id="acme", roles=["analyst"])


def _ask_step(question):
    return {"call": {"name": "ask", "args": {"question": question}}}


@pytest.fixture
def make(demo_project, fake_llm, monkeypatch):
    engines = []

    def _make(script, *, hooks=()):
        from nl2sql import NL2SQL
        from nl2sql.config import load_config

        answers = list(script)
        prompts = []

        def model(prompt):
            prompts.append(prompt)
            return answers.pop(0) if answers else {"reply": "Done."}

        server, env = fake_llm([*RULES_COUNT_CUSTOMERS, Rule("ModelStep", model)])
        config = load_config(_env(monkeypatch, demo_project, env))
        if hooks:
            config = config.model_copy(update={"hooks": list(hooks)})
        engines.append(NL2SQL(config))
        return engines[-1], server, prompts

    yield _make
    for engine in engines:
        engine.close()


@pytest.mark.e2e
def test_a_question_runs_through_the_real_pipeline_and_the_turn_stores_no_rows(make):
    engine, server, prompts = make([_ask_step(COUNT), {"reply": "There are 59 customers."}])
    conversation = engine.conversations.start(user_context=_asker())

    turn = engine.conversations.ask(conversation.id, "How many customers?", user_context=_asker())

    assert turn.status == "answered" and turn.reply == "There are 59 customers."
    [step] = turn.steps
    assert step.status == "ok" and step.question == COUNT
    assert step.result.status == "success" and step.result.sub_queries[0].rows is None
    assert step.result.sub_queries[0].sql and step.result.explanation is not None
    names = [c["name"] for c in server.calls]
    assert names.count("ModelStep") == 2 and "AggregatedResponse" in names
    # The rows are still there, by the step's trace id.
    page = engine.results.page(step.trace_id, step.result.result_manifest[0].node_id)
    assert page.rows == [[59]]
    assert '"rows"' not in prompts[1]


@pytest.mark.e2e
def test_run_before_hook_sees_conversation_id(make):
    review = Review()
    engine, _server, _ = make([_ask_step(COUNT), {"reply": "ok"}], hooks=[review])
    conversation = engine.conversations.start(user_context=_asker())

    engine.conversations.ask(conversation.id, "How many customers?", user_context=_asker())
    engine.run_query(COUNT, user_context=_asker())

    run_before = [cid for point, cid in review.seen if point == "run.before"]
    assert run_before == [conversation.id, None]


@pytest.mark.e2e
def test_an_approval_ends_the_turn_and_retry_with_the_approval_runs_the_pinned_plan(make):
    from nl2sql import Approval, UserContext

    engine, server, _ = make([_ask_step(COUNT)], hooks=[Review()])
    conversation = engine.conversations.start(user_context=_asker())

    first = engine.conversations.ask(conversation.id, "How many customers?", user_context=_asker())

    assert first.status == "needs_approval" and first.reply is None
    [request] = first.steps[0].result.approvals_needed
    model_calls = [c["name"] for c in server.calls].count("ModelStep")
    approval = Approval(request=request, by=UserContext(user_id="root", roles=["admin"]))

    second = engine.conversations.retry(conversation.id, user_context=_asker(), approvals=[approval])

    assert second.kind == "retry" and second.status == "answered"
    assert second.steps[0].result.sub_queries[0].plan_source == "approved"
    assert [c["name"] for c in server.calls].count("ModelStep") == model_calls, "a retry makes no model call"


@pytest.mark.e2e
def test_the_stream_carries_the_steps_run_events_and_ends_with_the_stored_turn(make):
    engine, _server, _ = make([_ask_step(COUNT), {"reply": "59."}])
    conversation = engine.conversations.start(user_context=_asker())

    events = list(engine.conversations.stream(conversation.id, "How many customers?", user_context=_asker()))

    names = [e.name for e in events]
    assert names[0] == "run.started" and names[-1] == "conversation.turn"
    assert {"node.started", "result.manifest", "conversation.step", "run.finished"} <= set(names)
    assert not {"llm.call", "policy.decision", "hook.decision"} & set(names)
    stored = events[-1].data["stored"]
    assert stored["reply"] == "59." and stored["steps"][0]["result"]["sub_queries"][0]["rows"] is None
    assert events[-1].data["trace_id"] == stored["trace_id"]
