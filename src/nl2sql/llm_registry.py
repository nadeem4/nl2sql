from __future__ import annotations

import dataclasses
import pathlib
from typing import Callable, Dict, Optional, List

import yaml
from langchain_openai import ChatOpenAI
from nl2sql.settings import settings






# Track token usage per run: list of {agent, model, prompt_tokens, completion_tokens, total_tokens}
TOKEN_LOG: List[Dict[str, object]] = []


def reset_usage() -> None:
    TOKEN_LOG.clear()


def get_usage_summary() -> Dict[str, Dict[str, int]]:
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
    provider: str
    model: str
    temperature: float = 0
    api_key: Optional[str] = None


@dataclasses.dataclass
class LLMConfig:
    default: AgentConfig
    agents: Dict[str, AgentConfig]


def load_llm_config(path: pathlib.Path) -> LLMConfig:
    if not path.exists():
        raise FileNotFoundError(f"LLM config not found: {path}")
    data = yaml.safe_load(path.read_text()) or {}
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


class LLMRegistry:
    def __init__(self, config: LLMConfig, engine, row_limit: int):
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
        structured_llm = llm.with_structured_output(schema)

        def call(prompt: str) -> Any:
            # Note: with_structured_output returns the object directly
            resp = structured_llm.invoke(prompt)
            # We can't easily track tokens here without the raw response, 
            # but we can at least log that a call happened or try to estimate.
            # For now, we'll skip detailed token logging for structured calls 
            # or we could use a callback if needed.
            return resp

        return call

    def intent_llm(self) -> LLMCallable:
        from nl2sql.schemas import IntentModel
        llm = self._base_llm("intent")
        return self._wrap_structured_usage(llm, "intent", IntentModel)

    def planner_llm(self) -> LLMCallable:
        from nl2sql.schemas import PlanModel
        llm = self._base_llm("planner")
        return self._wrap_structured_usage(llm, "planner", PlanModel)

    def generator_llm(self) -> LLMCallable:
        from nl2sql.schemas import SQLModel
        llm = self._base_llm("generator")
        return self._wrap_structured_usage(llm, "generator", SQLModel)



    def llm_map(self) -> Dict[str, LLMCallable]:
        return {
            "intent": self.intent_llm(),
            "planner": self.planner_llm(),
            "generator": self.generator_llm(),
            "_default": self.intent_llm(),
        }
