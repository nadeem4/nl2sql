
import time
import json
import traceback
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
from nl2sql.pipeline.graph import run_with_graph, UserContext
from nl2sql.datasources import DatasourceRegistry
from nl2sql.services.llm import LLMRegistry
from nl2sql.services.vector_store import OrchestratorVectorStore
from nl2sql.common.settings import settings
from nl2sql.common.exceptions import NL2SQLError

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
    def __init__(
        self,
        datasource_registry: DatasourceRegistry,
        llm_registry: LLMRegistry,
        vector_store: OrchestratorVectorStore
    ):
        self.ds_registry = datasource_registry
        self.llm_registry = llm_registry
        self.vector_store = vector_store

    def run(
        self,
        query: str,
        role: str = "admin",
        datasource_id: Optional[str] = None,
        execute: bool = True,
        callbacks: List[Any] = None,
        policy_context: Optional[Dict] = None
    ) -> PipelineResult:
        """
        Executes the pipeline graph.
        """
        start_time = time.perf_counter()
        
        # 1. Load Policy Context if not provided (Handling Core Logic)
        if policy_context is None:
            try:
                policy_context = self._load_policy_context(role)
            except Exception as e:
                return PipelineResult(
                    success=False, 
                    error=f"Policy Load Error: {e}",
                    duration=time.perf_counter() - start_time
                )

        # 2. Execute Graph
        try:
            final_state = run_with_graph(
                registry=self.ds_registry,
                llm_registry=self.llm_registry,
                user_query=query,
                datasource_id=datasource_id,
                execute=execute,
                vector_store=self.vector_store,
                vector_store_path=settings.vector_store_path,
                callbacks=callbacks or [],
                user_context=policy_context
            )
            
            return PipelineResult(
                success=True,
                final_state=final_state,
                duration=time.perf_counter() - start_time
            )

        except Exception as e:
            return PipelineResult(
                success=False,
                error=str(e),
                traceback=traceback.format_exc(),
                duration=time.perf_counter() - start_time
            )

    def _load_policy_context(self, role: str) -> Dict[str, Any]:
        """Loads and validates RBAC policies for the given role."""
        import pathlib
        from nl2sql.configs import PolicyFileConfig
        
        policies_path = pathlib.Path(settings.policies_config_path)            
        if not policies_path.exists():
            raise FileNotFoundError(f"Policy config not found at {policies_path}")

        try:
            with open(policies_path, "r") as f:
                raw_json = f.read()
            
            policy_cfg = PolicyFileConfig.model_validate_json(raw_json)
            role_policy = policy_cfg.get_role(role)
            
            if not role_policy:
                available = list(policy_cfg.roles.keys())
                raise ValueError(f"Role '{role}' not defined. Available: {available}")
                
            return UserContext(roles = [role], **role_policy.model_dump())
            
        except Exception as e:
            raise NL2SQLError(f"Policy Context Error: {e}") from e
