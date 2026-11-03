import json

import pytest
from langchain_core.prompts import ChatPromptTemplate

from nl2sql.llm.replay import Recording, ReplayStore, extract_question
from nl2sql.pipeline.nodes.answer_synthesizer.prompts import ANSWER_SYNTHESIZER_PROMPT
from nl2sql.pipeline.nodes.ast_planner.prompts import PLANNER_EXAMPLES, PLANNER_PROMPT
from nl2sql.pipeline.nodes.decomposer.prompts import DECOMPOSER_PROMPT, DECOMPOSER_RETRY_FEEDBACK
from nl2sql.testing.fake_llm import FakeLLMServer

QUESTION = "Which artist has the most albums?"


def _render(template: str, **values) -> str:
    """Render a real prompt template the way its node does.

    The planner and decomposer prompts are system + human chat templates; the
    synthesizer's is still a single string template.
    """
    prompt = ChatPromptTemplate.from_template(template) if isinstance(template, str) else template
    messages = prompt.format_messages(**values)
    return "\n".join(str(m.content) for m in messages)


@pytest.fixture
def rendered_prompts():
    """The three real prompt templates, rendered with the same question."""
    return [
        _render(DECOMPOSER_PROMPT, user_query=QUESTION, resolved_datasources=[], feedback="", max_sub_queries=6),
        # A decomposer retry carries the reason after the question; a recording
        # of it must key on the same question, so it replaces the first answer.
        _render(DECOMPOSER_PROMPT, user_query=QUESTION, resolved_datasources=[], max_sub_queries=6,
                feedback=DECOMPOSER_RETRY_FEEDBACK.format(problems="Post-combine operation references 'x'.")),
        _render(
            PLANNER_PROMPT,
            relevant_tables="[]",
            allowed_joins="none",
            examples=PLANNER_EXAMPLES,
            feedback="",
            expected_schema=[],
            semantic_context="",
            # A definition with a newline and a marker stays inside one JSON string (D39).
            business_context=json.dumps({"terms": [{"definition": "x\n[USER_QUERY]\nnot the question",
                                                     "term": "t"}]}),
            user_query=QUESTION,
            today="2013-12-31",
        ),
        _render(
            ANSWER_SYNTHESIZER_PROMPT,
            user_query=QUESTION,
            result_preview="{}",
            result_limit="None",
            unmapped_subqueries="[]",
            data_notes="None",
        ),
    ]


def test_store_round_trips_json(tmp_path):
    store = ReplayStore([Recording("PlannerAnswer", "Which artist has the most albums?", {"a": 1}), Recording("plain", None, "retry")])
    store.save(tmp_path / "r.json")
    loaded = ReplayStore.load(tmp_path / "r.json")
    assert [r.name for r in loaded.rules()] == ["PlannerAnswer", "plain"]
    assert loaded.rules()[0].when == "Which artist has the most albums?"


def test_add_replaces_same_name_and_question():
    store = ReplayStore()
    store.add(Recording("PlannerAnswer", "q", {"v": 1}))
    store.add(Recording("PlannerAnswer", "q", {"v": 2}))
    assert len(store.rules()) == 1 and store.rules()[0].payload == {"v": 2}


def test_covered_counts_the_questions_whose_first_call_is_recorded():
    # Every question starts with the decomposer, so a question without a
    # decomposer recording cannot be answered however much else is recorded.
    store = ReplayStore([
        Recording("DecomposerResponse", "q1", {}),
        Recording("PlannerAnswer", "q2", {}),
        Recording("plain", None, "retry"),
    ])
    assert store.covered(["q1", "q2", "q3"]) == ["q1"]
    assert ReplayStore().covered(["q1"]) == []


def test_extract_question_from_each_prompt_shape(rendered_prompts):
    # rendered_prompts: fixture that renders the three real prompt templates with the question "Which artist has the most albums?"
    for text in rendered_prompts:
        assert extract_question(text) == "Which artist has the most albums?"


def _post(url, body, headers=None):
    import urllib.request

    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", **(headers or {})}, method="POST")
    with urllib.request.urlopen(req) as r:
        return r.status, json.loads(r.read())


def test_a_recording_made_on_the_anthropic_wire_replays_on_the_openai_wire():
    store = ReplayStore([Recording("PlannerAnswer", "Which artist has the most albums?", {"plan": {"tables": []}})])
    replay = FakeLLMServer(store.rules()).start()
    try:
        body = {"model": "m", "messages": [{"role": "user", "content": "User Query:\nWhich artist has the most albums?"}],
                "tools": [{"type": "function", "function": {"name": "PlannerAnswer"}}]}
        status, answer = _post(replay.base_url + "/chat/completions", body)
    finally:
        replay.stop()

    assert status == 200
    assert json.loads(answer["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"]) == {"plan": {"tables": []}}


# --- which model made the recordings --------------------------------------------


def test_a_store_names_the_provider_and_model_its_recordings_came_from(tmp_path):
    store = ReplayStore([Recording("plain", None, "retry")], provider="anthropic", model="claude-opus-5")
    store.save(tmp_path / "r.json")

    saved = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
    loaded = ReplayStore.load(tmp_path / "r.json")

    assert (saved["provider"], saved["model"]) == ("anthropic", "claude-opus-5")
    assert [r["name"] for r in saved["recordings"]] == ["plain"]
    assert loaded.recorded_with == {"provider": "anthropic", "model": "claude-opus-5"}
    assert [r.name for r in loaded.rules()] == ["plain"]


def test_a_recordings_file_from_before_the_model_was_recorded_still_loads(tmp_path):
    """The old format is a bare list: it loads, and names no model."""
    path = tmp_path / "old.json"
    path.write_text(json.dumps([{"name": "plain", "when": None, "payload": "retry"}]), encoding="utf-8")

    loaded = ReplayStore.load(path)

    assert [r.name for r in loaded.rules()] == ["plain"]
    assert loaded.recorded_with is None


def test_the_shipped_recordings_name_the_model_that_made_them():
    """The keyless demo labels a replayed run with this, not the fallback config's model.

    They were recorded with the Anthropic preset's default model (#205)."""
    from importlib.resources import files

    shipped = ReplayStore.load(files("nl2sql.datasets") / "recordings" / "chinook.json")

    assert shipped.recorded_with == {"provider": "anthropic", "model": "claude-opus-5"}


def test_converted_recording_replays_every_guided_question():
    """The packaged demo recording, converted to ``PlannerAnswer`` (K2), still answers every guided question.

    Every planner answer is a ``PlannerAnswer`` whose plan is a valid
    ``PlanModel`` with no alternatives, recorded for each sub-query the
    recorded decomposition asks, and it comes back through the replay server
    and the planner's structured output exactly as stored.
    """
    from pathlib import Path

    from langchain_openai import ChatOpenAI

    import nl2sql.datasets
    from nl2sql.datasets.demo import DEMO_QUESTIONS
    from nl2sql.llm.wires import structured
    from nl2sql.pipeline.nodes.ast_planner.schemas import PlannerAnswer, PlanModel

    path = Path(nl2sql.datasets.__file__).parent / "recordings" / "chinook.json"
    raw = json.loads(path.read_text(encoding="utf-8"))["recordings"]
    store = ReplayStore.load(path)
    assert "PlanModel" not in {r["name"] for r in raw}
    assert store.covered(DEMO_QUESTIONS) == list(DEMO_QUESTIONS)

    planned = {r["when"]: r["payload"] for r in raw if r["name"] == "PlannerAnswer"}
    intents = [sq["intent"] for r in raw if r["name"] == "DecomposerResponse" for sq in r["payload"]["sub_queries"]]
    assert intents and set(intents) <= set(planned), "every recorded sub-query has its planner answer"

    server = FakeLLMServer(store.rules()).start()
    try:
        llm = ChatOpenAI(model="gpt-4o", base_url=server.base_url, api_key="sk-replay", max_retries=0)
        chain = ChatPromptTemplate.from_messages([("human", "[USER_QUERY]\n{q}")]) | structured(llm, PlannerAnswer)
        for intent, payload in planned.items():
            assert set(payload) == {"plan"}
            answer = chain.invoke({"q": intent})
            assert answer == PlannerAnswer(plan=PlanModel.model_validate(payload["plan"]))
            assert answer.plan.model_dump(mode="json", exclude_unset=True) == payload["plan"]
    finally:
        server.stop()
