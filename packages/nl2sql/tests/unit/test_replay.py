import json

import pytest
from langchain_core.prompts import ChatPromptTemplate

from nl2sql.llm.replay import Recording, RecordingProxy, ReplayStore, extract_question
from nl2sql.pipeline.nodes.answer_synthesizer.prompts import ANSWER_SYNTHESIZER_PROMPT
from nl2sql.pipeline.nodes.ast_planner.prompts import PLANNER_EXAMPLES, PLANNER_PROMPT
from nl2sql.pipeline.nodes.decomposer.prompts import DECOMPOSER_PROMPT
from nl2sql.testing.fake_llm import FakeLLMServer, Rule

QUESTION = "Which artist has the most albums?"


def _render(template: str, **values) -> str:
    """Render a real prompt template the way its node does."""
    messages = ChatPromptTemplate.from_template(template).format_messages(**values)
    return "\n".join(str(m.content) for m in messages)


@pytest.fixture
def rendered_prompts():
    """The three real prompt templates, rendered with the same question."""
    return [
        _render(DECOMPOSER_PROMPT, user_query=QUESTION, resolved_datasources=[]),
        _render(
            PLANNER_PROMPT,
            relevant_tables="[]",
            examples=PLANNER_EXAMPLES,
            feedback="",
            expected_schema=[],
            semantic_context="",
            user_query=QUESTION,
        ),
        _render(
            ANSWER_SYNTHESIZER_PROMPT,
            user_query=QUESTION,
            aggregated_result="{}",
            unmapped_subqueries="[]",
        ),
    ]


def test_store_round_trips_json(tmp_path):
    store = ReplayStore([Recording("PlanModel", "Which artist has the most albums?", {"a": 1}), Recording("plain", None, "retry")])
    store.save(tmp_path / "r.json")
    loaded = ReplayStore.load(tmp_path / "r.json")
    assert [r.name for r in loaded.rules()] == ["PlanModel", "plain"]
    assert loaded.rules()[0].when == "Which artist has the most albums?"


def test_add_replaces_same_name_and_question():
    store = ReplayStore()
    store.add(Recording("PlanModel", "q", {"v": 1}))
    store.add(Recording("PlanModel", "q", {"v": 2}))
    assert len(store.rules()) == 1 and store.rules()[0].payload == {"v": 2}


def test_extract_question_from_each_prompt_shape(rendered_prompts):
    # rendered_prompts: fixture that renders the three real prompt templates with the question "Which artist has the most albums?"
    for text in rendered_prompts:
        assert extract_question(text) == "Which artist has the most albums?"


def test_proxy_records_what_the_upstream_answered():
    upstream = FakeLLMServer([Rule("PlanModel", {"tables": []})]).start()
    store = ReplayStore()
    proxy = RecordingProxy(upstream.base_url, "sk-upstream", store).start()
    try:
        import urllib.request
        body = {"model": "m", "messages": [{"role": "user", "content": "User Query:\nWhich artist has the most albums?"}],
                "tools": [{"type": "function", "function": {"name": "PlanModel"}}]}
        req = urllib.request.Request(proxy.base_url + "/chat/completions", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req) as r:
            assert r.status == 200
    finally:
        proxy.stop()
        upstream.stop()
    rule = store.rules()[0]
    assert rule.name == "PlanModel" and rule.when == "Which artist has the most albums?" and rule.payload == {"tables": []}
