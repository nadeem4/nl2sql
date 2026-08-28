from __future__ import annotations

import pathlib
from typing import Optional, List

from pydantic import BaseModel


class BenchmarkConfig(BaseModel):
    """Configuration for running benchmarks."""

    dataset_path: pathlib.Path
    config_path: Optional[pathlib.Path] = None
    bench_config_path: Optional[pathlib.Path] = None
    llm_config_path: Optional[pathlib.Path] = None
    vector_store_path: Optional[str] = None
    secrets_path: Optional[pathlib.Path] = None
    iterations: int = 3
    routing_only: bool = False
    include_ids: Optional[List[str]] = None
    export_path: Optional[pathlib.Path] = None
    stub_llm: bool = False
