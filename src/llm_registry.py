from __future__ import annotations

import dataclasses
import pathlib
from typing import Callable, Dict, Optional


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

    default_llm = _build_llm_callable(LLMConfig(
        provider=default_cfg.get("provider", "openai"),
        model=default_cfg.get("model", "gpt-4o-mini"),
        api_key=default_cfg.get("api_key"),
    ))

    llm_map: Dict[str, LLMCallable] = {}
    for agent, cfg in agent_cfgs.items():
        llm_map[agent] = _build_llm_callable(LLMConfig(
            provider=cfg.get("provider", default_cfg.get("provider", "openai")),
            model=cfg.get("model", default_cfg.get("model", "gpt-4o-mini")),
            api_key=cfg.get("api_key", default_cfg.get("api_key")),
        ))

    # Provide fallback for nodes without explicit mapping
    llm_map["_default"] = default_llm
    return llm_map


def _build_llm_callable(cfg: LLMConfig) -> LLMCallable:
    provider = cfg.provider.lower()
    if provider == "openai":
        return _openai_llm(model=cfg.model, api_key=cfg.api_key)
    raise ValueError(f"Unsupported LLM provider: {cfg.provider}")


def _openai_llm(model: str, api_key: Optional[str] = None) -> LLMCallable:
    """
    Build an OpenAI LLM callable using langchain's ChatOpenAI for simplicity.
    """
    try:
        from langchain_openai import ChatOpenAI  # type: ignore
    except ImportError as exc:
        raise RuntimeError("langchain-openai is required for OpenAI provider") from exc

    client = ChatOpenAI(model=model, api_key=api_key, temperature=0)

    def call(prompt: str) -> str:
        resp = client.invoke(prompt)
        return resp.content if hasattr(resp, "content") else str(resp)

    return call
