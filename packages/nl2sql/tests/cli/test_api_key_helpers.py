"""Provider inference and masking for a key handed to the CLI.

`setup --api-key` and `demo --api-key` both take a raw key and have to decide
which provider it belongs to and which environment variable to store it under.
These are the one place that decision is made.
"""
import pytest

from nl2sql.cli.common.api_key import env_var_for_key, mask_key, provider_for_key


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
