"""Provider inference and masking for a key handed to the CLI.

`setup --api-key` and `demo --api-key` both take a raw key and have to decide
which provider it belongs to and which environment variable to store it under.
These are the one place that decision is made.
"""
import pytest

from nl2sql.llm.providers import default_model_for, env_var_for_key, mask_key, provider_for_key

# Built at run time so no secret scanner mistakes a fixture for a leaked key.
ANTHROPIC_KEY = "-".join(["sk", "ant", "api03", "not", "a", "real", "key"])
OPENAI_KEY = "-".join(["sk", "proj", "not", "a", "real", "key"])
# An OpenAI key that merely contains "ant" further in must stay OpenAI.
OPENAI_KEY_WITH_ANT = "-".join(["sk", "antelope", "not", "a", "real", "key"])


@pytest.mark.parametrize(
    "key, provider",
    [(ANTHROPIC_KEY, "anthropic"), (OPENAI_KEY, "openai"), (OPENAI_KEY_WITH_ANT, "openai")],
)
def test_an_anthropic_key_is_told_apart_from_an_openai_key(key, provider):
    assert provider_for_key(key) == provider


def test_an_anthropic_key_is_stored_as_anthropic_api_key():
    assert env_var_for_key(ANTHROPIC_KEY) == "ANTHROPIC_API_KEY"


def test_the_default_claude_model_is_opus_5():
    assert default_model_for("anthropic") == "claude-opus-5"


@pytest.mark.parametrize(
    "key, provider",
    [
        ("sk-test-not-a-real-key", "openai"),
        ("sk-proj-test-not-a-real-key", "openai"),
        ("sk-or-v1-test-not-a-real-key", "openrouter"),
        ("sk-or-test", "openrouter"),
        ("", "openai"),
    ],
)
def test_provider_follows_the_key_shape(key, provider):
    assert provider_for_key(key) == provider


@pytest.mark.parametrize(
    "key, variable",
    [
        ("sk-test-not-a-real-key", "OPENAI_API_KEY"),
        ("sk-or-v1-test-not-a-real-key", "OPENROUTER_API_KEY"),
    ],
)
def test_env_var_follows_the_key_shape(key, variable):
    assert env_var_for_key(key) == variable


def test_mask_keeps_only_the_prefix_and_last_four():
    assert mask_key("sk-test-not-a-real-key-4f2a") == "sk-...4f2a"


@pytest.mark.parametrize("key", ["x", "short", "sk-or-v1-abcdefghijklmnop"])
def test_mask_never_contains_the_key_itself(key):
    masked = mask_key(key)
    assert masked != key
    assert key not in masked
