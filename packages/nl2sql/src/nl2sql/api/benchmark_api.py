"""
Benchmark API for NL2SQL.

Provides public entry points for dataset benchmarking without exposing internal runners.
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Union

import yaml

from nl2sql.config import LLM
from nl2sql.llm.models import LLMFileConfig
from nl2sql.runtime import Runtime
from nl2sql.evaluation.benchmark_runner import BenchmarkRunner, BenchmarkResult
from nl2sql.evaluation.gold import dataset_datasources, load_gold_dataset
from nl2sql.evaluation.presets import load_llm_file
from nl2sql.evaluation.records import database_identity
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

    def __init__(self, ctx: Runtime):
        self._ctx = ctx

    def _context(self, config: BenchmarkConfig) -> Runtime:
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
        dataset = load_gold_dataset(config.dataset_path)
        ids = select_question_ids(dataset, questions) if questions else None
        ctx = self._context(config)
        # The whole dataset names the database, so narrowing the run to a few
        # questions leaves the record's identity -- and its series -- alone.
        return {**run_retrieval_recall(ctx, config.dataset_path, ids),
                "database": database_identity(ctx, dataset_datasources(dataset))}

    def tier2_configs(
        self, config: BenchmarkConfig,
        llm_configs: Optional[Dict[str, Union[pathlib.Path, str, LLMFileConfig]]] = None,
    ) -> Dict[str, LLMFileConfig]:
        """The named LLM configs tier 2 compares, or the engine's own ``llm`` section as ``default``.

        Each value is an ``LLMFileConfig`` (``--model``) or the path of a file
        holding an ``llm`` section, as ``llm.yaml`` does. Loading one makes no
        model call.
        """
        if not llm_configs:
            return {"default": LLMFileConfig.from_llm(self._ctx.config.llm)}
        return {name: cfg if isinstance(cfg, LLMFileConfig) else load_llm_file(pathlib.Path(cfg))
                for name, cfg in llm_configs.items()}

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
        use_curated: bool = False,
    ) -> Dict[str, Any]:
        """Run the real model end to end on the gold questions, per config, under a dollar cap.

        See :mod:`nl2sql.evaluation.tier2`. ``questions`` narrows the run to
        these question ids or tags. Curated answers are off unless
        ``use_curated`` is set. Returns the scoreboard.
        """
        dataset = load_gold_dataset(config.dataset_path)
        if questions:
            config = config.model_copy(update={"include_ids": select_question_ids(dataset, questions)})
        ctx = self._context(config)
        board = run_tier2(ctx, config, llm_configs, max_cost=max_cost, passes=passes,
                          before_case=before_case, on_case=on_case, use_curated=use_curated)
        return {**board, "database": database_identity(ctx, dataset_datasources(dataset))}

    @staticmethod
    def _load_llm_configs(config: BenchmarkConfig) -> Dict[str, LLMFileConfig]:
        """The named LLM configs in ``bench_config_path``, each an ``llm`` section, or none."""
        path = config.bench_config_path
        if not path or not pathlib.Path(path).exists():
            return {}
        bench_data = yaml.safe_load(pathlib.Path(path).read_text(encoding="utf-8")) or {}
        return {
            name: LLMFileConfig.from_llm(LLM.model_validate(cfg_data))
            for name, cfg_data in bench_data.items()
            if isinstance(cfg_data, dict)
        }
