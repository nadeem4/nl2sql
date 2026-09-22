"""
Benchmark API for NL2SQL.

Provides public entry points for dataset benchmarking without exposing internal runners.
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import yaml

from nl2sql.configs.llm import LLMFileConfig
from nl2sql.context import NL2SQLContext
from nl2sql.evaluation.benchmark_runner import BenchmarkRunner, BenchmarkResult
from nl2sql.evaluation.gold import load_gold_dataset
from nl2sql.evaluation.retrieval_recall import run_retrieval_recall
from nl2sql.evaluation.tier1 import run_tier1
from nl2sql.evaluation.tier2 import run_tier2, select_question_ids
from nl2sql.evaluation.types import BenchmarkConfig


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

    def _context(self, config: BenchmarkConfig) -> NL2SQLContext:
        if self._ctx is None:
            self._ctx = NL2SQLContext(
                ds_config_path=config.config_path,
                secrets_config_path=config.secrets_path,
                llm_config_path=config.llm_config_path,
                vector_store_path=pathlib.Path(config.vector_store_path) if config.vector_store_path else None,
                policies_config_path=config.policies_path,
            )
        return self._ctx

    def run_matrix(
        self,
        config: BenchmarkConfig,
        *,
        progress_callback=None,
    ) -> BenchmarkMatrixResult:
        """
        Run the gold dataset through the full pipeline with a real LLM.

        With ``bench_config_path`` each LLM config it names is run in turn;
        otherwise the context's own LLM config is used once, as ``default``.

        Args:
            config: Benchmark configuration (dataset, datasource config, etc.).
            progress_callback: Optional progress iterator wrapper.
        """
        ctx = self._context(config)
        llm_configs = self._load_llm_configs(config)
        if not llm_configs:
            runner = BenchmarkRunner(config, ctx)
            return BenchmarkMatrixResult({"default": runner.run_dataset(progress_callback=progress_callback)})

        results: Dict[str, BenchmarkResult] = {}
        for name, llm_cfg in llm_configs.items():
            agents = dict(llm_cfg.agents or {})
            agents["default"] = llm_cfg.default
            ctx.llm_registry.replace_llms(agents)
            results[name] = BenchmarkRunner(config, ctx).run_dataset(progress_callback=progress_callback)
        return BenchmarkMatrixResult(results_by_config=results)

    def run_tier1(self, config: BenchmarkConfig) -> BenchmarkResult:
        """Run the hand-written gold plans through the code nodes, with no API key.

        See :mod:`nl2sql.evaluation.tier1`. The context's LLM registry is
        replaced by the gold-plan fake for the rest of this API's life.
        """
        return run_tier1(self._context(config), config)

    def run_retrieval(self, config: BenchmarkConfig, questions: Optional[List[str]] = None) -> Dict[str, Any]:
        """Table and column recall of schema retrieval on the gold questions: no key, no LLM, no cost.

        See :mod:`nl2sql.evaluation.retrieval_recall`. ``questions`` narrows
        the run to these question ids or tags. Returns the report.
        """
        ids = select_question_ids(load_gold_dataset(config.dataset_path), questions) if questions else None
        return run_retrieval_recall(self._context(config), config.dataset_path, ids)

    def tier2_configs(
        self, config: BenchmarkConfig, llm_config_paths: Optional[Dict[str, pathlib.Path]] = None,
    ) -> Dict[str, LLMFileConfig]:
        """The named LLM configs tier 2 compares, or the context's own one as ``default``.

        Each path is an LLM config in the normal ``llm.yaml`` format. Loading
        one makes no model call.
        """
        manager = self._context(config).config_manager
        if not llm_config_paths:
            return {"default": manager.load_llm()}
        return {name: manager.load_llm(pathlib.Path(path)) for name, path in llm_config_paths.items()}

    def run_tier2(
        self,
        config: BenchmarkConfig,
        llm_configs: Dict[str, LLMFileConfig],
        *,
        max_cost: float,
        passes: int = 1,
        questions: Optional[List[str]] = None,
        before_case=None,
        on_case=None,
    ) -> Dict[str, Any]:
        """Run the real model end to end on the gold questions, per config, under a dollar cap.

        See :mod:`nl2sql.evaluation.tier2`. ``questions`` narrows the run to
        these question ids or tags. Returns the scoreboard.
        """
        if questions:
            ids = select_question_ids(load_gold_dataset(config.dataset_path), questions)
            config = config.model_copy(update={"include_ids": ids})
        return run_tier2(self._context(config), config, llm_configs, max_cost=max_cost, passes=passes,
                         before_case=before_case, on_case=on_case)

    @staticmethod
    def _load_llm_configs(config: BenchmarkConfig) -> Dict[str, LLMFileConfig]:
        """The named LLM configs in ``bench_config_path``, or none."""
        path = config.bench_config_path
        if not path or not pathlib.Path(path).exists():
            return {}
        bench_data = yaml.safe_load(pathlib.Path(path).read_text(encoding="utf-8")) or {}
        return {
            name: LLMFileConfig.model_validate(cfg_data)
            for name, cfg_data in bench_data.items()
            if isinstance(cfg_data, dict)
        }
