from nl2sql.pipeline.nodes.datasource_resolver.schemas import DatasourceResolverResponse, ResolvedDatasource
from nl2sql.pipeline.nodes.decomposer.schemas import DecomposerResponse, SubQuery, ExpectedColumn
from nl2sql.pipeline.nodes.global_planner.schemas import (
    GlobalPlannerResponse,
    ExecutionDAG,
    LogicalNode,
    RelationSchema,
    ColumnSpec,
)
from nl2sql.pipeline.nodes.ast_planner.schemas import ASTPlannerResponse, PlanModel
from nl2sql.pipeline.nodes.generator.schemas import GeneratorResponse
from nl2sql.pipeline.nodes.executor.schemas import ExecutorResponse, ExecutionModel
from nl2sql.pipeline.nodes.validator.schemas import LogicalValidatorResponse, PhysicalValidatorResponse
from nl2sql.pipeline.nodes.refiner.schemas import RefinerResponse
from nl2sql.pipeline.nodes.aggregator.schemas import AggregatorResponse
from nl2sql.pipeline.nodes.answer_synthesizer.schemas import AnswerSynthesizerResponse


def test_top_level_response_models_construct():
    resolver = DatasourceResolverResponse(
        resolved_datasources=[ResolvedDatasource(datasource_id="ds1", metadata={})],
        allowed_datasource_ids=["ds1"],
    )
    decomposer = DecomposerResponse(
        sub_queries=[
            SubQuery(
                id="sq1",
                datasource_id="ds1",
                intent="list users",
                expected_schema=[ExpectedColumn(name="id", dtype="int")],
            )
        ],
        combine_groups=[],
        post_combine_ops=[],
        unmapped_subqueries=[],
    )
    planner = GlobalPlannerResponse(
        execution_dag=ExecutionDAG(
            nodes=[
                LogicalNode(
                    node_id="sq1",
                    kind="scan",
                    output_schema=RelationSchema(columns=[ColumnSpec(name="id")]),
                )
            ],
            edges=[],
        )
    )

    assert resolver.allowed_datasource_ids == ["ds1"]
    assert decomposer.sub_queries[0].id == "sq1"
    assert planner.execution_dag.nodes[0].node_id == "sq1"


def test_subgraph_response_models_construct():
    ast = ASTPlannerResponse(plan=PlanModel(query_type="READ", tables=[], select_items=[], joins=[]))
    gen = GeneratorResponse(sql_draft="SELECT 1")
    exec_resp = ExecutorResponse(execution=ExecutionModel(row_count=1, rows=[{"id": 1}], columns=["id"]))
    logical = LogicalValidatorResponse()
    physical = PhysicalValidatorResponse()
    refiner = RefinerResponse(feedback="adjust plan")

    assert ast.plan is not None
    assert gen.sql_draft == "SELECT 1"
    assert exec_resp.execution.row_count == 1
    assert logical.errors == []
    assert physical.errors == []
    assert refiner.feedback == "adjust plan"


def test_aggregator_and_synthesizer_response_models_construct():
    agg = AggregatorResponse(terminal_results={"sq1": [{"id": 1}]}, computed_refs={"sq1": "ref"})
    synth = AnswerSynthesizerResponse(final_answer={"summary": "ok"})

    assert agg.terminal_results["sq1"][0]["id"] == 1
    assert synth.final_answer["summary"] == "ok"
