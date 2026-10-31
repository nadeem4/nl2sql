"""The ``replay`` provider (docs/architecture/wave_d.md, D209, row G2b).

``replay`` answers from recorded model output, with no key and no network, so
the demo and a keyless Try-the-demo run the real pipeline. These tests build the
provider on a small store; the packaged one is covered by ``test_replay_covered``.
"""
from __future__ import annotations

import pytest
from pydantic import BaseModel

import nl2sql
from nl2sql import ErrorCode, ReplayMiss
from nl2sql.api.llm_api import LLMAPI
from nl2sql.llm.failures import classify_provider_error, provider_error
from nl2sql.llm.providers import ResolvedAgent, describe_providers
from nl2sql.llm.replay import REPLAY, Recording, ReplayProvider, ReplayStore
from nl2sql.llm.wires import structured
from nl2sql.registry import Registry
from nl2sql.testing.runtime import fake_runtime

QUESTION = "How many customers are there?"
OTHER = "Which genre sold the most?"


class Thing(BaseModel):
    count: int


def _store():
    return ReplayStore(
        [Recording("Thing", QUESTION, {"count": 59}),
         Recording("Thing", None, {"count": 0}),
         Recording("plain", QUESTION, "There are 59.")],
        provider="anthropic", model="claude-opus-5")


def _client(store=None, model="gpt-test"):
    return ReplayProvider(store or _store()).create(ResolvedAgent(name="default", model=model, temperature=None))


def _prompt(question):
    return f"Plan this.\n\n[USER_QUERY]\n{question}\n"


def test_replay_answers_a_recorded_structured_question():
    """The question in the prompt picks the recording; the answer parses as the schema."""
    answer = structured(_client(), Thing).invoke(_prompt(QUESTION))

    assert answer == Thing(count=59)


def test_a_specific_recording_wins_over_the_catch_all():
    assert structured(_client(), Thing).invoke(_prompt(OTHER)) == Thing(count=0)


def test_unrecorded_question_is_replay_miss():
    """No recording and no catch-all: a ReplayMiss, which every caller reads as REPLAY_MISS."""
    store = ReplayStore([Recording("Thing", QUESTION, {"count": 59})])
    chain = structured(_client(store), Thing)

    with pytest.raises(ReplayMiss) as miss:
        chain.invoke(_prompt(OTHER))

    assert miss.value.code == "REPLAY_MISS" and miss.value.status == 422
    failure = classify_provider_error(miss.value)
    assert failure.code is ErrorCode.REPLAY_MISS
    assert failure.message == "No recorded answer for this question. Add an API key to ask it live."
    assert provider_error("decomposer", miss.value).error_code is ErrorCode.REPLAY_MISS
    # Found through a chain too: a node wraps what it caught.
    try:
        try:
            raise miss.value
        except ReplayMiss as inner:
            raise RuntimeError("node failed") from inner
    except RuntimeError as outer:
        assert classify_provider_error(outer).code is ErrorCode.REPLAY_MISS


def test_a_plain_call_replays_text():
    reply = _client().invoke(_prompt(QUESTION))

    assert reply.content == "There are 59."


def test_replay_usage_names_the_recorded_model():
    """The model that made the recordings, not the one the config names, labels the call."""
    reply = _client(model="gpt-test").invoke(_prompt(QUESTION))

    assert reply.response_metadata["model_name"] == "claude-opus-5"
    assert reply.response_metadata["model_provider"] == "replay"
    # A store that names no model falls back to the configured one.
    bare = _client(ReplayStore([Recording("plain", None, "ok")]), model="gpt-test")
    assert bare.invoke("hi").response_metadata["model_name"] == "gpt-test"


def test_replay_is_a_registered_provider_that_needs_no_key():
    registry = Registry.empty()
    registry.add_llm("replay", REPLAY)

    provider = registry.load("llm", "replay")

    assert provider.api_key_env is None and provider.requires == () and provider.interface_version == 2
    described = {p["name"]: p for p in describe_providers(registry)}
    assert described["replay"]["label"] == "Recorded answers"
    assert described["replay"]["key_variable"] is None and described["replay"]["key_shape"] is None


def test_recorded_lists_covered_questions():
    """``engine.llm.recorded`` filters questions to those the replay provider can answer, in the order asked."""
    store = ReplayStore([Recording("DecomposerResponse", QUESTION, {})])
    registry = Registry.empty()
    registry.add_llm("replay", ReplayProvider(store))
    api = LLMAPI(fake_runtime(registry=registry))

    assert api.recorded([OTHER, QUESTION]) == [QUESTION]
    assert api.recorded([]) == []


def test_miss_is_exported_and_is_not_a_value_error():
    assert nl2sql.ReplayMiss is ReplayMiss and not issubclass(ReplayMiss, ValueError)


def test_override_key_replaces_replay_for_one_call(monkeypatch):
    """A caller's own key moves the call off ``replay`` onto the key's provider, for that call only."""
    from nl2sql.config import LLM
    from nl2sql.llm.override import LLMOverride
    from nl2sql.llm.providers import LLM_PROVIDERS
    from nl2sql.llm.registry import LLMRegistry, agents_from_config
    from nl2sql.llm.request_key import use_llm_override
    from nl2sql.secrets import SecretManager

    registry = Registry.empty()
    registry.add_llm("replay", REPLAY)
    registry.add_llm("anthropic", LLM_PROVIDERS["anthropic"])
    llms = LLMRegistry(SecretManager(), registry)
    llms.replace_llms(agents_from_config(LLM(provider="replay", model="recorded")))
    key = "-".join(["sk", "ant", "api03", "replaykey" + "c" * 24 + "5f1b"])

    assert type(llms.get_llm("default")).__name__ == "ReplayChatModel"
    with use_llm_override(LLMOverride(keys={"anthropic": key})):
        assert type(llms.get_llm("default")).__name__ != "ReplayChatModel"
    assert type(llms.get_llm("default")).__name__ == "ReplayChatModel"
