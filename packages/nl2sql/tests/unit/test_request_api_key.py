"""A key that belongs to one request: how the registry uses it, and drops it.

This is the mechanism hosted mode is built on (``nl2sql demo --hosted``). The
rule it has to keep is simple: a key bound to a request builds a client for
that request and is cached nowhere, so no later request -- and no other
visitor -- can be handed a client built with it.
"""
import pytest

from nl2sql.llm import LLMRegistry
from nl2sql.llm.models import AgentConfig
from nl2sql.llm.request_key import current_api_key, use_api_key
from nl2sql.secrets import SecretManager
from nl2sql.tracing.trace import collect_secrets

# Built at run time so no scanner mistakes a test fixture for a leaked key.
FIRST_KEY = "-".join(["sk", "proj", "requestkeyone" + "a" * 24 + "3b1c"])
SECOND_KEY = "-".join(["sk", "proj", "requestkeytwo" + "b" * 24 + "8e2f"])
ANTHROPIC_KEY = "-".join(["sk", "ant", "api03", "requestkey" + "c" * 24 + "5d9a"])


@pytest.fixture
def registry():
    reg = LLMRegistry(SecretManager())
    reg.register_llm(AgentConfig(provider="openai", model="gpt-4o", temperature=0.0,
                                 api_key="${env:OPENAI_API_KEY}", name="default"))
    return reg


def test_the_bound_key_builds_the_client_and_is_cached_nowhere(registry, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with use_api_key(FIRST_KEY):
        client = registry.get_llm("astplanner")

    assert client.openai_api_key.get_secret_value() == FIRST_KEY
    # Nothing was kept: the next caller cannot be handed this client.
    assert registry.llms == {}
    assert current_api_key() is None


def test_each_key_gets_its_own_client(registry, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with use_api_key(FIRST_KEY):
        first = registry.get_llm("astplanner")
    with use_api_key(SECOND_KEY):
        second = registry.get_llm("astplanner")

    assert first is not second
    assert second.openai_api_key.get_secret_value() == SECOND_KEY


def test_a_bound_key_does_not_disturb_the_process_key(registry, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "the-owner-key-for-this-process")

    own = registry.get_llm("default")
    with use_api_key(FIRST_KEY):
        theirs = registry.get_llm("default")
    again = registry.get_llm("default")

    assert own.openai_api_key.get_secret_value() == "the-owner-key-for-this-process"
    assert theirs.openai_api_key.get_secret_value() == FIRST_KEY
    # The process's own client is still the cached one, untouched.
    assert again is own


def test_a_key_from_another_provider_moves_the_agent_to_that_provider(registry):
    moved = LLMRegistry._for_key(registry._config_for("default"), ANTHROPIC_KEY)
    unmoved = LLMRegistry._for_key(registry._config_for("default"), FIRST_KEY)

    # A gpt- model means nothing to Anthropic, so the model moves with the key.
    assert (moved.provider, moved.model) == ("anthropic", "claude-opus-5")
    assert moved.base_url is None
    # A key from the configured provider changes nothing but the credential,
    # which is what keeps a pinned model, and a test's fake endpoint, in force.
    assert unmoved is registry._config_for("default")


def test_the_trace_redactor_knows_the_bound_key():
    ctx = object()

    with use_api_key(FIRST_KEY):
        during = collect_secrets(ctx)
    after = collect_secrets(ctx)

    assert FIRST_KEY in during
    assert FIRST_KEY not in after
