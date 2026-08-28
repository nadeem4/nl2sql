import os
from threading import RLock
from typing import Any, Dict, NamedTuple, Optional

from langchain_openai import ChatOpenAI

from nl2sql.secrets import SecretManager
from .models import AgentConfig


class ProviderPreset(NamedTuple):
    """Endpoint and credential defaults for one OpenAI-compatible provider.

    Attributes:
        base_url: Endpoint the provider is reached on, or None to let the
            OpenAI client resolve its own default.
        api_key_env: Environment variable named in errors and used as the
            last-resort source of the key.
        api_key_placeholder: Stand-in key for providers that authenticate
            nothing. Non-None means the provider needs no real credential.
    """

    base_url: Optional[str]
    api_key_env: Optional[str]
    api_key_placeholder: Optional[str] = None


# OpenAI, OpenRouter and Ollama all speak the OpenAI wire protocol, so a single
# ChatOpenAI client serves all three; only the endpoint differs. A
# config-supplied ``base_url`` overrides the preset, which is what lets the same
# path serve vLLM, LiteLLM or any other OpenAI-compatible endpoint.
#
# Ollama needs no credential, but ChatOpenAI refuses to construct without an
# ``api_key`` (``openai.OpenAIError: Missing credentials``), so its preset
# supplies a placeholder the local daemon ignores.
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
}


class LLMRegistry:

    def __init__(self, secret_manager: SecretManager):
        self.secret_manager = secret_manager
        self.llms = {}
        self._configs: Dict[str, AgentConfig] = {}
        self._lock = RLock()

    def register_llms(self, config: Dict[str, AgentConfig]):
        for agent in config.values():
            self.register_llm(agent)

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

        with self._lock:
            self._configs[agent.name] = agent
            # A re-registration replaces any client built from the old config.
            self.llms.pop(agent.name, None)

    def get_llm(self, name: str) -> ChatOpenAI:
        """Returns the client for an agent, building it on first use.

        Args:
            name: Agent name; falls back to the 'default' agent.

        Returns:
            ChatOpenAI: The cached client for that agent.

        Raises:
            ValueError: If neither the named agent nor a 'default' agent is
                registered, or if the provider needs an API key that cannot be
                resolved.
        """
        with self._lock:
            if name in self.llms:
                return self.llms[name]

            config = self._configs.get(name) or self._configs.get("default")
            if config is None:
                raise ValueError(
                    f"No LLM named '{name}' is configured and no 'default' LLM has "
                    "been registered. Add it to configs/llm.yaml (under 'agents', or "
                    "as the 'default' agent)."
                )

            if config.name in self.llms:
                return self.llms[config.name]

            client = self._build_client(config)
            self.llms[config.name] = client
            return client

    def _build_client(self, agent: AgentConfig) -> ChatOpenAI:
        """Builds the ChatOpenAI client for one agent from its provider preset.

        Args:
            agent: Validated configuration for the agent.

        Returns:
            ChatOpenAI: A client pointed at the configured endpoint.
        """
        preset = PROVIDER_PRESETS[agent.provider]
        api_key = self._resolve_api_key(agent, preset)

        base_url = agent.base_url or preset.base_url
        kwargs = {"base_url": base_url} if base_url else {}

        return ChatOpenAI(
            model=agent.model,
            api_key=api_key,
            temperature=agent.temperature,
            tags=[agent.name],
            seed=42,
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
            "the environment, or give the agent an 'api_key' in configs/llm.yaml."
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
