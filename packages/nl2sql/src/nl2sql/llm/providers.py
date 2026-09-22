"""What the engine knows about providers and models beyond how to call them.

``PROVIDER_PRESETS`` (``llm/registry.py``) is the source of truth for a
provider's endpoint and key variable; everything here is derived from it or
sits next to it: which provider a raw key belongs to, the model a new config
starts with, the models whose parameters have been checked, and the agent name
each LLM node of the pipeline runs under. The CLI, the playground, evaluation
and the REST API all read it from here.
"""

from __future__ import annotations

from typing import Dict, Optional, Tuple

from .registry import PROVIDER_PRESETS

__all__ = [
    "ANTHROPIC_ENV",
    "DEFAULT_ANTHROPIC_MODEL",
    "DEFAULT_OPENAI_MODEL",
    "DEFAULT_OPENROUTER_MODEL",
    "KEYED_PROVIDERS",
    "LLM_AGENTS",
    "OPENAI_ENV",
    "OPENROUTER_ENV",
    "PROVIDER_KEYS",
    "UPSTREAMS",
    "VERIFIED_ANTHROPIC_MODELS",
    "VERIFIED_MODELS",
    "VERIFIED_OPENAI_MODELS",
    "default_model_for",
    "default_temperature_for",
    "env_var_for_key",
    "env_var_for_provider",
    "mask_key",
    "provider_for_key",
]

OPENAI_ENV = PROVIDER_PRESETS["openai"].api_key_env
OPENROUTER_ENV = PROVIDER_PRESETS["openrouter"].api_key_env
ANTHROPIC_ENV = PROVIDER_PRESETS["anthropic"].api_key_env

# The providers a key selects, and the variables they read it from, in preset order.
KEYED_PROVIDERS: Tuple[str, ...] = tuple(name for name, p in PROVIDER_PRESETS.items() if p.api_key_env)
PROVIDER_KEYS: Tuple[str, ...] = tuple(PROVIDER_PRESETS[name].api_key_env for name in KEYED_PROVIDERS)

# Where a keyed OpenAI-wire provider is reached. OpenAI's preset leaves
# base_url unset so the client keeps its own default (and OPENAI_BASE_URL);
# the demo's recording proxy needs the address spelled out.
_OPENAI_BASE_URL = "https://api.openai.com/v1"
UPSTREAMS: Dict[str, str] = {
    name: PROVIDER_PRESETS[name].base_url or _OPENAI_BASE_URL
    for name in KEYED_PROVIDERS if PROVIDER_PRESETS[name].wire == "openai"
}

# The pipeline's LLM nodes: the graph node name usage is recorded under, and
# the agent key the LLM config names it by. Anything not listed uses ``default``.
LLM_AGENTS: Dict[str, str] = {
    "datasource_resolver": "datasourceresolver",
    "decomposer": "decomposer",
    "ast_planner": "astplanner",
    "refiner": "refiner",
    "answer_synthesizer": "answersynthesizer",
}

# The model a config written by `setup` or `demo` starts with. gpt-5.4 accepts
# temperature=0 and had 500,000 tokens/min on the owner's account (gpt-4o: 30,000,
# below one question with a retry). OpenRouter keeps its own default: the
# matching OpenRouter id for gpt-5.4 has not been verified.
DEFAULT_OPENAI_MODEL = "gpt-5.4"
DEFAULT_OPENROUTER_MODEL = "anthropic/claude-sonnet-4.5"
# Anthropic's current flagship, and the default its own documentation gives.
DEFAULT_ANTHROPIC_MODEL = "claude-opus-5"

# The models the playground's settings panel offers per LLM node, each with the
# temperature a node on it is written with. The only list: the page reads it
# from GET /api/settings. A probe on 2026-09-20 sent the engine's exact
# parameters (temperature=0, seed=42, strict json_schema) to each on the
# owner's account. All accepted them, except that gpt-5.5 and gpt-5-mini
# answer temperature=0 with HTTP 400 and work only without it, so they run at
# the model's default temperature (None: nothing is sent) and vary more.
# "Verified" means the parameters are accepted, not that the model plans well;
# that is eval tier 2's question. OpenRouter and Ollama have no list yet: add
# one here under the provider's name.
VERIFIED_OPENAI_MODELS: Dict[str, Optional[float]] = {
    "gpt-5.4": 0.0,
    "gpt-5.4-mini": 0.0,
    "gpt-4.1": 0.0,
    "gpt-4.1-mini": 0.0,
    "gpt-4o": 0.0,
    "gpt-5.5": None,
    "gpt-5-mini": None,
}
# Claude, on Anthropic's own API. These are not from a probe on the owner's
# account (no key was used): the temperatures follow Anthropic's documented
# rules. Opus 5 and Sonnet 5 reject any sampling parameter with HTTP 400, so
# they run with none; Haiku 4.5 accepts temperature=0.
VERIFIED_ANTHROPIC_MODELS: Dict[str, Optional[float]] = {
    "claude-opus-5": None,
    "claude-sonnet-5": None,
    "claude-haiku-4-5": 0.0,
}
VERIFIED_MODELS: Dict[str, Dict[str, Optional[float]]] = {
    "openai": VERIFIED_OPENAI_MODELS,
    "anthropic": VERIFIED_ANTHROPIC_MODELS,
}

# Each provider's own documented prefix. Anthropic's ``sk-ant-`` and
# OpenRouter's ``sk-or-`` are both also ``sk-``, so they are checked before
# falling back to OpenAI, the provider every other path already defaults to.
_PREFIXES = (("sk-ant-", "anthropic"), ("sk-or-", "openrouter"))
_DEFAULT_MODELS = {"openai": DEFAULT_OPENAI_MODEL, "openrouter": DEFAULT_OPENROUTER_MODEL,
                   "anthropic": DEFAULT_ANTHROPIC_MODEL}


def provider_for_key(key: str) -> str:
    """Returns the provider a key belongs to, inferred from its prefix."""
    return next((provider for prefix, provider in _PREFIXES if (key or "").startswith(prefix)), "openai")


def default_model_for(provider: str) -> str:
    """Returns the model a newly written config uses for ``provider``."""
    return _DEFAULT_MODELS.get(provider, DEFAULT_OPENAI_MODEL)


def default_temperature_for(provider: str) -> Optional[float]:
    """The temperature a newly written config uses: none for Claude, whose default model rejects one."""
    return None if provider == "anthropic" else 0.0


def env_var_for_provider(provider: str) -> str:
    """Returns the environment variable ``provider`` reads its key from."""
    preset = PROVIDER_PRESETS.get(provider)
    return (preset.api_key_env if preset else None) or OPENAI_ENV


def env_var_for_key(key: str) -> str:
    """Returns the environment variable that provider reads its key from."""
    return env_var_for_provider(provider_for_key(key))


def mask_key(key: str) -> str:
    """Returns a form safe to print: the ``sk-`` marker and the last four.

    A key is never echoed whole, so anything too short to mask usefully comes
    back as ``***`` rather than as itself.
    """
    if not key or len(key) < 12:
        return "***"
    return f"{key[:3]}...{key[-4:]}"
