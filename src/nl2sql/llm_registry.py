from __future__ import annotations

import dataclasses
import time
import pathlib
from typing import Callable, Dict, Optional, List, Any

import yaml
from langchain_openai import ChatOpenAI
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult
from nl2sql.settings import settings



def get_usage_summary() -> Dict[str, Dict[str, int]]:
    """
    Aggregates token usage by agent:model.

    Returns:
        A dictionary mapping "agent:model" keys to token counts.
    """
    from nl2sql.metrics import TOKEN_LOG
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

    def __init__(self, config: LLMConfig):
        """
        Initializes the LLMRegistry.

        Args:
            config: LLM configuration.
        """
        self.config = config

    def _agent_cfg(self, agent: str) -> AgentConfig:
        return self.config.agents.get(agent) or self.config.default

    def _base_llm(self, agent: str) -> ChatOpenAI:
        cfg = self._agent_cfg(agent)
        if cfg.provider != "openai":
            raise ValueError(f"Unsupported LLM provider: {cfg.provider}")
        key = cfg.api_key or settings.openai_api_key
        if not key:
            raise RuntimeError("OPENAI_API_KEY is not set and no api_key provided in config.")
            
        # We use tags to identifying agent in the global PipelineMonitor
        return ChatOpenAI(model=cfg.model, temperature=cfg.temperature, api_key=key, tags=[agent])

    def get_llm(self, agent: str) -> ChatOpenAI:
        """
        Returns the raw ChatOpenAI object for a specific agent.
        Useful when custom wrapping (e.g., with_structured_output) is needed.
        """
        return self._base_llm(agent)

    def _wrap_structured_usage(self, llm: ChatOpenAI, schema: Any) -> LLMCallable:    
        structured_llm = llm.with_structured_output(schema)

        def call(prompt: str) -> Any:
            return structured_llm.invoke(prompt)

        return call

    def planner_llm(self) -> LLMCallable:
        """Returns the LLM callable for the Planner agent."""
        from nl2sql.nodes.planner.schemas import PlanModel
        llm = self._base_llm("planner")
        return self._wrap_structured_usage(llm, PlanModel)

    def canonicalizer_llm(self) -> LLMCallable:
        llm = self._base_llm("canonicalizer")
        return llm.invoke

    def summarizer_llm(self) -> LLMCallable:
        """Returns the LLM callable for the Summarizer agent."""
        llm = self._base_llm("summarizer") 
        return llm.invoke

    def decomposer_llm(self) -> LLMCallable:
        """Returns the LLM callable for the Decomposer agent."""
        from nl2sql.nodes.decomposer.schemas import DecomposerResponse
        llm = self._base_llm("decomposer")
        return self._wrap_structured_usage(llm, DecomposerResponse)

    def aggregator_llm(self) -> LLMCallable:
        """Returns the LLM callable for the Aggregator agent."""
        from nl2sql.nodes.aggregator.schemas import AggregatedResponse
        llm = self._base_llm("aggregator")
        return self._wrap_structured_usage(llm,  AggregatedResponse)

    def intent_classifier_llm(self) -> LLMCallable:
        """Returns the LLM callable for the Intent Classifier."""
        from nl2sql.nodes.intent.schemas import IntentResponse
        llm = self._base_llm("intent_classifier")
        return llm.with_structured_output(IntentResponse)

    def direct_sql_llm(self) -> LLMCallable:
        """Returns the LLM callable for the Direct SQL agent."""
        llm = self._base_llm("direct_sql")
        return llm.invoke

    def llm_map(self) -> Dict[str, LLMCallable]:
        """Returns a dictionary of all agent LLM callables."""
        return {
            "planner": self.planner_llm(),
            "summarizer": self.summarizer_llm(),
            "decomposer": self.decomposer_llm(),
            "aggregator": self.aggregator_llm(),
            "intent_classifier": self.intent_classifier_llm(),
            "direct_sql": self.direct_sql_llm(),
            "direct_sql": self.direct_sql_llm(),
            "_default": self.decomposer_llm(),
        }

    def get_usage_summary(self) -> Dict[str, Dict[str, int]]:
        """Returns the token usage summary."""
        return get_usage_summary()

    def get_token_log(self) -> List[Dict[str, Any]]:
        """Returns the raw token usage log."""
        return TOKEN_LOG
