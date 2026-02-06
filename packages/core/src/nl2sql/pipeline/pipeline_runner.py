import time
import traceback
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field

from nl2sql.auth import UserContext
from nl2sql.pipeline.runtime import run_with_graph
from nl2sql.common.settings import settings
from nl2sql.context import NL2SQLContext


@dataclass
class PipelineResult:
    """Standardized result object from the pipeline execution."""

    success: bool
    final_state: Dict[str, Any] = field(default_factory=dict)
    duration: float = 0.0
    error: Optional[str] = None
    traceback: Optional[str] = None


class PipelineRunner:
    """
    Orchestrates the execution of the NL2SQL pipeline.
    Decoupled from CLI presentation logic.
    """

    def __init__(self, ctx: NL2SQLContext):
        self.ctx = ctx

    def run(
        self,
        query: str,
        role: str = "admin",
        datasource_id: Optional[str] = None,
        execute: bool = True,
        callbacks: List[Any] = None,
    ) -> PipelineResult:
        """
        Executes the pipeline graph.
        """
        start_time = time.perf_counter()

        user_context = UserContext(
            roles=[role],
            tenant_id=settings.tenant_id,
        )

        try:
            final_state = run_with_graph(
                self.ctx,
                user_query=query,
                datasource_id=datasource_id,
                execute=execute,
                callbacks=callbacks or [],
                user_context=user_context,
            )

            return PipelineResult(
                success=True,
                final_state=final_state,
                duration=time.perf_counter() - start_time,
            )

        except Exception as e:
            return PipelineResult(
                success=False,
                error=str(e),
                traceback=traceback.format_exc(),
                duration=time.perf_counter() - start_time,
            )
