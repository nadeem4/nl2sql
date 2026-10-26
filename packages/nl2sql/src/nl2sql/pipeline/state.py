from __future__ import annotations

from typing import Any, Dict, List, Optional, Annotated
import operator
import uuid

from pydantic import BaseModel, ConfigDict, Field

from nl2sql.common.errors import PipelineError, RunWarning
from nl2sql.hooks.approval import Approval, ApprovalRequest
from nl2sql.policies import UserContext
from nl2sql.pipeline.nodes.curated_matcher.schemas import VerifiedMatch
from nl2sql.pipeline.nodes.datasource_resolver.schemas import DatasourceResolverResponse
from nl2sql.pipeline.nodes.decomposer.schemas import DecomposerResponse, SubQuery
from nl2sql.pipeline.nodes.aggregator.schemas import AggregatorResponse
from nl2sql.pipeline.nodes.answer_synthesizer.schemas import AnswerSynthesizerResponse
from nl2sql.pipeline.nodes.ast_planner.schemas import ASTPlannerResponse, PlanModel
from nl2sql.pipeline.nodes.validator.schemas import LogicalValidatorResponse
from nl2sql.pipeline.nodes.generator.schemas import GeneratorResponse
from nl2sql.execution.contracts import ArtifactRef, ExecutorResponse
from nl2sql.execution.dag import ExecutionDAG
from nl2sql.pipeline.nodes.refiner.schemas import RefinerResponse
from nl2sql.pipeline.subgraphs.schemas import SubgraphOutput
from nl2sql.pipeline.nodes.schema_retriever.schema import Table
from nl2sql.pipeline.pinned import PinnedRun, PinnedSource
from nl2sql.knowledge.models import KnowledgeUsed
from nl2sql_sdk.models import Estimate


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
        verified_match (Optional[VerifiedMatch]): Set by the curated matcher when a verified query answered.
        datasource_resolver_response (Optional[DatasourceResolverResponse]): Output of resolver node.
        decomposer_response (Optional[DecomposerResponse]): Output of decomposer node.
        execution_dag (Optional[ExecutionDAG]): The graph of sub-queries the decomposer built.
        aggregator_response (Optional[AggregatorResponse]): Output of aggregator node.
        answer_synthesizer_response (Optional[AnswerSynthesizerResponse]): Output of synthesizer node.
        artifact_refs (Dict[str, ArtifactRef]): Artifact refs keyed by ExecutionDAG node_id.
        subgraph_outputs (Dict[str, SubgraphOutput]): Per-subgraph diagnostic outputs.
        errors (List[PipelineError]): List of errors encountered during execution.
        reasoning (List[Dict[str, Any]]): Log of reasoning steps from nodes.
        warnings (List[RunWarning]): Warnings emitted by nodes; the result's ``warnings``.
        subgraph_id (Optional[str]): ID of the subgraph execution.
        subgraph_name (Optional[str]): Name of the subgraph execution.
        pinned (Optional[PinnedRun]): A pinned decomposition and plans (supplied, approved or chosen); the
            run enters at the pinned entry instead of routing and decomposing.
        approvals (List[Approval]): The approvals the run was asked with; the hooks at execute.before see them.
        approvals_needed (List[ApprovalRequest]): What a person must approve; the run ends ``needs_approval``.
        clarify (bool): The run's ``clarify``: whether a run of one sub-query may end ``needs_clarification``
            (docs/architecture/wave_b.md, section 7). Off, the default reading runs.
    """
    model_config = ConfigDict(extra="ignore", arbitrary_types_allowed=True)

    trace_id: str = Field(default_factory=lambda: str(uuid.uuid4()), description="Distributed unique trace ID.")
    user_query: str = Field(description="Canonical user query.")
    user_context: UserContext = Field(default_factory=UserContext, description="User identity and permissions context.")
    datasource_id: Optional[str] = Field(default=None, description="Optional datasource override for resolution.")
    verified_match: Optional[VerifiedMatch] = Field(
        default=None, description="The verified query that answered the run, when one did.")
    datasource_resolver_response: Optional[DatasourceResolverResponse] = Field(default=None)
    decomposer_response: Optional[DecomposerResponse] = Field(default=None)
    execution_dag: Optional[ExecutionDAG] = Field(default=None)
    aggregator_response: Optional[AggregatorResponse] = Field(default=None)
    answer_synthesizer_response: Optional[AnswerSynthesizerResponse] = Field(default=None)
    artifact_refs: Annotated[Dict[str, ArtifactRef], update_results] = Field(default_factory=dict)
    subgraph_outputs: Annotated[Dict[str, SubgraphOutput], update_results] = Field(default_factory=dict)
    errors: Annotated[List[PipelineError], operator.add] = Field(default_factory=list)
    reasoning: Annotated[List[Dict[str, Any]], operator.add] = Field(default_factory=list)
    warnings: Annotated[List[RunWarning], operator.add] = Field(default_factory=list)
    subgraph_id: Optional[str] = Field(default=None)
    subgraph_name: Optional[str] = Field(default=None)
    pinned: Optional[PinnedRun] = Field(default=None)
    approvals: List[Approval] = Field(default_factory=list)
    approvals_needed: Annotated[List[ApprovalRequest], operator.add] = Field(default_factory=list)
    clarify: bool = True


class SubgraphExecutionState(BaseModel):
    model_config = ConfigDict(extra="ignore", arbitrary_types_allowed=True)

    trace_id: str
    # The run's question (after run.before), for the hooks a sub-query calls.
    user_query: str = ""
    sub_query: Optional[SubQuery] = None
    user_context: Optional[UserContext] = None
    subgraph_id: Optional[str] = None
    subgraph_name: Optional[str] = None
    relevant_tables: List[Table] = Field(default_factory=list)
    # A pinned plan, returned by the planner as ``plan_source=pinned_source``; never refined or cached.
    pinned_plan: Optional[PlanModel] = None
    pinned_source: Optional[PinnedSource] = None
    approvals: List[Approval] = Field(default_factory=list)
    # The knowledge this sub-query's prompts carried (the schema retriever fills it from K2). The planner's
    # other readings are on ast_planner_response.alternatives.
    knowledge_used: Optional[KnowledgeUsed] = None
    # The cost rule's estimate for this sub-query's SQL, when it asked for one (C2); the executor fills it.
    estimate: Optional[Estimate] = None
    # Whether the clarifier may end this sub-query with a question (L1): the run's ``clarify`` is on and this
    # is its only sub-query (D47). The sub-query wrapper sets it; otherwise the default plan runs.
    clarify: bool = False

    ast_planner_response: Optional[ASTPlannerResponse] = Field(default=None)
    logical_validator_response: Optional[LogicalValidatorResponse] = Field(default=None)
    generator_response: Optional[GeneratorResponse] = Field(default=None)
    executor_response: Optional[ExecutorResponse] = Field(default=None)
    refiner_response: Optional[RefinerResponse] = Field(default=None)

    retry_count: int = 0
    errors: Annotated[List[PipelineError], operator.add] = Field(default_factory=list)
    reasoning: Annotated[List[Dict[str, Any]], operator.add] = Field(default_factory=list)
    warnings: Annotated[List[RunWarning], operator.add] = Field(default_factory=list)
