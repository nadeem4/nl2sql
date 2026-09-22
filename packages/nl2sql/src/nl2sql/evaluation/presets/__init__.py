"""The LLM configs tier 2 can compare without a file path.

``--model MODEL`` builds a config with that model on every LLM node
(:func:`model_config`). ``--llm NAME`` picks one of the preset YAML files in
this folder, ``--llm PATH.yaml`` or ``--llm NAME=PATH`` a custom file
(:func:`resolve_llm_spec`).
"""
from __future__ import annotations

import pathlib
from typing import List, Tuple

import yaml

from nl2sql.configs.llm import AgentConfig, LLMFileConfig

PRESETS_DIR = pathlib.Path(__file__).resolve().parent


def model_config(spec: str) -> LLMFileConfig:
    """A config with ``spec``'s model on every LLM node.

    A model in ``VERIFIED_MODELS`` names its provider and its temperature
    (none where the model rejects one). Any other model is written
    ``provider/model``, e.g. ``ollama/llama3``, and runs at temperature 0.
    The key is the provider's ``${env:...}`` variable, which ``--env`` loads.
    """
    from nl2sql.cli.common.api_key import VERIFIED_MODELS, default_temperature_for
    from nl2sql.evaluation.tier2 import LLM_NODES
    from nl2sql.llm.registry import PROVIDER_PRESETS

    provider = next((p for p, models in VERIFIED_MODELS.items() if spec in models), None)
    model = spec
    if provider is None:
        provider, sep, model = spec.partition("/")
        if not sep or not model:
            raise ValueError(f"Unknown model '{spec}': it is not in VERIFIED_MODELS, so name its provider as "
                             f"provider/model, e.g. openrouter/{spec} or ollama/{spec}.")
        if provider not in PROVIDER_PRESETS:
            raise ValueError(f"Unknown provider '{provider}' in '{spec}'. Known: {', '.join(PROVIDER_PRESETS)}.")
    verified = VERIFIED_MODELS.get(provider, {})
    temperature = verified[model] if model in verified else default_temperature_for(provider)
    key_env = PROVIDER_PRESETS[provider].api_key_env
    agent = AgentConfig(provider=provider, model=model, temperature=temperature,
                        api_key="${env:" + key_env + "}" if key_env else None)
    return LLMFileConfig(default=agent,
                         agents={key: agent.model_copy(update={"name": key}) for key in LLM_NODES.values()})


def preset_names() -> List[str]:
    return sorted(p.stem for p in PRESETS_DIR.glob("*.yaml"))


def list_presets() -> List[Tuple[str, LLMFileConfig]]:
    """Every preset, by name, loaded."""
    return [(name, LLMFileConfig.model_validate(
        yaml.safe_load((PRESETS_DIR / f"{name}.yaml").read_text(encoding="utf-8")))) for name in preset_names()]


def resolve_llm_spec(spec: str) -> Tuple[str, pathlib.Path]:
    """``(name, path)`` for an ``--llm`` value: ``NAME=PATH``, ``PATH.yaml`` or a preset name.

    A preset matches by its full name or by a ``-``-separated suffix, so
    ``mini-helpers`` is ``gpt-5.4-mini-helpers``.
    """
    spec = spec.strip()
    if "=" in spec:
        name, _, path = spec.partition("=")
        if not name.strip() or not path.strip():
            raise ValueError(f"--llm expects NAME=PATH, PATH.yaml or a preset name, got '{spec}'.")
        return name.strip(), pathlib.Path(path.strip())
    if spec.lower().endswith((".yaml", ".yml")):
        return pathlib.Path(spec).stem, pathlib.Path(spec)
    names = preset_names()
    matches = [n for n in names if n == spec] or [n for n in names if n.endswith("-" + spec)]
    if len(matches) != 1:
        found = f"matches {', '.join(matches)}" if matches else "matches none"
        raise ValueError(f"No preset named '{spec}' ({found}). Presets: {', '.join(names)}. "
                         "Or give a file: --llm PATH.yaml or --llm NAME=PATH.")
    return matches[0], PRESETS_DIR / f"{matches[0]}.yaml"
