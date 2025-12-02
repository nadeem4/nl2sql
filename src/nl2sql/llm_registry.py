from __future__ import annotations

import dataclasses
import pathlib
from typing import Callable, Dict, Optional, List, Any

import yaml
from langchain_openai import ChatOpenAI
from nl2sql.settings import settings

TOKEN_LOG: List[Dict[str, object]] = []


def reset_usage() -> None:
    """Resets the token usage log."""
    TOKEN_LOG.clear()


def get_usage_summary() -> Dict[str, Dict[str, int]]:
    """
    Aggregates token usage by agent:model.

    Returns:
        A dictionary mapping "agent:model" keys to token counts.
    """
    totals = {"_all": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}}
    for entry in TOKEN_LOG:
        agent = entry.get("agent", "unknown")
        model = entry.get("model", "unknown")
        key = f"{agent}:{model}"
        if key not in totals:
            totals[key] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        for bucket in ("prompt_tokens", "completion_tokens", "total_tokens"):
            val = int(entry.get(bucket, 0))
            totals[key][bucket] += val
            totals["_all"][bucket] += val
    return totals


LLMCallable = Callable[[str], str]


@dataclasses.dataclass
class AgentConfig:
    """Configuration for a specific agent's LLM."""
    provider: str
    model: str
    temperature: float = 0
    api_key: Optional[str] = None


@dataclasses.dataclass
class LLMConfig:
    """Global LLM configuration."""
    default: AgentConfig
    agents: Dict[str, AgentConfig]


def parse_llm_config(data: Dict) -> LLMConfig:
    """
    Parses raw dictionary configuration into LLMConfig.

    Args:
        data: Raw configuration dictionary.

    Returns:
        LLMConfig object.
    """
    default_cfg = data.get("default", {})
    agents_cfg = data.get("agents", {})

    def to_agent(cfg: Dict, fallback: Dict) -> AgentConfig:
        return AgentConfig(
            provider=cfg.get("provider", fallback.get("provider", "openai")),
            model=cfg.get("model", fallback.get("model", "gpt-4o-mini")),
            temperature=float(cfg.get("temperature", fallback.get("temperature", 0))),
            api_key=cfg.get("api_key", fallback.get("api_key")),
        )

    default = to_agent(default_cfg, {})
    agents: Dict[str, AgentConfig] = {}
    for name, cfg in agents_cfg.items():
        agents[name] = to_agent(cfg, default_cfg)
    return LLMConfig(default=default, agents=agents)


def load_llm_config(path: pathlib.Path) -> LLMConfig:
    """
    Loads LLM configuration from a YAML file.

    Args:
        path: Path to the YAML config file.

    Returns:
        LLMConfig object.
    """
    if not path.exists():
        raise FileNotFoundError(f"LLM config not found: {path}")
    data = yaml.safe_load(path.read_text()) or {}
    return parse_llm_config(data)


class LLMRegistry:
    """
    Manages LLM instances and configurations for different agents.
    
    Handles:
    - Loading configuration.
    - Instantiating LLMs (currently only OpenAI).
    - Wrapping LLMs for token usage tracking.
    - Enforcing structured output for specific agents.
    """

    def __init__(self, config: LLMConfig, engine, row_limit: int):
        """
        Initializes the LLMRegistry.

        Args:
            config: LLM configuration.
            engine: Database engine type (unused here but kept for interface consistency).
            row_limit: Row limit (unused here but kept for interface consistency).
        """
        self.config = config
        self.engine = engine

    def _agent_cfg(self, agent: str) -> AgentConfig:
        return self.config.agents.get(agent) or self.config.default

    def _base_llm(self, agent: str) -> ChatOpenAI:
        cfg = self._agent_cfg(agent)
        if cfg.provider != "openai":
            raise ValueError(f"Unsupported LLM provider: {cfg.provider}")
        key = cfg.api_key or settings.openai_api_key
        if not key:
            raise RuntimeError("OPENAI_API_KEY is not set and no api_key provided in config.")
        return ChatOpenAI(model=cfg.model, temperature=cfg.temperature, api_key=key)

    def _wrap_usage(self, llm: ChatOpenAI, agent: str) -> LLMCallable:
        model_name = self._agent_cfg(agent).model

        def call(prompt: str) -> str:
            resp = llm.invoke(prompt)
            usage = getattr(resp, "usage_metadata", None)
            if usage:
                TOKEN_LOG.append(
                    {
                        "agent": agent,
                        "model": model_name,
                        "prompt_tokens": usage.get("prompt_tokens", 0),
                        "completion_tokens": usage.get("completion_tokens", 0),
                        "total_tokens": usage.get("total_tokens", 0),
                    }
                )
            return resp.content if hasattr(resp, "content") else str(resp)

        return call

    def _wrap_structured_usage(self, llm: ChatOpenAI, agent: str, schema: Any) -> LLMCallable:
        model_name = self._agent_cfg(agent).model
        structured_llm = llm.with_structured_output(schema, include_raw=True)

        def call(prompt: str) -> Any:
            resp = structured_llm.invoke(prompt)
            
            raw_msg = resp.get("raw")
            if raw_msg:
                usage = getattr(raw_msg, "usage_metadata", None)
                if usage:
                    TOKEN_LOG.append(
                        {
                            "agent": agent,
                            "model": model_name,
                            "prompt_tokens": usage.get("prompt_tokens", 0),
                            "completion_tokens": usage.get("completion_tokens", 0),
                            "total_tokens": usage.get("total_tokens", 0),
                        }
                    )
            
            return resp["parsed"]

        return call

    def intent_llm(self) -> LLMCallable:
        """Returns the LLM callable for the Intent agent."""
        from nl2sql.schemas import IntentModel
        llm = self._base_llm("intent")
        return self._wrap_structured_usage(llm, "intent", IntentModel)

    def planner_llm(self) -> LLMCallable:
        """Returns the LLM callable for the Planner agent."""
        from nl2sql.schemas import PlanModel
        llm = self._base_llm("planner")
        return self._wrap_structured_usage(llm, "planner", PlanModel)

    def generator_llm(self) -> LLMCallable:
        """Returns the LLM callable for the Generator agent."""
        from nl2sql.schemas import SQLModel
        llm = self._base_llm("generator")
        return self._wrap_structured_usage(llm, "generator", SQLModel)

    def summarizer_llm(self) -> LLMCallable:
        """Returns the LLM callable for the Summarizer agent."""

        llm = self._base_llm("planner") 
        return self._wrap_usage(llm, "summarizer")

    def llm_map(self) -> Dict[str, LLMCallable]:
        """Returns a dictionary of all agent LLM callables."""
        return {
            "intent": self.intent_llm(),
            "planner": self.planner_llm(),
            "generator": self.generator_llm(),
            "summarizer": self.summarizer_llm(),
            "_default": self.intent_llm(),
        }
