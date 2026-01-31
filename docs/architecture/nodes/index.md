# Node Catalog

This section indexes all control-graph nodes and SQL agent subgraph nodes. Each node has a dedicated reference page with inputs, outputs, contracts, and limitations.

For pipeline-level wiring, see `../pipeline.md`. For subgraph wiring and lifecycle, see `../subgraphs/sql_agent.md`.

## Control graph nodes (main pipeline)

- [DatasourceResolverNode](datasource_resolver_node.md)
- [DecomposerNode](decomposer_node.md)
- [GlobalPlannerNode](global_planner_node.md)
- [EngineAggregatorNode](engine_aggregator_node.md)
- [AnswerSynthesizerNode](answer_synthesizer_node.md)

## SQL agent subgraph nodes

- [SchemaRetrieverNode](schema_retriever_node.md)
- [ASTPlannerNode](ast_planner_node.md)
- [LogicalValidatorNode](logical_validator_node.md)
- [PhysicalValidatorNode](physical_validator_node.md)
- [GeneratorNode](generator_node.md)
- [ExecutorNode](executor_node.md)
- [RefinerNode](refiner_node.md)
