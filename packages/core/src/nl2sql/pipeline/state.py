from __future__ import annotations

from typing import Any, Dict, List, Optional, Annotated
import operator
import uuid

from pydantic import BaseModel, ConfigDict, Field

from nl2sql.common.errors import PipelineError
from nl2sql.auth import UserContext
from nl2sql.pipeline.nodes.datasource_resolver.schemas import DatasourceResolverResponse
from nl2sql.pipeline.nodes.decomposer.schemas import DecomposerResponse, SubQuery
from nl2sql.pipeline.nodes.global_planner.schemas import GlobalPlannerResponse
from nl2sql.pipeline.nodes.aggregator.schemas import AggregatorResponse
from nl2sql.pipeline.nodes.answer_synthesizer.schemas import AnswerSynthesizerResponse
from nl2sql.pipeline.nodes.ast_planner.schemas import ASTPlannerResponse
from nl2sql.pipeline.nodes.validator.schemas import LogicalValidatorResponse, PhysicalValidatorResponse
from nl2sql.pipeline.nodes.generator.schemas import GeneratorResponse
from nl2sql.execution.contracts import ArtifactRef, ExecutorResponse
from nl2sql.pipeline.nodes.refiner.schemas import RefinerResponse
from nl2sql.pipeline.subgraphs.schemas import SubgraphOutput
from nl2sql.schema import Table


def update_results(current: Dict, new: Dict) -> Dict:
    """Reducer to merge execution results from parallel branches."""
    if current is None:
        return new
    return {**current, **new}


class GraphState(BaseModel):
    """Represents the shared state of the NL2SQL pipeline execution graph.

    Attributes:
        trace_id (str): Distributed unique trace ID.
        user_query (str): Canonical user query.
        user_context (UserContext): User identity and permissions context.
        datasource_id (Optional[str]): Optional datasource override for resolution.
        datasource_resolver_response (Optional[DatasourceResolverResponse]): Output of resolver node.
        decomposer_response (Optional[DecomposerResponse]): Output of decomposer node.
        global_planner_response (Optional[GlobalPlannerResponse]): Output of planner node.
        aggregator_response (Optional[AggregatorResponse]): Output of aggregator node.
        answer_synthesizer_response (Optional[AnswerSynthesizerResponse]): Output of synthesizer node.
        artifact_refs (Dict[str, ArtifactRef]): Artifact refs keyed by ExecutionDAG node_id.
        subgraph_outputs (Dict[str, SubgraphOutput]): Per-subgraph diagnostic outputs.
        errors (List[PipelineError]): List of errors encountered during execution.
        reasoning (List[Dict[str, Any]]): Log of reasoning steps from nodes.
        warnings (List[Dict[str, Any]]): Warning messages emitted by nodes.
        subgraph_id (Optional[str]): ID of the subgraph execution.
        subgraph_name (Optional[str]): Name of the subgraph execution.
    """
    model_config = ConfigDict(extra="ignore", arbitrary_types_allowed=True)

    trace_id: str = Field(default_factory=lambda: str(uuid.uuid4()), description="Distributed unique trace ID.")
    user_query: str = Field(description="Canonical user query.")
    user_context: UserContext = Field(default_factory=UserContext, description="User identity and permissions context.")
    datasource_id: Optional[str] = Field(default=None, description="Optional datasource override for resolution.")
    datasource_resolver_response: Optional[DatasourceResolverResponse] = Field(default=None)
    decomposer_response: Optional[DecomposerResponse] = Field(default=None)
    global_planner_response: Optional[GlobalPlannerResponse] = Field(default=None)
    aggregator_response: Optional[AggregatorResponse] = Field(default=None)
    answer_synthesizer_response: Optional[AnswerSynthesizerResponse] = Field(default=None)
    artifact_refs: Annotated[Dict[str, ArtifactRef], update_results] = Field(default_factory=dict)
    subgraph_outputs: Annotated[Dict[str, SubgraphOutput], update_results] = Field(default_factory=dict)
    errors: Annotated[List[PipelineError], operator.add] = Field(default_factory=list)
    reasoning: Annotated[List[Dict[str, Any]], operator.add] = Field(default_factory=list)
    warnings: Annotated[List[Dict[str, Any]], operator.add] = Field(default_factory=list)
    subgraph_id: Optional[str] = Field(default=None)
    subgraph_name: Optional[str] = Field(default=None)


class SubgraphExecutionState(BaseModel):
    model_config = ConfigDict(extra="ignore", arbitrary_types_allowed=True)

    trace_id: str
    sub_query: Optional[SubQuery] = None
    user_context: Optional[UserContext] = None
    subgraph_id: Optional[str] = None
    subgraph_name: Optional[str] = None
    relevant_tables: List[Table] = Field(default_factory=list)

    ast_planner_response: Optional[ASTPlannerResponse] = Field(default=None)
    logical_validator_response: Optional[LogicalValidatorResponse] = Field(default=None)
    physical_validator_response: Optional[PhysicalValidatorResponse] = Field(default=None)
    generator_response: Optional[GeneratorResponse] = Field(default=None)
    executor_response: Optional[ExecutorResponse] = Field(default=None)
    refiner_response: Optional[RefinerResponse] = Field(default=None)

    retry_count: int = 0
    errors: Annotated[List[PipelineError], operator.add] = Field(default_factory=list)
    reasoning: Annotated[List[Dict[str, Any]], operator.add] = Field(default_factory=list)
    warnings: Annotated[List[Dict[str, Any]], operator.add] = Field(default_factory=list)
