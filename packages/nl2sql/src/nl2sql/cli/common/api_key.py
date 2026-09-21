"""One rule for a key handed to the CLI: which provider, which variable, and
how to show it without showing it.

``nl2sql setup --api-key`` and ``nl2sql demo --api-key`` both take a raw key on
the command line. Neither asks which provider it belongs to: OpenRouter issues
keys prefixed ``sk-or-`` and OpenAI does not, so the shape answers it. That is
the whole abstraction -- the provider preset table stays the source of truth
for everything else about a provider.
"""

from __future__ import annotations

from typing import Dict, Optional

__all__ = [
    "DEFAULT_OPENAI_MODEL",
    "DEFAULT_OPENROUTER_MODEL",
    "OPENAI_ENV",
    "OPENROUTER_ENV",
    "VERIFIED_MODELS",
    "VERIFIED_OPENAI_MODELS",
    "default_model_for",
    "env_var_for_key",
    "mask_key",
    "provider_for_key",
]

OPENAI_ENV = "OPENAI_API_KEY"
OPENROUTER_ENV = "OPENROUTER_API_KEY"

# The model a config written by `setup` or `demo` starts with. gpt-5.4 accepts
# temperature=0 and had 500,000 tokens/min on the owner's account (gpt-4o: 30,000,
# below one question with a retry). OpenRouter keeps its own default: the
# matching OpenRouter id for gpt-5.4 has not been verified.
DEFAULT_OPENAI_MODEL = "gpt-5.4"
DEFAULT_OPENROUTER_MODEL = "anthropic/claude-sonnet-4.5"

# The models the playground's settings panel offers per LLM node, each with the
# temperature a node on it is written with. The only list: the page reads it
# from GET /api/settings. A probe on 2026-09-20 sent the engine's exact
# parameters (temperature=0, seed=42, strict json_schema) to each on the
# owner's account. All accepted them, except that gpt-5.5 and gpt-5-mini
# answer temperature=0 with HTTP 400 and work only without it, so they run at
# the model's default temperature (None: nothing is sent) and vary more.
# "Verified" means the parameters are accepted, not that the model plans well;
# that is eval tier 2's question. Other providers have no list yet: add one
# here under the provider's name.
VERIFIED_OPENAI_MODELS: Dict[str, Optional[float]] = {
    "gpt-5.4": 0.0,
    "gpt-5.4-mini": 0.0,
    "gpt-4.1": 0.0,
    "gpt-4.1-mini": 0.0,
    "gpt-4o": 0.0,
    "gpt-5.5": None,
    "gpt-5-mini": None,
}
VERIFIED_MODELS: Dict[str, Dict[str, Optional[float]]] = {"openai": VERIFIED_OPENAI_MODELS}

# OpenRouter's own documented prefix. Anything else is treated as OpenAI,
# which is the provider every other path already defaults to.
_OPENROUTER_PREFIX = "sk-or-"


def provider_for_key(key: str) -> str:
    """Returns the provider a key belongs to, inferred from its prefix."""
    return "openrouter" if (key or "").startswith(_OPENROUTER_PREFIX) else "openai"


def default_model_for(provider: str) -> str:
    """Returns the model a newly written config uses for ``provider``."""
    return DEFAULT_OPENROUTER_MODEL if provider == "openrouter" else DEFAULT_OPENAI_MODEL


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
