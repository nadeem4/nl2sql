from __future__ import annotations

from collections import defaultdict
from typing import Callable, Dict, Any, List, Optional, TYPE_CHECKING, Set

if TYPE_CHECKING:
    from nl2sql.pipeline.state import SubgraphExecutionState
    from nl2sql.pipeline.nodes.decomposer.schemas import SubQuery

from nl2sql.auth.rbac import table_allowed
from nl2sql.common.logger import get_logger
from nl2sql.common.settings import settings
from nl2sql.context import NL2SQLContext
from .schema import Table, Column
from nl2sql_adapter_sdk.schema import SchemaSnapshot, TableRef


logger = get_logger("schema_retriever")


class SchemaRetrieverNode:
    """Retrieves relevant schema chunks for planning context."""

    # Entries MMR picks: tables (or columns, when no table matches), then the
    # columns and relationships of the picked tables.
    TABLE_K = 8
    PLANNING_K = 12

    def __init__(self, ctx: NL2SQLContext):
        self.node_name = self.__class__.__name__.lower().replace("node", "")
        self.vector_store = ctx.vector_store
        self.schema_store = ctx.schema_store
        self.rbac = getattr(ctx, "rbac", None)

    def _readable(self, state: SubgraphExecutionState, datasource_id: str) -> Callable[[TableRef], bool]:
        """Whether the caller's role may read a table's data.

        Structure yes, data no: the planner sees every table's name, columns,
        types, keys and relationships, so a question that needs a forbidden
        table still plans against it and the validator refuses it. What a
        forbidden table loses is its data: sample values and column statistics
        never reach the prompt, the LLM provider or the run trace. The same
        ``table_allowed`` rule the validator enforces decides it. With no RBAC
        configured nothing is readable, so the default is to strip.
        """
        if self.rbac is None:
            return lambda _ref: False
        allowed = self.rbac.get_allowed_tables(state.user_context)
        return lambda ref: table_allowed(allowed, datasource_id, ref.table_name)

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
        *,
        readable: Callable[[TableRef], bool],
    ) -> List[Table]:
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

            with_data = readable(table_contract.table)
            columns: List[Column] = []
            for col_key, col_contract in table_contract.columns.items():
                if col_key not in resolved_columns:
                    continue
                col_metadata = table_metadata.columns.get(col_key) if table_metadata else None

                columns.append(
                    Column(
                        name=col_key,
                        type=col_contract.data_type,
                        stats=(
                            col_metadata.statistics.model_dump()
                            if with_data and col_metadata and col_metadata.statistics
                            else {}
                        ),
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

    @staticmethod
    def _withhold_unreadable(
        searches: List[Dict[str, Any]],
        snapshot: Optional[SchemaSnapshot],
        readable: Callable[[TableRef], bool],
    ) -> None:
        """Structure yes, data no, in the trace too.

        A column entry's embedded text carries the column's statistics and
        sample values, so its similarity to the question says something about
        that data. For a table the role cannot read, the entry keeps its name,
        rank and whether it was picked, and loses its scores.
        """
        tables = snapshot.contract.tables if snapshot else {}
        for search in searches:
            for entry in search.get("pool", []):
                if entry.get("type") != "schema.column":
                    continue
                contract = tables.get(entry.get("table"))
                if contract is not None and readable(contract.table):
                    continue
                entry.update(similarity=None, distance=None, mmr_score=None, redundancy=None,
                             withheld="The role cannot read this table's data.")

    @staticmethod
    def _retrieval(query: str, searches: List[Dict[str, Any]], tables_out: List[Table],
                   reason: Optional[str] = None) -> Dict[str, Any]:
        """What the vector searches retrieved and what survived, for the run trace.

        Not a state field: LangGraph drops the key, the trace keeps the node's
        whole return.
        """
        record: Dict[str, Any] = {"skipped": reason is not None}
        if reason is not None:
            record["reason"] = reason
        else:
            record.update(query=query, searches=searches)
        record["tables"] = [{"table": t.name, "columns": [c.name for c in t.columns]} for t in tables_out]
        return record

    def __call__(self, state: SubgraphExecutionState) -> Dict[str, Any]:
        try:
            sub_query = state.sub_query
            if not sub_query:
                return {"relevant_tables": []}

            datasource_id = sub_query.datasource_id
            schema_version = sub_query.schema_version
            query = self._build_semantic_query(sub_query)

            snapshot = self._resolve_snapshot(datasource_id, schema_version)
            readable = self._readable(state, datasource_id)
            limit = settings.schema_retrieval_full_snapshot_max_tables
            if snapshot and len(snapshot.contract.tables) <= limit:
                tables_out = self._build_tables_from_snapshot(
                    snapshot, resolved_tables=None, schema_version=schema_version, readable=readable
                )
                return {
                    "relevant_tables": tables_out,
                    "retrieval": self._retrieval(
                        query, [], tables_out,
                        reason=(
                            f"The schema has {len(snapshot.contract.tables)} tables (limit {limit}), "
                            "so the full schema is sent without retrieval."
                        ),
                    ),
                    "reasoning": [
                        {
                            "node": self.node_name,
                            "content": (
                                f"Schema has {len(tables_out)} tables (limit {limit}); "
                                "using the full snapshot without vector retrieval."
                            ),
                        }
                    ],
                }

            tables: Dict[str, Set[str]] = defaultdict(set)
            searches: List[Dict[str, Any]] = []
            schema_docs = []
            column_docs = []

            if self.vector_store:
                schema_docs = self.vector_store.retrieve_schema_context(
                    query, datasource_id, k=self.TABLE_K, explain=searches
                )
                if schema_docs:
                    for doc in schema_docs:
                        table = doc.metadata.get("table")
                        if table:
                            tables[table].update([])
                else:
                    column_docs = self.vector_store.retrieve_column_candidates(
                        query, datasource_id, k=self.TABLE_K, explain=searches
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
                    query, datasource_id, list(tables.keys()), k=self.PLANNING_K, explain=searches
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

            self._withhold_unreadable(searches, snapshot, readable)

            if not tables:
                relevant_tables = self._build_tables_from_snapshot(
                    snapshot,
                    resolved_tables=None,
                    schema_version=schema_version,
                    readable=readable,
                )
                return {
                    "relevant_tables": relevant_tables,
                    "retrieval": self._retrieval(
                        query, searches, relevant_tables,
                        reason=None if self.vector_store else "No vector store is configured.",
                    ),
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

            relevant_tables = self._build_tables_from_snapshot(
                snapshot,
                resolved_tables=tables,
                schema_version=schema_version,
                readable=readable,
            )



            return {
                "relevant_tables": relevant_tables,
                "retrieval": self._retrieval(query, searches, relevant_tables),
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
