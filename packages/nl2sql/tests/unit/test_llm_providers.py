"""Provider, model and LLM-node knowledge lives in ``nl2sql.llm``, not the CLI.

The SDK, the REST API and evaluation need the same facts the CLI does: which
env var a provider reads, which models are verified, which agent each LLM node
runs on. They are defined once, next to ``PROVIDER_PRESETS``.
"""
import ast
import pathlib

from nl2sql.llm.providers import (
    KEYED_PROVIDERS,
    LLM_AGENTS,
    PROVIDER_KEYS,
    UPSTREAMS,
    VERIFIED_MODELS,
    env_var_for_provider,
)
from nl2sql.llm.registry import PROVIDER_PRESETS

SRC = pathlib.Path(__file__).resolve().parents[2] / "src" / "nl2sql"


def test_provider_keys_are_the_env_vars_the_presets_name():
    assert PROVIDER_KEYS == tuple(p.api_key_env for p in PROVIDER_PRESETS.values() if p.api_key_env)
    assert KEYED_PROVIDERS == tuple(name for name, p in PROVIDER_PRESETS.items() if p.api_key_env)


def test_env_var_for_provider_reads_the_preset():
    for name, preset in PROVIDER_PRESETS.items():
        if preset.api_key_env:
            assert env_var_for_provider(name) == preset.api_key_env


def test_upstreams_are_the_openai_wire_providers_with_a_key():
    assert UPSTREAMS == {"openai": "https://api.openai.com/v1",
                         "openrouter": PROVIDER_PRESETS["openrouter"].base_url}


def test_verified_models_name_known_providers():
    assert set(VERIFIED_MODELS) <= set(PROVIDER_PRESETS)


def test_the_cli_and_demo_reuse_the_llm_tables():
    from nl2sql.cli.commands import demo
    from nl2sql.cli.demo import manager
    from nl2sql.cli.demo.playground import settings

    assert demo.PROVIDER_KEYS is PROVIDER_KEYS
    assert manager.PROVIDER_KEYS is PROVIDER_KEYS
    assert demo.UPSTREAMS is UPSTREAMS
    assert [node["agent"] for node in settings.LLM_NODES] == list(LLM_AGENTS.values())


def test_feedback_names_each_node_by_its_agent():
    from nl2sql.feedback.record import models_by_node

    calls = [{"node": node, "model": "m"} for node in LLM_AGENTS]
    configs = {agent: {"provider": f"p-{agent}"} for agent in LLM_AGENTS.values()}
    out = models_by_node({"usage": {"calls": calls}}, configs)
    assert {node: v["provider"] for node, v in out.items()} == {n: f"p-{a}" for n, a in LLM_AGENTS.items()}


def _imports(path: pathlib.Path):
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.module:
            yield node.module
        elif isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)


def test_evaluation_never_imports_the_cli():
    offenders = [f"{path.relative_to(SRC)}: {name}"
                 for path in (SRC / "evaluation").rglob("*.py")
                 for name in _imports(path) if name.startswith("nl2sql.cli")]
    assert offenders == []


def test_keyed_provider_env_vars_are_spelled_in_one_module():
    """Rule 10 of the boundary audit: one home for the env-var names."""
    for name in ("OPENROUTER_API_KEY", "ANTHROPIC_API_KEY"):
        homes = set()
        for path in SRC.rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value == name:
                    homes.add(path.relative_to(SRC).as_posix())
        assert homes == {"llm/registry.py"}, name
