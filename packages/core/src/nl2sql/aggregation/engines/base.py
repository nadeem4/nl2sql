from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Tuple

import polars as pl

from nl2sql.execution.contracts import ArtifactRef


class AggregationEngine(ABC):
    @abstractmethod
    def load_scan(self, artifact: ArtifactRef) -> pl.DataFrame:
        raise NotImplementedError

    @abstractmethod
    def combine(
        self,
        operation: str,
        inputs: List[Tuple[str, pl.DataFrame]],
        join_keys: List[Dict[str, Any]],
    ) -> pl.DataFrame:
        raise NotImplementedError

    @abstractmethod
    def post_op(self, operation: str, frame: pl.DataFrame, attributes: Dict[str, Any]) -> pl.DataFrame:
        raise NotImplementedError

    def to_rows(self, frame: pl.DataFrame) -> List[Dict[str, Any]]:
        return frame.to_dicts()
