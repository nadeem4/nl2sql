"""Keys that belong to one request: how the registry uses them, and drops them.

This is the mechanism hosted mode is built on (``nl2sql demo --hosted``). The
rule it has to keep is simple: a key bound to a request builds a client for
that request and is cached nowhere, so no later request -- and no other
visitor -- can be handed a client built with it.

A request may bring one key per provider and a model per step; which key each
step is built with is :meth:`RequestLLMs.resolve`, and it is tested here.
"""
import pytest

from nl2sql.llm import LLMRegistry
from nl2sql.llm.models import AgentConfig
from nl2sql.llm.request_key import (
    MissingProviderKey,
    RequestLLMs,
    current_api_key,
    current_api_keys,
    use_api_key,
    use_request_llms,
)
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


# --- a key per provider, and a model per step --------------------------------------


def test_a_step_with_a_choice_takes_that_providers_key():
    llms = RequestLLMs(keys={"openai": FIRST_KEY, "anthropic": ANTHROPIC_KEY},
                       models={"astplanner": ("anthropic", "claude-opus-5")})

    assert llms.resolve("astplanner", "openai") == ("anthropic", "claude-opus-5", ANTHROPIC_KEY)
    # A step with no choice of its own stays where the config put it.
    assert llms.resolve("decomposer", "openai") == (None, None, FIRST_KEY)


def test_a_step_whose_chosen_provider_has_no_key_names_both():
    llms = RequestLLMs(keys={"openai": FIRST_KEY},
                       models={"astplanner": ("anthropic", "claude-opus-5")})

    with pytest.raises(MissingProviderKey) as raised:
        llms.resolve("astplanner", "openai")

    assert raised.value.agent == "astplanner"
    assert raised.value.provider == "anthropic"
    # The refusal says what is missing and quotes no key.
    assert FIRST_KEY not in str(raised.value)


def test_one_key_and_no_choices_is_exactly_what_it_was():
    llms = RequestLLMs.from_key(ANTHROPIC_KEY)

    # The key names its own provider, and every step follows it.
    assert llms.keys == {"anthropic": ANTHROPIC_KEY}
    assert llms.resolve("astplanner", "openai") == (None, None, ANTHROPIC_KEY)


def test_a_step_with_no_choice_prefers_the_configured_providers_key():
    llms = RequestLLMs(keys={"openai": FIRST_KEY, "anthropic": ANTHROPIC_KEY}, models={})

    assert llms.resolve("decomposer", "anthropic") == (None, None, ANTHROPIC_KEY)
    assert llms.resolve("decomposer", "openai") == (None, None, FIRST_KEY)


def test_a_step_with_no_choice_and_no_key_for_its_provider_names_it():
    llms = RequestLLMs(keys={"openai": FIRST_KEY, "anthropic": ANTHROPIC_KEY}, models={})

    with pytest.raises(MissingProviderKey) as raised:
        llms.resolve("decomposer", "openrouter")

    assert (raised.value.agent, raised.value.provider) == ("decomposer", "openrouter")


def test_a_chosen_model_on_the_configured_provider_keeps_the_configured_endpoint(registry):
    config = registry._config_for("default").model_copy(update={"base_url": "http://fake/v1"})
    llms = RequestLLMs(keys={"openai": FIRST_KEY}, models={"astplanner": ("openai", "gpt-4.1")})

    moved, key = LLMRegistry._for_request(config, "astplanner", llms)

    assert (moved.provider, moved.model, key) == ("openai", "gpt-4.1", FIRST_KEY)
    # Same provider, so the endpoint the config pinned is still in force.
    assert moved.base_url == "http://fake/v1"
    # And the temperature moves with the model.
    assert moved.temperature == 0.0


def test_a_chosen_model_on_another_provider_drops_the_endpoint_and_the_key_reference(registry):
    config = registry._config_for("default").model_copy(update={"base_url": "http://fake/v1"})
    llms = RequestLLMs(keys={"openai": FIRST_KEY, "anthropic": ANTHROPIC_KEY},
                       models={"astplanner": ("anthropic", "claude-opus-5")})

    moved, key = LLMRegistry._for_request(config, "astplanner", llms)

    assert (moved.provider, moved.model, key) == ("anthropic", "claude-opus-5", ANTHROPIC_KEY)
    # OpenAI's endpoint means nothing to Anthropic, and neither does its key
    # reference: the key for this call is the one the caller supplied.
    assert moved.base_url is None and moved.api_key is None
    # Claude Opus 5 rejects any temperature, so none is sent.
    assert moved.temperature is None


def test_every_bound_key_reaches_the_redactor_and_none_of_them_outlives_the_block():
    llms = RequestLLMs(keys={"openai": FIRST_KEY, "anthropic": ANTHROPIC_KEY}, models={})

    with use_request_llms(llms):
        assert set(current_api_keys()) == {FIRST_KEY, ANTHROPIC_KEY}
        # No key was sent without naming a provider, so there is no fallback.
        assert current_api_key() is None
        during = collect_secrets(object())

    assert current_api_keys() == ()
    assert {FIRST_KEY, ANTHROPIC_KEY} <= during
    assert not ({FIRST_KEY, ANTHROPIC_KEY} & collect_secrets(object()))
