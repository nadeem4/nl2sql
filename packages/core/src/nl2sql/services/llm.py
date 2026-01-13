from __future__ import annotations

import dataclasses
import time
import pathlib
from typing import Callable, Dict, Optional, List, Any

import yaml
from langchain_openai import ChatOpenAI
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult
from nl2sql.common.settings import settings



def get_usage_summary() -> Dict[str, Dict[str, int]]:
    """
    Aggregates token usage by agent:model.

    Returns:
        A dictionary mapping "agent:model" keys to token counts.
    """
    from nl2sql.common.metrics import TOKEN_LOG
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


from nl2sql.configs import LLMFileConfig, AgentConfig

def parse_llm_config(data: Dict) -> LLMFileConfig:
    """
    Parses raw dictionary configuration into LLMFileConfig using Pydantic.

    Args:
        data: Raw configuration dictionary.

    Returns:
        LLMFileConfig object.
    """
    try:
        return LLMFileConfig.model_validate(data)
    except Exception as e:
        # Fallback for older flat structure if necessary or just re-raise
        # Attempt to reconstruct if 'default' key is missing but likely 
        # the Pydantic model structure should match the YAML 1:1.
        # If the input is raw dict from legacy parse, we might need to map it.
        # But ConfigManager takes care of loading YAML. `parse_llm_config` is mostly used for testing or manual dicts.
        
        # Backward compat for legacy 'parse_llm_config' internal logic if mostly used for transformation:
        # Pydantic's model_validate handles strict structure. 
        # If existing code calls this with flat dicts, we might need manual mapping.
        # But looking at previous code, it constructed AgentConfig manually.
        
        # Let's trust Pydantic validation here.
        raise ValueError(f"Invalid LLM Config: {e}")


def load_llm_config(path: pathlib.Path) -> LLMFileConfig:
    """
    Loads LLM configuration from a YAML file.

    Args:
        path: Path to the YAML config file.

    Returns:
        LLMFileConfig object.
    """
    from nl2sql.configs import ConfigManager
    
    manager = ConfigManager()
    return manager.load_llm(path)


class LLMRegistry:
    """
    Manages LLM instances and configurations for different agents.
    
    Handles:
    - Loading configuration.
    - Instantiating LLMs (currently only OpenAI).
    - Wrapping LLMs for token usage tracking.
    - Enforcing structured output for specific agents.
    """

    def __init__(self, config: LLMFileConfig):
        """
        Initializes the LLMRegistry.

        Args:
            config: LLM configuration.
        """
        self.config = config

    def _agent_cfg(self, agent: str) -> AgentConfig:
        return (self.config.agents or {}).get(agent) or self.config.default

    def _base_llm(self, agent: str) -> ChatOpenAI:
        cfg = self._agent_cfg(agent)
        if cfg.provider != "openai":
            raise ValueError(f"Unsupported LLM provider: {cfg.provider}")
        
        # Unwrap SecretStr if present
        key_val = cfg.api_key.get_secret_value() if cfg.api_key else settings.openai_api_key
        
        if not key_val:
            raise RuntimeError("OPENAI_API_KEY is not set and no api_key provided in config.")
            
        # Enforce determinism: Temperature 0 and fixed seed
        return ChatOpenAI(
            model=cfg.model, 
            temperature=0.0, 
            api_key=key_val, 
            tags=[agent],
            seed=42
        )

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
        from nl2sql.pipeline.nodes.planner.schemas import PlanModel
        llm = self._base_llm("planner")
        return self._wrap_structured_usage(llm, PlanModel)

    def refiner_llm(self) -> LLMCallable:
        """Returns the LLM callable for the Refiner agent."""
        llm = self._base_llm("refiner")
        return llm.invoke


    def decomposer_llm(self) -> LLMCallable:
        """Returns the LLM callable for the Decomposer agent."""
        from nl2sql.pipeline.nodes.decomposer.schemas import DecomposerResponse
        llm = self._base_llm("decomposer")
        return self._wrap_structured_usage(llm, DecomposerResponse)

    def aggregator_llm(self) -> LLMCallable:
        """Returns the LLM callable for the Aggregator agent."""
        from nl2sql.pipeline.nodes.aggregator.schemas import AggregatedResponse
        llm = self._base_llm("aggregator")
        return self._wrap_structured_usage(llm,  AggregatedResponse)


    def direct_sql_llm(self) -> LLMCallable:
        """Returns the LLM callable for the Direct SQL agent."""
        from nl2sql.pipeline.nodes.direct_sql.schemas import DirectSQLResponse
        llm = self._base_llm("direct_sql")
        return self._wrap_structured_usage(llm, DirectSQLResponse)

    def semantic_llm(self) -> LLMCallable:
        """Returns the LLM callable for the Semantic Analysis agent."""
        from nl2sql.pipeline.nodes.semantic.schemas import SemanticAnalysisResponse
        llm = self._base_llm("semantic_analysis")
        return self._wrap_structured_usage(llm, SemanticAnalysisResponse)

    def intent_validator_llm(self) -> LLMCallable:
        """Returns the LLM callable for the Intent Validator agent."""
        from nl2sql.pipeline.nodes.intent_validator.schemas import IntentValidationResult
        llm = self._base_llm("intent_validator")
        return self._wrap_structured_usage(llm, IntentValidationResult)

    def llm_map(self) -> Dict[str, LLMCallable]:
        """Returns a dictionary of all agent LLM callables."""
        return {
            "planner": self.planner_llm(),
            "refiner": self.refiner_llm(),
            "decomposer": self.decomposer_llm(),
            "aggregator": self.aggregator_llm(),
            "direct_sql": self.direct_sql_llm(),
            "semantic_analysis": self.semantic_llm()
        }

    def get_usage_summary(self) -> Dict[str, Dict[str, int]]:
        """Returns the token usage summary."""
        return get_usage_summary()

    def get_token_log(self) -> List[Dict[str, Any]]:
        """Returns the raw token usage log."""
        return TOKEN_LOG
