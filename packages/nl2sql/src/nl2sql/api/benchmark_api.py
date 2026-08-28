"""
Benchmark API for NL2SQL.

Provides public entry points for dataset benchmarking without exposing internal runners.
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass
from typing import Dict, Optional

import yaml

from nl2sql.common.settings import settings
from nl2sql.configs import ConfigManager
from nl2sql.configs.llm import LLMFileConfig, AgentConfig
from nl2sql.context import NL2SQLContext
from nl2sql.datasources import DatasourceRegistry
from nl2sql.evaluation.benchmark_runner import BenchmarkRunner, BenchmarkResult
from nl2sql.evaluation.types import BenchmarkConfig
from nl2sql.indexing.vector_store import VectorStore
from nl2sql.llm import LLMRegistry
from nl2sql.secrets import SecretManager


@dataclass
class BenchmarkMatrixResult:
    """Aggregate results for a matrix benchmark run."""

    results_by_config: Dict[str, BenchmarkResult]


class BenchmarkAPI:
    """
    API for running dataset benchmarks using core evaluation tooling.
    """

    def __init__(self, ctx: Optional[NL2SQLContext] = None):
        self._ctx = ctx

    def run_matrix(
        self,
        config: BenchmarkConfig,
        *,
        progress_callback=None,
    ) -> BenchmarkMatrixResult:
        """
        Run a benchmark suite against one or more LLM configs.

        Args:
            config: Benchmark configuration (dataset, datasource config, etc.).
            progress_callback: Optional progress iterator wrapper.
        """
        cm = self._ctx.config_manager if self._ctx else ConfigManager()

        secret_manager = SecretManager()
        secrets_path = config.secrets_path or settings.secrets_config_path
        if secrets_path and pathlib.Path(secrets_path).exists():
            secret_configs = cm.load_secrets(pathlib.Path(secrets_path))
            if secret_configs:
                secret_manager.configure(secret_configs)

        config_path = pathlib.Path(config.config_path) if config.config_path else pathlib.Path(settings.datasource_config_path)
        ds_configs = cm.load_datasources(config_path)
        ds_registry = DatasourceRegistry(secret_manager)
        ds_registry.register_datasources(ds_configs)

        vector_store = VectorStore(
            collection_name=settings.vector_store_collection_name,
            persist_directory=config.vector_store_path or settings.vector_store_path,
        )

        llm_configs = self._load_llm_configs(config, cm)
        results: Dict[str, BenchmarkResult] = {}

        for name, llm_cfg in llm_configs.items():
            llm_registry = LLMRegistry(secret_manager)
            agents = llm_cfg.agents or {}
            agents["default"] = llm_cfg.default
            llm_registry.register_llms(agents)

            runner = BenchmarkRunner(config, ds_registry, vector_store, llm_registry)
            results[name] = runner.run_dataset(
                config_name=name,
                progress_callback=progress_callback,
            )

        return BenchmarkMatrixResult(results_by_config=results)

    def _load_llm_configs(self, config: BenchmarkConfig, cm: ConfigManager) -> Dict[str, LLMFileConfig]:
        llm_configs: Dict[str, LLMFileConfig] = {}

        if config.bench_config_path and pathlib.Path(config.bench_config_path).exists():
            bench_data = yaml.safe_load(pathlib.Path(config.bench_config_path).read_text()) or {}
            for name, cfg_data in bench_data.items():
                if isinstance(cfg_data, dict):
                    llm_configs[name] = LLMFileConfig.model_validate(cfg_data)

        if not llm_configs:
            if config.llm_config_path and pathlib.Path(config.llm_config_path).exists():
                llm_configs["default"] = cm.load_llm(pathlib.Path(config.llm_config_path))
            else:
                llm_configs["default"] = LLMFileConfig(
                    default=AgentConfig(provider="openai", model="gpt-4o")
                )

        if config.stub_llm:
            for llm_cfg in llm_configs.values():
                llm_cfg.default.provider = "stub"
                for agent_cfg in llm_cfg.agents.values():
                    agent_cfg.provider = "stub"

        return llm_configs
