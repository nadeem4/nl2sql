from nl2sql.secrets import SecretManager
from langchain_openai import ChatOpenAI
from .models import AgentConfig
from typing import Dict, Any
from threading import RLock

# OpenRouter is an OpenAI-compatible gateway, so it is served by ChatOpenAI
# with a different base URL rather than a dedicated client.
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# Providers reachable through the OpenAI-compatible client.
OPENAI_COMPATIBLE_PROVIDERS = ("openai", "openrouter")

class LLMRegistry:

    def __init__(self, secret_manager: SecretManager ):
        self.secret_manager = secret_manager
        self.llms = {}
        self._configs: Dict[str, AgentConfig] = {}
        self._lock = RLock()

    def register_llms(self, config: Dict[str, AgentConfig]):
        for agent in config.values():
            self.register_llm(agent)


    def register_llm(self, agent: AgentConfig):
        with self._lock:
            self._configs[agent.name] = agent
        if agent.provider in OPENAI_COMPATIBLE_PROVIDERS:
            self.register_openai_llm(agent)
        else:
            raise ValueError(f"Unsupported LLM provider: {agent.provider}")

    def register_openai_llm(self, agent: AgentConfig):
        """Builds a ChatOpenAI client for OpenAI or any OpenAI-compatible endpoint.

        A config-supplied ``base_url`` always wins, which is what lets this same
        path serve vLLM, LiteLLM or a local proxy. Otherwise ``openrouter``
        falls back to the OpenRouter gateway and ``openai`` keeps the stock
        endpoint that ChatOpenAI resolves on its own.
        """
        api_key = self.secret_manager.resolve_object(agent.api_key)

        base_url = agent.base_url
        if not base_url and agent.provider == "openrouter":
            base_url = OPENROUTER_BASE_URL

        kwargs = {"base_url": base_url} if base_url else {}
        llm = ChatOpenAI(
            model=agent.model,
            api_key=api_key,
            temperature=agent.temperature,
            tags=[agent.name],
            seed=42,
            **kwargs,
        )
        with self._lock:
            self.llms[agent.name] = llm


    
    def get_llm(self, name: str) -> ChatOpenAI:
        with self._lock:
            if name in self.llms:
                return self.llms[name]
            if "default" in self.llms:
                return self.llms["default"]
            raise ValueError(
                f"No LLM named '{name}' is configured and no 'default' LLM has "
                "been registered. Add it to configs/llm.yaml (under 'agents', or "
                "as the 'default' agent)."
            )
    

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

    