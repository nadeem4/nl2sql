"""The demo project's LLM settings on disk: the key in ``.env.demo`` and the
agents in ``configs/llm.demo.yaml``.

Shared by ``nl2sql demo`` (``cli/commands/demo.py``) and the playground's
settings panel, so it lives here rather than in a command module.
"""
from __future__ import annotations

import pathlib
from typing import List, Optional

import yaml

from nl2sql.llm.providers import (
    PROVIDER_KEYS,
    default_model_for,
    default_temperature_for,
    env_var_for_key,
    env_var_for_provider,
)


def persist_api_key(path: pathlib.Path, key: str) -> None:
    """Records the key in the demo project's ``.env.demo``, one key per provider.

    Every assignment of this key's variable is replaced by the one new line;
    other providers' keys stay, so a node on another provider keeps working.
    Empty placeholders of other providers are dropped: an empty
    ``OPENAI_API_KEY=`` left below a real value would blank it again when
    indexing loads the file with ``override=True``.
    """
    variable = env_var_for_key(key)
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []

    kept: List[str] = []
    written = False
    for line in lines:
        name, _, value = line.partition("=")
        if name.strip() in PROVIDER_KEYS and (name.strip() == variable or not value.strip()):
            if name.strip() == variable and not written:
                kept.append(f"{variable}={key}")
                written = True
            continue
        kept.append(line)
    if not written:
        kept.append(f"{variable}={key}")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(kept) + "\n", encoding="utf-8")


def _point_agent_at(agent: dict, provider: str, base_url: Optional[str]) -> None:
    """Points one agent entry at ``provider``: its key reference, model and endpoint.

    The key reference follows the provider. The scaffolded config reads
    ``${env:OPENAI_API_KEY}``, and a reference names the one variable the
    registry looks in, so an OpenRouter demo kept failing with "no API key"
    while ``OPENROUTER_API_KEY`` was set.

    Moving between OpenAI and Anthropic also moves the model: a ``gpt-`` model
    means nothing to Anthropic, nor a ``claude-`` one to OpenAI. The model is
    replaced by the provider's default, with the temperature that model takes.
    """
    agent["provider"] = provider
    if provider in ("openai", "anthropic") and (
        (provider == "anthropic") != str(agent.get("model", "")).startswith("claude-")
    ):
        agent["model"] = default_model_for(provider)
        agent["temperature"] = default_temperature_for(provider)
    if provider in ("openai", "openrouter", "anthropic"):
        agent["api_key"] = "${env:" + env_var_for_provider(provider) + "}"
    if base_url:
        agent["base_url"] = base_url
    else:
        agent.pop("base_url", None)


def point_llm_config_at(directory: pathlib.Path, base_url: Optional[str], provider: str = "openai") -> None:
    """Points the default agent at ``provider``, and the per-node agents that follow it.

    With a ``base_url`` (replay and record, where one fake or proxy serves
    everything) every per-node agent follows too: in replay mode a node left on
    a real provider would be sent the ``replay`` placeholder as its key.

    Live (no ``base_url``), the provider a per-node agent was given stays: a
    step put on Claude stays on Claude when the default moves to OpenAI. Only
    an agent with no provider, or one on the replay or record endpoint the
    default was on, follows the default.
    """
    path = directory / "configs" / "llm.demo.yaml"
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    shared_endpoint = cfg["default"].get("base_url")
    _point_agent_at(cfg["default"], provider, base_url)
    for agent in (cfg.get("agents") or {}).values():
        follows = (bool(base_url) or not agent.get("provider")
                   or (shared_endpoint is not None and agent.get("base_url") == shared_endpoint))
        _point_agent_at(agent, provider if follows else agent["provider"], base_url)
    path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
