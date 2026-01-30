from typing import List

from .models import (
    BaseChunk,
    DatasourceChunk,
    TableChunk,
    ColumnChunk,
    RelationshipChunk,
    MetricChunk,
)
from nl2sql.schema import SchemaSnapshot
from nl2sql_adapter_sdk.schema import TableRef, ColumnRef


class SchemaChunkBuilder:
    """
    Builds schema chunks from a SchemaSnapshot.

    This class performs a pure transformation from SchemaSnapshot
    to a list of schema chunks used for retrieval and grounding.

    No inference, optimization, or database access is performed.
    """

    def __init__(
        self,
        ds_id: str,
        schema_snapshot: SchemaSnapshot,
        schema_version: str,
        questions: List[str],
    ):
        """
        Initializes the SchemaChunkBuilder.

        Args:
            ds_id: Datasource identifier.
            schema_snapshot: Snapshot containing schema contract and metadata.
            schema_version: Version identifier for the schema snapshot.
            questions: Example user questions for grounding the datasource.
        """
        self.ds_id = ds_id
        self.schema_snapshot = schema_snapshot
        self.schema_version = schema_version
        self.questions = questions

    def build(self) -> List[BaseChunk]:
        """
        Builds all schema chunks.

        Returns:
            List of schema chunks derived from the schema snapshot.
        """
        chunks: List[BaseChunk] = []

        chunks.extend(self._build_datasource_chunks())
        chunks.extend(self._build_table_chunks())
        chunks.extend(self._build_column_chunks())
        chunks.extend(self._build_relationship_chunks())
        chunks.extend(self._build_metric_chunks())

        return chunks

    def _build_datasource_chunks(self) -> List[DatasourceChunk]:
        """
        Builds datasource-level chunks.

        Returns:
            List containing a single DatasourceChunk.
        """
        md = self.schema_snapshot.metadata

        return [
            DatasourceChunk(
                id=f"schema.datasource:{md.datasource_id}:{self.schema_version}",
                datasource_id=md.datasource_id,
                description=md.description or "",
                domains=md.domains,
                schema_version=self.schema_version,
                examples=self.questions,
            )
        ]

    def _build_table_chunks(self) -> List[TableChunk]:
        """
        Builds table-level chunks.

        Returns:
            List of TableChunk objects.
        """
        chunks: List[TableChunk] = []

        contract = self.schema_snapshot.contract
        metadata = self.schema_snapshot.metadata

        for table_key, table_contract in contract.tables.items():
            table_md = metadata.tables.get(table_key)
            table_ref = TableRef(
                schema_name=table_contract.table.schema_name,
                table_name=table_contract.table.table_name,
            )

            primary_keys = [
                c.name for c in table_contract.columns.values() if c.is_primary_key
            ]

            column_names = sorted(table_contract.columns.keys())

            foreign_keys = [
                f"{table_ref.full_name} -> {fk.referred_table.full_name}"
                for fk in table_contract.foreign_keys
            ]

            chunks.append(
                TableChunk(
                    id=f"schema.table:{table_ref.full_name}:{self.schema_version}",
                    datasource_id=self.ds_id,
                    table=table_ref,
                    description=table_md.description if table_md else None,
                    primary_key=primary_keys,
                        columns=column_names,
                    foreign_keys=foreign_keys,
                    row_count=table_md.row_count if table_md else None,
                    schema_version=self.schema_version,
                )
            )

        return chunks

    def _build_column_chunks(self) -> List[ColumnChunk]:
        """
        Builds column-level chunks.

        Returns:
            List of ColumnChunk objects.
        """
        chunks: List[ColumnChunk] = []

        contract = self.schema_snapshot.contract
        metadata = self.schema_snapshot.metadata

        for table_key, table_contract in contract.tables.items():
            table_md = metadata.tables.get(table_key)
            table_ref = TableRef(
                schema_name=table_contract.table.schema_name,
                table_name=table_contract.table.table_name,
            )

            for column_name, column_contract in table_contract.columns.items():
                column_md = table_md.columns.get(column_name) if table_md else None

                column_ref = ColumnRef(
                    table=table_ref,
                    column_name=column_name,
                )

                chunks.append(
                    ColumnChunk(
                        id=f"schema.column:{column_ref.table.full_name}:{column_ref.column_name}:{self.schema_version}",
                        datasource_id=self.ds_id,
                        column=column_ref,
                        dtype=column_contract.data_type,
                        description=column_md.description if column_md else None,
                        column_stats=(
                            column_md.statistics.model_dump()
                            if column_md and column_md.statistics
                            else {}
                        ),
                        synonyms=column_md.synonyms if column_md else None,
                        pii=column_md.pii if column_md else False,
                        schema_version=self.schema_version,
                    )
                )

        return chunks

    def _build_relationship_chunks(self) -> List[RelationshipChunk]:
        """
        Builds relationship-level chunks.

        Returns:
            List of RelationshipChunk objects with unknown cardinality.
        """
        chunks: List[RelationshipChunk] = []

        contract = self.schema_snapshot.contract

        for table_contract in contract.tables.values():
            from_table = TableRef(
                schema_name=table_contract.table.schema_name,
                table_name=table_contract.table.table_name,
            )

            for fk in table_contract.foreign_keys:
                to_table = TableRef(
                    schema_name=fk.referred_table.schema_name,
                    table_name=fk.referred_table.table_name,
                )

                chunks.append(
                    RelationshipChunk(
                        id=(
                            f"schema.relationship:"
                            f"{from_table.full_name}"
                            f"->{to_table.full_name}:"
                            f"{self.schema_version}"
                        ),
                        datasource_id=self.ds_id,
                        from_table=from_table,
                        to_table=to_table,
                        from_columns=fk.constrained_columns,
                        to_columns=fk.referred_columns,
                        cardinality="unknown",
                        business_meaning=None,
                        schema_version=self.schema_version,
                    )
                )

        return chunks

    def _build_metric_chunks(self) -> List[MetricChunk]:
        """
        Builds metric-level chunks.

        Returns:
            Empty list, as metrics are not part of SchemaSnapshot.
        """
        return []
