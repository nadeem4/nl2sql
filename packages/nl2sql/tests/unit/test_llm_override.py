"""``llm_override=``: one call's own keys and models (docs/architecture/wave_d.md, D210, row G2a).

The run is a small graph with the pipeline's node names (as ``test_stream_query`` uses), so the override is
observed from inside a real run, in the thread a real run uses. Keys are built at run time; nothing calls a model.
"""
import json
import os

import pytest
from langgraph.graph import END, StateGraph

import nl2sql
from nl2sql import MissingProviderKey
from nl2sql.api.query_api import QueryAPI
from nl2sql.execution.artifacts.results import ResultSummary
from nl2sql.llm.override import LLMOverride
from nl2sql.llm.request_key import current_api_keys, current_request_llms
from nl2sql.pipeline.nodes.aggregator.schemas import AggregatorResponse
from nl2sql.pipeline.nodes.answer_synthesizer.schemas import AnswerSynthesizerResponse
from nl2sql.pipeline.state import GraphState
from nl2sql.testing.runtime import fake_runtime
from nl2sql.tracing.trace import collect_secrets

KEY = "-".join(["sk", "proj", "overridekey" + "a" * 24 + "7c1d"])
ANTHROPIC_KEY = "-".join(["sk", "ant", "api03", "overridekey" + "b" * 24 + "4e2a"])
ANSWER = {"summary": "Rock leads.", "format_type": "text", "content": "Rock leads.", "warnings": []}
OVERRIDE = LLMOverride(keys={"openai": KEY, "anthropic": ANTHROPIC_KEY},
                       models={"ast_planner": ("anthropic", "claude-opus-5")})


def _graph(seen):
    def decomposer(state):
        bound = current_request_llms()
        seen.append({"keys": dict(bound.keys) if bound else None, "models": dict(bound.models) if bound else None,
                     "secrets": collect_secrets(None), "environ": dict(os.environ)})
        return {}

    graph = StateGraph(GraphState)
    graph.add_node("datasource_resolver", lambda state: {})
    graph.add_node("decomposer", decomposer)
    graph.add_node("aggregator", lambda state: {"aggregator_response": AggregatorResponse(
        results=[ResultSummary(node_id="sq_1", columns=["genre"], row_count=1)])})
    graph.add_node("answer_synthesizer",
                   lambda state: {"answer_synthesizer_response": AnswerSynthesizerResponse(final_answer=ANSWER)})
    graph.set_entry_point("datasource_resolver")
    for a, b in [("datasource_resolver", "decomposer"), ("decomposer", "aggregator"),
                 ("aggregator", "answer_synthesizer")]:
        graph.add_edge(a, b)
    graph.add_edge("answer_synthesizer", END)
    return graph.compile()


@pytest.fixture
def api(monkeypatch):
    seen = []
    monkeypatch.setattr("nl2sql.pipeline.runtime.build_graph", lambda ctx, execute=True: _graph(seen))
    query = QueryAPI(fake_runtime(artifact_store=None))
    query.seen = seen
    return query


def test_run_query_binds_the_override_for_the_run_only(api):
    before = dict(os.environ)

    api.run_query("Which genre sells most?", llm_override=OVERRIDE)

    [seen] = api.seen
    assert seen["keys"] == {"openai": KEY, "anthropic": ANTHROPIC_KEY}
    assert seen["models"] == {"ast_planner": ("anthropic", "claude-opus-5")}
    assert current_request_llms() is None and current_api_keys() == ()
    assert dict(os.environ) == before


def test_stream_query_carries_the_override_into_its_run_thread(api):
    events = list(api.stream_query("Which genre sells most?", llm_override=OVERRIDE))

    assert events[-1].name == "run.finished"
    assert api.seen[0]["keys"] == {"openai": KEY, "anthropic": ANTHROPIC_KEY}
    assert current_request_llms() is None


def test_a_call_without_an_override_binds_nothing(api):
    api.run_query("Which genre sells most?")

    assert api.seen[0]["keys"] is None


def test_override_never_reaches_the_environment_or_trace(api):
    published = []
    api._ctx.bus.subscribe(published.append)

    result = api.run_query("Which genre sells most?", llm_override=OVERRIDE)
    streamed = [e.model_dump_json() for e in api.stream_query("Which genre sells most?", llm_override=OVERRIDE)]

    [during, _] = api.seen
    # While it runs, the redactor knows the keys, so a trace masks them; afterwards nothing remembers them.
    assert {KEY, ANTHROPIC_KEY} <= during["secrets"]
    assert KEY not in during["environ"].values() and ANTHROPIC_KEY not in during["environ"].values()
    assert KEY not in collect_secrets(None) and ANTHROPIC_KEY not in collect_secrets(None)
    everything = result.model_dump_json() + json.dumps([e.model_dump_json() for e in published]) + "".join(streamed)
    assert KEY not in everything and ANTHROPIC_KEY not in everything


def test_model_override_without_its_key_is_refused(api):
    override = LLMOverride(keys={"openai": KEY}, models={"ast_planner": ("anthropic", "claude-opus-5")})

    with pytest.raises(MissingProviderKey) as refused:
        api.run_query("Which genre sells most?", llm_override=override)
    with pytest.raises(MissingProviderKey):
        api.stream_query("Which genre sells most?", llm_override=override)  # at the call, not the first next()

    assert (refused.value.agent, refused.value.provider) == ("ast_planner", "anthropic")
    assert api.seen == [], "nothing ran"
    assert KEY not in str(refused.value)


def test_a_model_override_is_never_moved_onto_the_operators_key():
    # No keys at all: a model choice has nothing of the caller's to run on.
    with pytest.raises(MissingProviderKey):
        QueryAPI(fake_runtime(artifact_store=None)).run_query(
            "q", llm_override=LLMOverride(models={"ast_planner": ("openai", "gpt-5.4")}))


def test_providers_carry_labels_and_key_shape():
    providers = {p["name"]: p for p in _providers()}

    openai = providers["openai"]
    assert openai["label"] == "OpenAI" and openai["key_variable"] == "OPENAI_API_KEY"
    assert "gpt-5.4" in openai["models"] and "claude-opus-5" in providers["anthropic"]["models"]
    assert providers["ollama"]["models"] == [] and providers["ollama"]["key_variable"] is None
    assert openai["key_shape"] == {"pattern": r"^[A-Za-z0-9_-]{20,}$", "min_length": 20, "prefix": None}
    assert providers["anthropic"]["key_shape"]["prefix"] == "sk-ant-"
    assert KEY not in json.dumps(list(providers.values()))


def _providers():
    from nl2sql.api.llm_api import LLMAPI
    from nl2sql.registry import Registry

    class Ctx:
        registry = Registry.default()

    return LLMAPI(Ctx()).providers()


def test_provider_for_key_and_environment_keys_are_exported(monkeypatch):
    for variable in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", ANTHROPIC_KEY)

    assert nl2sql.provider_for_key(ANTHROPIC_KEY) == "anthropic"
    assert nl2sql.provider_for_key(KEY) == "openai"
    assert nl2sql.provider_keys_in_environment() == {"anthropic": ANTHROPIC_KEY}
