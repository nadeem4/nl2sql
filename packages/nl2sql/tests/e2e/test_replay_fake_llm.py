"""The ``replay`` provider through the real pipeline: no key, no network, the packaged recordings (row G2b).

No fake server is needed: the replay provider is the model. The index is the demo project's, built locally.
"""
from __future__ import annotations

import pytest

from nl2sql import NL2SQL, UserContext, load_config
from nl2sql.datasets.demo import CHINOOK_QUESTIONS
from nl2sql.llm.replay import Recording, ReplayProvider, packaged_store
from nl2sql.registry import Registry

from .conftest import private_storage, project_config

ADMIN = UserContext(user_id="ana", roles=["admin"])
UNRECORDED = "How many tracks are longer than ten minutes?"


def _engine(demo_project, tmp_path, store=None):
    path = project_config(demo_project, "replay.yaml", llm={"provider": "replay", "model": "recorded"}, curated=True,
                          storage=private_storage(demo_project, tmp_path / "store"))
    registry = Registry.default()
    registry.add_llm("replay", ReplayProvider(store if store is not None else packaged_store()))
    return NL2SQL(load_config(path), registry=registry)


@pytest.mark.e2e
def test_replay_answers_a_recorded_question_through_the_pipeline(demo_project, tmp_path):
    with _engine(demo_project, tmp_path) as engine:
        result = engine.run_query(CHINOOK_QUESTIONS[0], user_context=ADMIN)

        assert result.status == "success", result.errors
        assert result.sub_queries[0].sql and result.final_answer
        assert {call.model for call in result.usage.calls} <= {packaged_store().model, None}

        missed = engine.run_query(UNRECORDED, user_context=ADMIN)

    assert missed.status == "error"
    assert [e["error_code"] for e in missed.errors] == ["REPLAY_MISS"]
    assert missed.errors[0]["message"] == "No recorded answer for this question. Add an API key to ask it live."


@pytest.mark.e2e
def test_recorded_follow_up_replays_the_models_steps(demo_project, tmp_path):
    """A conversation turn is two recorded model steps (the ask, then the reply) around the recorded pipeline."""
    store = packaged_store()
    text = '"Show me the first guided question"'
    store.add(Recording("ModelStep", f"{text} (turn 1, step 1)",
                        {"call": {"name": "ask", "args": {"question": CHINOOK_QUESTIONS[0]}}}))
    store.add(Recording("ModelStep", f"{text} (turn 1, step 2)", {"reply": "Here is the answer."}))

    with _engine(demo_project, tmp_path, store) as engine:
        conversation = engine.conversations.start(user_context=ADMIN)
        turn = engine.conversations.ask(conversation.id, "Show me the first guided question", user_context=ADMIN)

    assert turn.status == "answered" and turn.reply == "Here is the answer."
    [step] = turn.steps
    assert step.status == "ok" and step.question == CHINOOK_QUESTIONS[0]
    assert step.result.status == "success"


@pytest.mark.e2e
def test_every_recorded_question_replays_after_add_demo(tmp_path):
    """Try the demo on an empty project: whatever ``llm.recorded`` lists must run, with no REPLAY_MISS (D237)."""
    from nl2sql.config import Embeddings, default_llm, starter_config
    from nl2sql.datasets.demo import DEMO_QUESTIONS
    from nl2sql.llm.replay import REPLAY

    registry = Registry.default()
    registry.add_llm("replay", REPLAY)
    config = starter_config(llm=default_llm("replay"), path=tmp_path / "store").model_copy(
        update={"knowledge": [], "embeddings": Embeddings(provider="local")})
    with NL2SQL(config, registry=registry) as engine:
        engine.add_demo(user_context=ADMIN)
        listed = engine.llm.recorded(DEMO_QUESTIONS)
        assert listed
        failed = {}
        for question in listed:
            result = engine.run_query(question, user_context=ADMIN)
            if result.status != "success":
                failed[question] = [e.get("error_code") for e in result.errors]

    assert failed == {}


@pytest.mark.e2e
def test_every_recorded_question_still_replays_after_a_restart(tmp_path):
    """Try the demo on a project, then a new engine from the same folder: the queries and glossary came back (D237)."""
    from nl2sql.config import init_project, load_config
    from nl2sql.datasets.demo import DEMO_QUESTIONS
    from nl2sql.llm.replay import REPLAY

    init_project(tmp_path, provider="replay")
    registry = Registry.default()
    registry.add_llm("replay", REPLAY)
    with NL2SQL(load_config(tmp_path / "nl2sql.yaml"), registry=registry) as engine:
        engine.add_demo(user_context=ADMIN)

    with NL2SQL(load_config(tmp_path / "nl2sql.yaml"), registry=registry) as engine:
        assert any(str(k.path).endswith("demo-glossary.yaml") for k in engine._runtime.config.knowledge)
        listed = engine.llm.recorded(DEMO_QUESTIONS)
        assert listed
        failed = {}
        for question in listed:
            result = engine.run_query(question, user_context=ADMIN)
            if result.status != "success":
                failed[question] = [e.get("error_code") for e in result.errors]

    assert failed == {}
