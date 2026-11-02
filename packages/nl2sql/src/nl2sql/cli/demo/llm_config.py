"""The demo project's LLM settings on disk: the key in ``.env`` and the ``llm`` section in ``llm.yaml``.

``nl2sql init --demo`` writes the project; ``nl2sql.yaml`` includes
``llm.yaml``. Shared by ``nl2sql demo`` (``cli/commands/demo.py``) and the
playground's settings panel, so it lives here rather than in a command module.
"""
from __future__ import annotations

import pathlib
from typing import TYPE_CHECKING, List, Mapping, Optional

import yaml

from nl2sql.llm.providers import (
    LLM_AGENTS,
    PROVIDER_KEYS,
    default_model_for,
    default_temperature_for,
    env_var_for_key,
)

if TYPE_CHECKING:
    from nl2sql import LLM

# The files in the demo folder: the llm section, and the key's dotenv file.
LLM_FILE = pathlib.Path("llm.yaml")
ENV_FILE = pathlib.Path(".env")

# The registry's agent key (``astplanner``) -> the node name ``llm.agents`` uses (``ast_planner``).
AGENT_NODES = {key: node for node, key in LLM_AGENTS.items()}


def persist_api_key(path: pathlib.Path, key: str) -> None:
    """Records the key in the demo project's ``.env``, one key per provider.

    Every assignment of this key's variable is replaced by the one new line;
    other providers' keys stay, so a node on another provider keeps working.
    Empty placeholders of other providers are dropped, so a blank line can
    never stand in for a real key.
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


def with_api_keys(llm: "LLM", api_keys: Mapping[str, str]) -> "LLM":
    """``llm`` with each key in ``api_keys`` (provider -> key) given to the entries on that provider.

    This is how a key the demo was given (``--api-key``, or one saved in the
    settings panel) reaches the engine: as a value in its LLM config, never
    through the environment, and never written into ``llm.yaml``. An entry
    that names a key of its own keeps it. An agent on another provider than
    the default is given that provider's key, or explicitly none, so it never
    inherits the default's key for a provider it does not run on.
    """
    if not api_keys:
        return llm
    agents = {}
    for name, agent in llm.agents.items():
        provider = agent.provider or llm.provider
        if "api_key" not in agent.model_fields_set and provider != llm.provider:
            agent = agent.model_copy(update={"api_key": api_keys.get(provider)})
        agents[name] = agent
    api_key = llm.api_key if llm.api_key is not None else api_keys.get(llm.provider)
    return llm.model_copy(update={"api_key": api_key, "agents": agents})


def read_llm(directory: pathlib.Path) -> dict:
    """The demo's ``llm.yaml``, as written."""
    return yaml.safe_load((directory / LLM_FILE).read_text(encoding="utf-8")) or {}


def write_llm(directory: pathlib.Path, llm: dict) -> None:
    (directory / LLM_FILE).write_text(yaml.safe_dump(llm, sort_keys=False), encoding="utf-8")


def _point_agent_at(agent: dict, provider: str, base_url: Optional[str], api_key: Optional[str]) -> None:
    """Points one entry (the top level, or one agent) at ``provider``: its model, endpoint and key.

    Without ``api_key`` the entry names none, so the provider's standard
    variable is read. With a ``base_url`` (the replay server or the recording
    proxy) the placeholder ``api_key`` is the one that endpoint accepts.

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
    if api_key:
        agent["api_key"] = api_key
    else:
        agent.pop("api_key", None)
    if base_url:
        agent["base_url"] = base_url
    else:
        agent.pop("base_url", None)


def point_llm_config_at(directory: pathlib.Path, base_url: Optional[str], provider: str = "openai",
                        api_key: Optional[str] = None) -> None:
    """Points the ``llm`` section at ``provider``, and the per-node agents that follow it.

    With a ``base_url`` (replay and record, where one fake or proxy serves
    everything) every per-node agent follows too, with the placeholder
    ``api_key`` that endpoint takes: in replay mode a node left on a real
    provider would be sent the placeholder as its key.

    Live (no ``base_url``), the provider a per-node agent was given stays: a
    step put on Claude stays on Claude when the default moves to OpenAI. Only
    an agent with no provider, or one on the replay or record endpoint the
    default was on, follows the default.
    """
    cfg = read_llm(directory)
    shared_endpoint = cfg.get("base_url")
    _point_agent_at(cfg, provider, base_url, api_key)
    for agent in (cfg.get("agents") or {}).values():
        follows = (bool(base_url) or not agent.get("provider")
                   or (shared_endpoint is not None and agent.get("base_url") == shared_endpoint))
        _point_agent_at(agent, provider if follows else agent["provider"], base_url,
                        api_key if follows else None)
    write_llm(directory, cfg)
