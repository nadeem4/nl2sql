from __future__ import annotations

import pathlib
from typing import Optional, List

from pydantic import BaseModel

from nl2sql.evaluation.gold import GOLD_DATASET_PATH


class BenchmarkConfig(BaseModel):
    """Configuration for running benchmarks.

    ``include_ids`` and ``roles`` narrow the run; by default every question in
    the dataset runs once per role named in its ``expected`` map. The engine
    the benchmark runs on is built from ``nl2sql.yaml``; ``bench_config_path``
    names the LLM configs a matrix run compares, each an ``llm`` section.
    """

    dataset_path: pathlib.Path = GOLD_DATASET_PATH
    bench_config_path: Optional[pathlib.Path] = None
    iterations: int = 3
    include_ids: Optional[List[str]] = None
    roles: Optional[List[str]] = None
    export_path: Optional[pathlib.Path] = None
