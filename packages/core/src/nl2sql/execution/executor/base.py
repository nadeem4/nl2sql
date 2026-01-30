from __future__ import annotations

from abc import ABC, abstractmethod

from nl2sql.execution.contracts import ExecutorRequest, ExecutorResponse


class ExecutorService(ABC):
    @abstractmethod
    def execute(self, request: ExecutorRequest) -> ExecutorResponse:
        raise NotImplementedError
