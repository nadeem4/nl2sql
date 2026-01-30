from pydantic import BaseModel, Field
from typing import Optional, List, Any
import pathlib

class RunConfig(BaseModel):
    """Configuration for running the pipeline."""
    query: str
    ds_id: Optional[str] = None
    role: str = "admin"
    no_exec: bool = False
    verbose: bool = False
    show_perf: bool = False


class BenchmarkConfig(BaseModel):
    """Configuration for running benchmarks."""
    dataset_path: pathlib.Path
    config_path: pathlib.Path
    bench_config_path: Optional[pathlib.Path] = None
    llm_config_path: Optional[pathlib.Path] = None
    vector_store_path: str
    iterations: int = 3
    routing_only: bool = False
    include_ids: Optional[List[str]] = None
    export_path: Optional[pathlib.Path] = None
    stub_llm: bool = False
