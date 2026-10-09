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


def test_covered_counts_the_questions_whose_first_call_is_recorded():
    # Every question starts with the decomposer, so a question without a
    # decomposer recording cannot be answered however much else is recorded.
    store = ReplayStore([
        Recording("DecomposerResponse", "q1", {}),
        Recording("PlanModel", "q2", {}),
        Recording("plain", None, "retry"),
    ])
    assert store.covered(["q1", "q2", "q3"]) == ["q1"]
    assert ReplayStore().covered(["q1"]) == []


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


def _post(url, body, headers=None):
    import urllib.request

    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", **(headers or {})}, method="POST")
    with urllib.request.urlopen(req) as r:
        return r.status, json.loads(r.read())


def test_proxy_records_an_anthropic_upstream_with_the_key_in_x_api_key():
    """The demo's answers are recorded with a Claude model, on Anthropic's own wire."""
    upstream = FakeLLMServer([Rule("PlanModel", {"tables": []}), Rule("plain", "Fifty-nine.")]).start()
    store = ReplayStore()
    proxy = RecordingProxy(upstream.anthropic_base_url, "sk-ant-upstream", store, wire="anthropic").start()
    try:
        tool = {"model": "claude", "max_tokens": 10, "tools": [{"name": "PlanModel", "input_schema": {}}],
                "messages": [{"role": "user", "content": "User Query:\nWhich artist has the most albums?"}]}
        status, answer = _post(proxy.anthropic_base_url + "/v1/messages", tool, {"anthropic-version": "2023-06-01"})
        plain = {"model": "claude", "max_tokens": 10,
                 "messages": [{"role": "user", "content": [{"type": "text", "text": "User Query: How many?"}]}]}
        _post(proxy.anthropic_base_url + "/v1/messages", plain)
    finally:
        proxy.stop()
        upstream.stop()

    assert status == 200 and answer["content"][0]["type"] == "tool_use"
    assert all(call["api_key"] == "sk-ant-upstream" for call in upstream.calls)
    rules = {(r.name, r.when): r.payload for r in store.rules()}
    assert rules[("PlanModel", "Which artist has the most albums?")] == {"tables": []}
    assert rules[("plain", "How many?")] == "Fifty-nine."


def test_a_recording_made_on_the_anthropic_wire_replays_on_the_openai_wire():
    store = ReplayStore([Recording("PlanModel", "Which artist has the most albums?", {"tables": []})])
    replay = FakeLLMServer(store.rules()).start()
    try:
        body = {"model": "m", "messages": [{"role": "user", "content": "User Query:\nWhich artist has the most albums?"}],
                "tools": [{"type": "function", "function": {"name": "PlanModel"}}]}
        status, answer = _post(replay.base_url + "/chat/completions", body)
    finally:
        replay.stop()

    assert status == 200
    assert json.loads(answer["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"]) == {"tables": []}


def test_the_proxy_refuses_a_wire_it_cannot_speak():
    with pytest.raises(ValueError):
        RecordingProxy("http://127.0.0.1:9", "k", ReplayStore(), wire="gemini")
