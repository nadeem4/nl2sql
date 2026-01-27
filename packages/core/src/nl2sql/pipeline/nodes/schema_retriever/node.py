from __future__ import annotations

from typing import Dict, Any, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from nl2sql.pipeline.state import SubgraphExecutionState

from nl2sql.common.logger import get_logger
from nl2sql.context import NL2SQLContext
from nl2sql.schema import Table, Column

logger = get_logger("schema_retriever")


class SchemaRetrieverNode:
    """Retrieves relevant schema chunks for planning context."""

    def __init__(self, ctx: NL2SQLContext):
        self.node_name = self.__class__.__name__.lower().replace("node", "")
        self.vector_store = ctx.vector_store
        self.schema_store = ctx.schema_store

    def _table_name_from_full(self, table_full: str) -> str:
        if "." in table_full:
            return table_full.rsplit(".", 1)[-1]
        return table_full

    def _column_parts(self, column_full: str) -> tuple[Optional[str], Optional[str]]:
        if not column_full:
            return None, None
        if "." not in column_full:
            return None, column_full
        table_full, column_name = column_full.rsplit(".", 1)
        return table_full, column_name

    def _build_tables_from_snapshot(
        self,
        datasource_id: str,
        allowed_tables: Optional[set[str]],
    ) -> List[Table]:
        snapshot = self.schema_store.get_latest_snapshot(datasource_id) if self.schema_store else None
        if not snapshot:
            return []

        tables: List[Table] = []
        for table_contract in snapshot.contract.tables.values():
            table_full = (
                f"{table_contract.table.schema_name}.{table_contract.table.table_name}"
            )
            if allowed_tables and table_full not in allowed_tables:
                continue
            columns = [
                Column(name=col.name, type=col.data_type)
                for col in table_contract.columns.values()
            ]
            tables.append(Table(name=table_contract.table.table_name, columns=columns))

        return tables

    def __call__(self, state: SubgraphExecutionState) -> Dict[str, Any]:
        try:
            sub_query = state.sub_query
            datasource_id = sub_query.datasource_id 

            query = sub_query.intent 

            table_full_names: List[str] = []
            if self.vector_store:
                schema_docs = self.vector_store.retrieve_schema_context(
                    query, datasource_id, k=8
                )
                for doc in schema_docs:
                    table_full = doc.metadata.get("table")
                    if table_full and table_full not in table_full_names:
                        table_full_names.append(table_full)

            table_full_set = set(table_full_names)
            planning_docs = []
            if self.vector_store and table_full_names:
                planning_docs = self.vector_store.retrieve_planning_context(
                    query, datasource_id, table_full_names, k=12
                )

            table_columns: Dict[str, Dict[str, Column]] = {}

            for doc in planning_docs:
                doc_type = doc.metadata.get("type")
                if doc_type == "schema.column":
                    table_full = doc.metadata.get("table")
                    column_full = doc.metadata.get("column")
                    if not table_full and column_full:
                        table_full, _ = self._column_parts(column_full)
                    if not table_full or not column_full:
                        continue
                    table_name = self._table_name_from_full(table_full)
                    _, column_name = self._column_parts(column_full)
                    if not column_name:
                        continue
                    dtype = doc.metadata.get("dtype")
                    table_columns.setdefault(table_name, {})
                    table_columns[table_name][column_name] = Column(
                        name=column_name, type=dtype
                    )

                elif doc_type == "schema.relationship":
                    from_table = doc.metadata.get("from_table")
                    to_table = doc.metadata.get("to_table")
                    from_columns = doc.metadata.get("from_columns") or []
                    to_columns = doc.metadata.get("to_columns") or []
                    if from_table:
                        table_name = self._table_name_from_full(from_table)
                        table_columns.setdefault(table_name, {})
                        for col in from_columns:
                            table_columns[table_name].setdefault(col, Column(name=col))
                    if to_table:
                        table_name = self._table_name_from_full(to_table)
                        table_columns.setdefault(table_name, {})
                        for col in to_columns:
                            table_columns[table_name].setdefault(col, Column(name=col))

            if not planning_docs:
                tables = self._build_tables_from_snapshot(datasource_id, table_full_set)
                return {
                    "relevant_tables": tables,
                    "reasoning": [
                        {
                            "node": self.node_name,
                            "content": "Fallback to schema store for table definitions.",
                            "type": "warning",
                        }
                    ],
                }

            relevant_tables: List[Table] = []
            for table_full in table_full_names:
                table_name = self._table_name_from_full(table_full)
                columns = list(table_columns.get(table_name, {}).values())
                columns = sorted(columns, key=lambda c: c.name)
                relevant_tables.append(Table(name=table_name, columns=columns))

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
