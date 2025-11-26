from __future__ import annotations

import dataclasses
import pathlib
from typing import Callable, Dict, Optional, List

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    openai_api_key: Optional[str] = Field(default=None, env="OPENAI_API_KEY")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()

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
class LLMConfig:
    provider: str
    model: str
    api_key: Optional[str] = None


def _load_yaml(path: pathlib.Path) -> Dict:
    try:
        import yaml  # type: ignore
    except ImportError as exc:
        raise RuntimeError("PyYAML is required to load LLM configs") from exc
    if not path.exists():
        raise FileNotFoundError(f"LLM config not found: {path}")
    return yaml.safe_load(path.read_text())


def load_llm_map(path: pathlib.Path) -> Dict[str, LLMCallable]:
    """
    Load an LLM mapping from YAML.

    YAML structure:
    default:
      provider: openai
      model: gpt-4o-mini
    agents:
      intent:
        provider: openai
        model: gpt-4o-mini
      planner:
        provider: openai
        model: gpt-4o-mini
      generator:
        provider: openai
        model: gpt-4o-mini
    """
    data = _load_yaml(path) or {}
    default_cfg = data.get("default") or {}
    agent_cfgs = data.get("agents") or {}

    default_llm = _build_llm_callable(
        LLMConfig(
            provider=default_cfg.get("provider", "openai"),
            model=default_cfg.get("model", "gpt-4o-mini"),
            api_key=default_cfg.get("api_key"),
        ),
        agent_name="_default",
    )

    llm_map: Dict[str, LLMCallable] = {}
    for agent, cfg in agent_cfgs.items():
        llm_map[agent] = _build_llm_callable(
            LLMConfig(
                provider=cfg.get("provider", default_cfg.get("provider", "openai")),
                model=cfg.get("model", default_cfg.get("model", "gpt-4o-mini")),
                api_key=cfg.get("api_key", default_cfg.get("api_key")),
            ),
            agent_name=agent,
        )

    # Provide fallback for nodes without explicit mapping
    llm_map["_default"] = default_llm
    return llm_map


def _build_llm_callable(cfg: LLMConfig, agent_name: str = "") -> LLMCallable:
    provider = cfg.provider.lower()
    if provider == "openai":
        return _openai_llm(model=cfg.model, api_key=cfg.api_key, agent_name=agent_name)
    raise ValueError(f"Unsupported LLM provider: {cfg.provider}")


def _openai_llm(model: str, api_key: Optional[str] = None, agent_name: str = "") -> LLMCallable:
    """
    Build an OpenAI LLM callable using langchain's ChatOpenAI for simplicity.
    """
    try:
        from langchain_openai import ChatOpenAI  # type: ignore
    except ImportError as exc:
        raise RuntimeError("langchain-openai is required for OpenAI provider") from exc

    key = api_key or settings.openai_api_key
    if not key:
        raise RuntimeError("OPENAI_API_KEY is not set and no api_key provided in config.")

    client = ChatOpenAI(model=model, api_key=str(key), temperature=0)

    def call(prompt: str) -> str:
        resp = client.invoke(prompt)
        usage = getattr(resp, "usage_metadata", None)
        if usage:
            TOKEN_LOG.append(
                {
                    "model": model,
                    "agent": agent_name or "unknown",
                    "prompt_tokens": usage.get("prompt_tokens", 0),
                    "completion_tokens": usage.get("completion_tokens", 0),
                    "total_tokens": usage.get("total_tokens", 0),
                }
            )
        return resp.content if hasattr(resp, "content") else str(resp)

    return call
