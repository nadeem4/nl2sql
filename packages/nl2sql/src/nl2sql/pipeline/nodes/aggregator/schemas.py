from typing import List, Dict, Any
from pydantic import BaseModel, Field

from nl2sql.execution.contracts import ArtifactRef


class AggregatorResponse(BaseModel):
    # The first ANSWER_PREVIEW_ROWS rows of each terminal result, keyed by DAG
    # node id: what the answer model is shown. The whole result is the artifact
    # in computed_artifacts under the same key.
    terminal_results: Dict[str, List[Dict[str, Any]]] = Field(default_factory=dict)
    # Each terminal result, saved as its own parquet artifact in the run's
    # request folder; its row_count is the full result's.
    computed_artifacts: Dict[str, ArtifactRef] = Field(default_factory=dict)
    errors: List[Any] = Field(default_factory=list)
    reasoning: List[Dict[str, Any]] = Field(default_factory=list)
