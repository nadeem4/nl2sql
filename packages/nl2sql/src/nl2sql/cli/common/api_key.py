"""One rule for a key handed to the CLI: which provider, which variable, and
how to show it without showing it.

``nl2sql setup --api-key`` and ``nl2sql demo --api-key`` both take a raw key on
the command line. Neither asks which provider it belongs to: OpenRouter issues
keys prefixed ``sk-or-`` and OpenAI does not, so the shape answers it. That is
the whole abstraction -- the provider preset table stays the source of truth
for everything else about a provider.
"""

from __future__ import annotations

__all__ = ["OPENAI_ENV", "OPENROUTER_ENV", "env_var_for_key", "mask_key", "provider_for_key"]

OPENAI_ENV = "OPENAI_API_KEY"
OPENROUTER_ENV = "OPENROUTER_API_KEY"

# OpenRouter's own documented prefix. Anything else is treated as OpenAI,
# which is the provider every other path already defaults to.
_OPENROUTER_PREFIX = "sk-or-"


def provider_for_key(key: str) -> str:
    """Returns the provider a key belongs to, inferred from its prefix."""
    return "openrouter" if (key or "").startswith(_OPENROUTER_PREFIX) else "openai"


def env_var_for_key(key: str) -> str:
    """Returns the environment variable that provider reads its key from."""
    return OPENROUTER_ENV if provider_for_key(key) == "openrouter" else OPENAI_ENV


def mask_key(key: str) -> str:
    """Returns a form safe to print: the ``sk-`` marker and the last four.

    A key is never echoed whole, so anything too short to mask usefully comes
    back as ``***`` rather than as itself.
    """
    if not key or len(key) < 12:
        return "***"
    return f"{key[:3]}...{key[-4:]}"
