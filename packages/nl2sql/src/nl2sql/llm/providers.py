"""What the engine knows about providers and models beyond how to call them.

``PROVIDER_PRESETS`` (``llm/registry.py``) is the source of truth for a
provider's endpoint and key variable; everything here is derived from it or
sits next to it: which provider a raw key belongs to, the model a new config
starts with, the models whose parameters have been checked, and the agent name
each LLM node of the pipeline runs under. The CLI, the playground, evaluation
and the REST API all read it from here.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, Dict, Optional, Protocol, Tuple, runtime_checkable

from .registry import PROVIDER_PRESETS, ProviderPreset
from .wires import WIRES
from .wires.anthropic import EXTRA_HINT

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

__all__ = [
    "ANTHROPIC",
    "ANTHROPIC_ENV",
    "LLMProvider",
    "LLM_PROVIDERS",
    "OLLAMA",
    "OPENAI",
    "OPENROUTER",
    "ResolvedAgent",
    "api_key_from_env",
    "KEY_SHAPE_MESSAGE",
    "DEFAULT_ANTHROPIC_MODEL",
    "DEFAULT_OPENAI_MODEL",
    "DEFAULT_OPENROUTER_MODEL",
    "KEYED_PROVIDERS",
    "LLM_AGENTS",
    "OPENAI_ENV",
    "OPENROUTER_ENV",
    "PROVIDER_LABELS",
    "PROVIDER_KEYS",
    "UPSTREAMS",
    "ANTHROPIC_UPSTREAM",
    "VERIFIED_ANTHROPIC_MODELS",
    "VERIFIED_MODELS",
    "VERIFIED_OPENAI_MODELS",
    "default_model_for",
    "default_temperature_for",
    "env_var_for_key",
    "env_var_for_provider",
    "looks_like_api_key",
    "mask_key",
    "provider_for_key",
]

# Letters, digits, '-' and '_' only, and at least twenty of them. A key is
# written into a dotenv file by the CLI and carried in an HTTP header by the
# hosted demo, so nothing that could end a line, start a comment or split a
# header gets through. The one shape check: the CLI, the settings panel and
# hosted mode all read it from here.
_KEY_SHAPE = re.compile(r"^[A-Za-z0-9_-]{20,}$")

# What to tell someone whose key does not match, without quoting what they sent.
KEY_SHAPE_MESSAGE = (
    "That does not look like an API key: expected 20 or more letters, digits, "
    "'-' or '_', with no spaces."
)


def looks_like_api_key(key: Optional[str]) -> bool:
    """True when ``key`` has the shape every supported provider's keys share."""
    return bool(_KEY_SHAPE.match((key or "").strip()))

# Each provider as a person names it: in the settings panel, and in the message
# of an error the provider caused (``nl2sql.llm.failures``).
PROVIDER_LABELS: Dict[str, str] = {"openai": "OpenAI", "anthropic": "Anthropic", "openrouter": "OpenRouter",
                                   "ollama": "Ollama"}

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

# Where Anthropic's Messages API is reached; the client appends /v1/messages.
# The recording proxy forwards Claude's calls here when the demo's answers are
# recorded with an Anthropic key.
ANTHROPIC_UPSTREAM = "https://api.anthropic.com"

# The pipeline's LLM nodes: the graph node name usage is recorded under, and
# the agent key the LLM config names it by. Anything not listed uses ``default``.
LLM_AGENTS: Dict[str, str] = {
    "curated_matcher": "curatedmatcher",
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


def api_key_from_env(provider: str) -> Optional[str]:
    """The key in ``provider``'s own variable, when it has one and it is set.

    The fallback when the config gives no key. Provider key variables are read
    here, in ``nl2sql/llm/``, and nowhere else: the embeddings providers ask
    this function too.
    """
    preset = PROVIDER_PRESETS.get(provider)
    return os.environ.get(preset.api_key_env) if preset and preset.api_key_env else None


@dataclass(frozen=True)
class ResolvedAgent:
    """One agent's LLM settings, with its key already resolved.

    ``api_key`` is the value, never a ``${provider:key}`` reference. None means
    the provider's own variable (or no key, for a provider that needs none).
    """

    name: str
    model: str
    temperature: Optional[float]
    api_key: Optional[str] = None
    base_url: Optional[str] = None


@runtime_checkable
class LLMProvider(Protocol):
    """An LLM provider: the code behind ``provider: <name>`` in the LLM config.

    Registered under the ``nl2sql.llms`` entry point group, or in code with
    ``Registry.add_llm``. The registry checks ``interface_version`` and finds
    each of ``requires`` before the provider is used, reporting
    ``install_hint`` when one is missing.
    """

    interface_version: ClassVar[int]
    label: str
    api_key_env: Optional[str]
    requires: Tuple[str, ...]
    install_hint: str

    def create(self, agent: ResolvedAgent) -> "BaseChatModel":
        """The chat client for ``agent``. Builds it without calling the model."""
        ...


_WIRE_REQUIRES = {"openai": ("langchain_openai",), "anthropic": ("langchain_anthropic",)}
_WIRE_HINTS = {"openai": 'pip install "nl2sql-engine"', "anthropic": EXTRA_HINT.split(": ", 1)[1]}


@dataclass(frozen=True)
class _PresetProvider:
    """A built-in provider, made from its entry in ``PROVIDER_PRESETS``."""

    interface_version: ClassVar[int] = 1
    name: str

    @property
    def preset(self) -> ProviderPreset:
        # Read on every use: PROVIDER_PRESETS stays the one table.
        return PROVIDER_PRESETS[self.name]

    @property
    def label(self) -> str:
        return PROVIDER_LABELS[self.name]

    @property
    def api_key_env(self) -> Optional[str]:
        return self.preset.api_key_env

    @property
    def requires(self) -> Tuple[str, ...]:
        return _WIRE_REQUIRES[self.preset.wire]

    @property
    def install_hint(self) -> str:
        return _WIRE_HINTS[self.preset.wire]

    def create(self, agent: ResolvedAgent) -> "BaseChatModel":
        """Raises ValueError naming the key variable when there is no key."""
        api_key = agent.api_key or self.preset.api_key_placeholder or api_key_from_env(self.name)
        if not api_key:
            raise ValueError(f"LLM agent '{agent.name}' uses provider '{self.name}', which requires an "
                             f"API key, but none was given. Set {self.api_key_env} or give the agent an "
                             "'api_key'.")
        base_url = agent.base_url or self.preset.base_url
        kwargs = {"base_url": base_url} if base_url else {}
        return WIRES[self.preset.wire].build_client(agent.model, agent.temperature, api_key=api_key,
                                                    tags=[agent.name], **kwargs)


# The built-in providers, one per preset: the ``nl2sql.llms`` entry points name these.
LLM_PROVIDERS: Dict[str, LLMProvider] = {name: _PresetProvider(name) for name in PROVIDER_PRESETS}
OPENAI = LLM_PROVIDERS["openai"]
ANTHROPIC = LLM_PROVIDERS["anthropic"]
OPENROUTER = LLM_PROVIDERS["openrouter"]
OLLAMA = LLM_PROVIDERS["ollama"]
