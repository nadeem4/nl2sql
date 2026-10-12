from typing import List, Dict, Any
from pydantic import BaseModel, Field

from nl2sql.execution.artifacts.results import ResultSummary
from nl2sql.execution.contracts import ArtifactRef


class AggregatorResponse(BaseModel):
    # The first ANSWER_PREVIEW_ROWS rows of each terminal result, keyed by DAG
    # node id: what the answer model is shown. The whole result is the artifact
    # in computed_artifacts under the same key.
    terminal_results: Dict[str, List[Dict[str, Any]]] = Field(default_factory=dict)
    # Each terminal result, saved as its own parquet artifact in the run's
    # request folder; its row_count is the full result's.
    computed_artifacts: Dict[str, ArtifactRef] = Field(default_factory=dict)
    # One entry per saved result (columns, row count, truncated): the run's
    # manifest, also written as manifest.json beside the results.
    results: List[ResultSummary] = Field(default_factory=list)
    errors: List[Any] = Field(default_factory=list)
    reasoning: List[Dict[str, Any]] = Field(default_factory=list)
