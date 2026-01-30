from __future__ import annotations

from collections import defaultdict
from typing import Dict, Any, List, Optional, TYPE_CHECKING, Set

if TYPE_CHECKING:
    from nl2sql.pipeline.state import SubgraphExecutionState
    from nl2sql.pipeline.nodes.decomposer.schemas import SubQuery

from nl2sql.common.logger import get_logger
from nl2sql.context import NL2SQLContext
from .schema import Table, Column
from nl2sql_adapter_sdk.schema import SchemaSnapshot


logger = get_logger("schema_retriever")


class SchemaRetrieverNode:
    """Retrieves relevant schema chunks for planning context."""

    def __init__(self, ctx: NL2SQLContext):
        self.node_name = self.__class__.__name__.lower().replace("node", "")
        self.vector_store = ctx.vector_store
        self.schema_store = ctx.schema_store


    def _build_semantic_query(self, sub_query: SubQuery) -> str:
        parts: List[str] = []
        if sub_query and sub_query.intent:
            parts.append(sub_query.intent)

        if sub_query and sub_query.filters:
            filters_text = []
            for f in sub_query.filters:
                value = f.value
                if isinstance(value, list):
                    value = ", ".join(str(v) for v in value)
                filters_text.append(f"{f.attribute}={value}")
            if filters_text:
                parts.append("filters: " + "; ".join(filters_text))

        if sub_query and sub_query.group_by:
            group_by = ", ".join(g.attribute for g in sub_query.group_by)
            if group_by:
                parts.append("group_by: " + group_by)

        if sub_query and sub_query.expected_schema:
            expected = ", ".join(c.name for c in sub_query.expected_schema)
            if expected:
                parts.append("expected_schema: " + expected)

        if sub_query and sub_query.metrics:
            metrics = ", ".join(m.name for m in sub_query.metrics)
            if metrics:
                parts.append("metrics: " + metrics)

        return "\n".join(parts).strip()

    def _resolve_snapshot(self, datasource_id: str, schema_version: Optional[str]) -> Optional[SchemaSnapshot]:
        if not self.schema_store:
            return None
        if schema_version:
            return self.schema_store.get_snapshot(datasource_id, schema_version)
        return self.schema_store.get_latest_snapshot(datasource_id)

    def _build_tables_from_snapshot(
        self,
        snapshot: SchemaSnapshot,
        resolved_tables: Optional[Dict[str, Set[str]]] = None,
        schema_version: Optional[str] = None,
    ) -> List[Table]:
        logger.info(f"Building tables from snapshot: {snapshot.model_dump_json(indent=2)}")
        if not snapshot:
            return []

        tables_out: List[Table] = []
        table_keys = (
            list(snapshot.contract.tables.keys())
            if not resolved_tables
            else list(resolved_tables.keys())
        )

        for table_key in table_keys:
            table_contract = snapshot.contract.tables.get(table_key)
            table_metadata = snapshot.metadata.tables.get(table_key)
            if not table_contract:
                continue

            resolved_columns = resolved_tables[table_key] if resolved_tables else set()
            if not resolved_columns:
                resolved_columns = set(table_contract.columns.keys())

            columns: List[Column] = []
            for col_key, col_contract in table_contract.columns.items():
                if col_key not in resolved_columns:
                    continue
                col_metadata = table_metadata.columns.get(col_key) if table_metadata else None

                columns.append(
                    Column(
                        name=col_key,
                        type=col_contract.data_type,
                        stats=col_metadata.statistics.model_dump() if col_metadata and col_metadata.statistics else {},
                        description=col_metadata.description if col_metadata else ""
                    )
                )

            relationships = []
            for fk in table_contract.foreign_keys:
                relationships.append(
                    {
                        "from_table": table_contract.table.full_name,
                        "to_table": fk.referred_table.full_name,
                        "from_columns": fk.constrained_columns,
                        "to_columns": fk.referred_columns,
                        "cardinality": fk.cardinality,
                        "business_meaning": fk.business_meaning,
                    }
                )

            primary_keys = [
                col.name for col in table_contract.columns.values() if col.is_primary_key
            ]

            table = Table(
                    name=table_contract.table.table_name,
                    columns=columns,
                    description=table_metadata.description if table_metadata else "",
                    primary_key=primary_keys,
                    schema_version=schema_version,
                    relationships=relationships,
                )

            tables_out.append( table )        

        return tables_out

    def __call__(self, state: SubgraphExecutionState) -> Dict[str, Any]:
        try:
            sub_query = state.sub_query
            if not sub_query:
                return {"relevant_tables": []}

            datasource_id = sub_query.datasource_id
            schema_version = sub_query.schema_version
            query = self._build_semantic_query(sub_query)

            tables: Dict[str, Set[str]] = defaultdict(set)
            schema_docs = []
            column_docs = []

            if self.vector_store:
                schema_docs = self.vector_store.retrieve_schema_context(
                    query, datasource_id, k=8
                )
                if schema_docs:
                    for doc in schema_docs:
                        table = doc.metadata.get("table")
                        if table:
                            tables[table].update([])
                else:
                    column_docs = self.vector_store.retrieve_column_candidates(
                        query, datasource_id, k=8
                    )
                    for doc in column_docs:
                        table = doc.metadata.get("table")
                        column = doc.metadata.get("column")
                        if not table:
                            continue
                        tables[table].update([])
                        if column:
                            tables[table].add(column)

            planning_docs = []
            if self.vector_store and tables:
                planning_docs = self.vector_store.retrieve_planning_context(
                    query, datasource_id, list(tables.keys()), k=12
                )

            for doc in planning_docs:
                doc_type = doc.metadata.get("type")
                if doc_type == "schema.column":
                    table = doc.metadata.get("table")
                    column = doc.metadata.get("column")
                    if table and column:
                        tables[table].add(column)

                if doc_type == "schema.relationship":
                    from_table = doc.metadata.get("from_table")
                    to_table = doc.metadata.get("to_table")
                    if from_table:
                        tables[from_table].update(doc.metadata.get("from_columns"))
                    if to_table:
                        tables[to_table].update(doc.metadata.get("to_columns"))

            if not tables:
                snapshot = self._resolve_snapshot(datasource_id, schema_version)
                relevant_tables = self._build_tables_from_snapshot(
                    snapshot,
                    resolved_tables=None,
                    schema_version=schema_version,
                )
                logger.info(f"Length of relevant tables for planning: {len(relevant_tables)}")
                return {
                    "relevant_tables": relevant_tables,
                    "reasoning": [
                        {
                            "node": self.node_name,
                            "content": "Vector retrieval produced no candidates. Using full schema snapshot.",
                            "type": "warning",
                        }
                    ],
                    "warnings": [
                        {
                            "node": self.node_name,
                            "content": "Vector retrieval produced no candidates. Using full schema snapshot.",
                        }
                    ],
                }

            snapshot = self._resolve_snapshot(datasource_id, schema_version)
            relevant_tables = self._build_tables_from_snapshot(
                snapshot,
                resolved_tables=tables,
                schema_version=schema_version,
            )


            logger.info(f"Length of relevant tables for planning: {len(relevant_tables)}")

            return {
                "relevant_tables": relevant_tables,
                "reasoning": [
                    {
                        "node": self.node_name,
                        "content": (
                            f"Retrieved {len(relevant_tables)} tables "
                            f"with {sum(len(t.columns) for t in relevant_tables)} columns."
                        ),
                    }
                ],
            }
        except Exception as exc:
            logger.error(f"Schema retrieval failed: {exc}")
            return {
                "relevant_tables": [],
                "reasoning": [
                    {
                        "node": self.node_name,
                        "content": f"Schema retrieval failed: {exc}",
                        "type": "error",
                    }
                ],
            }
