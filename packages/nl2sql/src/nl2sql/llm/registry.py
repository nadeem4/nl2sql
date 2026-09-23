import os
from threading import RLock
from typing import Any, Dict, NamedTuple, Optional

from langchain_core.language_models import BaseChatModel

from nl2sql.common.env_hint import active_env_file
from nl2sql.secrets import SecretManager
from .models import AgentConfig
from .request_key import current_api_key
from .wires import WIRES, Wire


class ProviderPreset(NamedTuple):
    """Endpoint, credential and wire-type defaults for one provider.

    Attributes:
        base_url: Endpoint the provider is reached on, or None to let the
            client resolve its own default.
        api_key_env: Environment variable named in errors and used as the
            last-resort source of the key.
        api_key_placeholder: Stand-in key for providers that authenticate
            nothing. Non-None means the provider needs no real credential.
        wire: The wire type the provider speaks, a key of
            ``nl2sql.llm.wires.WIRES``. Its adapter builds the client.
    """

    base_url: Optional[str]
    api_key_env: Optional[str]
    api_key_placeholder: Optional[str] = None
    wire: str = "openai"


# OpenAI, OpenRouter and Ollama all speak the OpenAI wire protocol, so one
# adapter serves all three; only the endpoint differs. A config-supplied
# ``base_url`` overrides the preset, which is what lets the same path serve
# vLLM, LiteLLM or any other OpenAI-compatible endpoint.
#
# Ollama needs no credential, but ChatOpenAI refuses to construct without an
# ``api_key`` (``openai.OpenAIError: Missing credentials``), so its preset
# supplies a placeholder the local daemon ignores.
#
# Anthropic speaks its own Messages API, so it has its own wire type: prompt
# caching and reliable tool-based structured output only work there.
PROVIDER_PRESETS: Dict[str, ProviderPreset] = {
    "openai": ProviderPreset(
        base_url=None,
        api_key_env="OPENAI_API_KEY",
    ),
    "openrouter": ProviderPreset(
        base_url="https://openrouter.ai/api/v1",
        api_key_env="OPENROUTER_API_KEY",
    ),
    "ollama": ProviderPreset(
        base_url="http://localhost:11434/v1",
        api_key_env=None,
        api_key_placeholder="ollama",
    ),
    "anthropic": ProviderPreset(
        base_url=None,
        api_key_env="ANTHROPIC_API_KEY",
        wire="anthropic",
    ),
}


def wire_for_provider(provider: str) -> Wire:
    """The adapter for the wire type ``provider`` speaks."""
    return WIRES[PROVIDER_PRESETS[provider].wire]


class LLMRegistry:

    def __init__(self, secret_manager: SecretManager):
        self.secret_manager = secret_manager
        self.llms = {}
        self._configs: Dict[str, AgentConfig] = {}
        self._lock = RLock()

    def register_llms(self, config: Dict[str, AgentConfig]):
        """Registers each agent under its key in ``config``.

        The key is the agent's name (``decomposer``, ``astplanner``, ...). An
        entry's own ``name`` field defaults to 'default', so trusting it made
        every unnamed entry under ``agents`` overwrite, and then be overwritten
        by, the default agent.
        """
        for key, agent in config.items():
            self.register_llm(agent if agent.name == key else agent.model_copy(update={"name": key}))

    def replace_llms(self, config: Dict[str, AgentConfig]):
        """Replaces every agent with those in ``config``, as one step.

        Unlike ``register_llms`` this also forgets agents the new config no
        longer names, so they fall back to 'default'. Every entry is validated
        before anything changes: an invalid config leaves the old one in place.
        Clients already handed out are untouched; the next ``get_llm`` builds
        from the new config.
        """
        named = {
            key: agent if agent.name == key else agent.model_copy(update={"name": key})
            for key, agent in config.items()
        }
        for agent in named.values():
            self._validate(agent)
        with self._lock:
            self._configs = named
            self.llms = {}

    @staticmethod
    def _validate(agent: AgentConfig) -> None:
        if agent.provider not in PROVIDER_PRESETS:
            raise ValueError(
                f"Unsupported LLM provider: {agent.provider}. "
                f"Valid providers are: {', '.join(sorted(PROVIDER_PRESETS))}."
            )
        if not agent.model or not agent.model.strip():
            raise ValueError(
                f"LLM agent '{agent.name}' has no model configured. "
                "Set 'model' for it in configs/llm.yaml."
            )

    def register_llm(self, agent: AgentConfig):
        """Validates an agent's configuration and records it for later use.

        The client itself is built on first ``get_llm`` so that constructing a
        context does not require credentials for every configured agent. What is
        genuinely misconfiguration - an unknown provider, a missing model - still
        fails here rather than mid-query.

        Args:
            agent: Configuration for one agent.

        Raises:
            ValueError: If the provider is unknown or the model is empty.
        """
        self._validate(agent)

        with self._lock:
            self._configs[agent.name] = agent
            # A re-registration replaces any client built from the old config.
            self.llms.pop(agent.name, None)

    def get_llm(self, name: str) -> BaseChatModel:
        """Returns the client for an agent, building it on first use.

        When the caller brought its own key (:mod:`nl2sql.llm.request_key`) the
        client is built from that key and cached nowhere, so it belongs to that
        request and to nothing else.

        Args:
            name: Agent name; falls back to the 'default' agent.

        Returns:
            BaseChatModel: The cached client for that agent (``ChatOpenAI``, or
            ``ChatAnthropic`` for the anthropic provider).

        Raises:
            ValueError: If neither the named agent nor a 'default' agent is
                registered, or if the provider needs an API key that cannot be
                resolved.
        """
        request_key = current_api_key()
        with self._lock:
            if request_key:
                return self._build_client(self._for_key(self._config_for(name), request_key),
                                          api_key=request_key)
            if name in self.llms:
                return self.llms[name]

            config = self._config_for(name)
            if config.name in self.llms:
                return self.llms[config.name]

            client = self._build_client(config)
            self.llms[config.name] = client
            return client

    def _config_for(self, name: str) -> AgentConfig:
        config = self._configs.get(name) or self._configs.get("default")
        if config is None:
            raise ValueError(
                f"No LLM named '{name}' is configured and no 'default' LLM has "
                "been registered. Add it to configs/llm.yaml (under 'agents', or "
                "as the 'default' agent)."
            )
        return config

    @staticmethod
    def _for_key(agent: AgentConfig, key: str) -> AgentConfig:
        """``agent`` as the key's own provider would run it.

        A key names its provider by its shape, and the configured model belongs
        to the configured provider: a ``gpt-`` model means nothing to Anthropic.
        So a key from another provider moves the agent to that provider's
        default model and endpoint; a key from the configured provider changes
        nothing but the credential, which keeps a pinned model (and a test's
        fake endpoint) in force.
        """
        from .providers import default_model_for, default_temperature_for, provider_for_key

        provider = provider_for_key(key)
        if provider == agent.provider:
            return agent
        return agent.model_copy(update={"provider": provider, "model": default_model_for(provider),
                                        "temperature": default_temperature_for(provider),
                                        "base_url": None, "api_key": None})

    def _build_client(self, agent: AgentConfig, api_key: Optional[str] = None) -> BaseChatModel:
        """Builds the client for one agent through its provider's wire adapter.

        Args:
            agent: Validated configuration for the agent.
            api_key: A key supplied by the caller, which wins over the config
                and the environment and is never stored.

        Returns:
            BaseChatModel: A client pointed at the configured endpoint.

        Raises:
            ValueError: If the wire's optional dependency (the anthropic
                extra) is missing.
        """
        preset = PROVIDER_PRESETS[agent.provider]
        api_key = api_key or self._resolve_api_key(agent, preset)

        base_url = agent.base_url or preset.base_url
        kwargs = {"base_url": base_url} if base_url else {}

        return WIRES[preset.wire].build_client(
            agent.model,
            agent.temperature,
            api_key=api_key,
            tags=[agent.name],
            **kwargs,
        )

    def _resolve_api_key(self, agent: AgentConfig, preset: ProviderPreset):
        """Resolves the API key for an agent, or explains what is missing.

        Args:
            agent: Configuration for the agent.
            preset: Preset for the agent's provider.

        Returns:
            The resolved key, the provider's placeholder for key-free providers,
            or the value of the provider's environment variable.

        Raises:
            ValueError: If the provider requires a key and none can be found.
        """
        try:
            resolved = self.secret_manager.resolve_object(agent.api_key)
        except ValueError:
            # An unresolvable "${env:...}" reference is the same situation as no
            # key at all, and is reported as such below.
            resolved = None

        if resolved is not None and resolved.get_secret_value():
            return resolved

        if preset.api_key_placeholder:
            return preset.api_key_placeholder

        env_var = self._api_key_env_var(agent, preset)
        from_env = os.environ.get(env_var) if env_var else None
        if from_env:
            return from_env

        raise ValueError(
            f"LLM agent '{agent.name}' uses provider '{agent.provider}', which "
            f"requires an API key, but none could be resolved. Set {env_var} in "
            f"{active_env_file()} (the env file for this run) or in the "
            "environment, or give the agent an 'api_key' in the LLM config file."
        )

    @staticmethod
    def _api_key_env_var(agent: AgentConfig, preset: ProviderPreset) -> Optional[str]:
        """Returns the environment variable the agent's key should come from.

        A config that already says ``${env:SOME_VAR}`` names its own variable;
        anything else falls back to the provider's conventional one.
        """
        raw = agent.api_key.get_secret_value() if agent.api_key else ""
        if raw and raw.startswith("${env:") and raw.endswith("}"):
            return raw[len("${env:") : -1]
        return preset.api_key_env

    def get_llm_config(self, name: str) -> Dict[str, Any]:
        with self._lock:
            if name not in self._configs:
                name = "default"
            config = self._configs[name]
            return config.model_dump(exclude={"api_key"})

    def list_llms(self) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            return {
                name: config.model_dump(exclude={"api_key"})
                for name, config in self._configs.items()
            }
