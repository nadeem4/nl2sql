import hashlib
from typing import Dict, List, Optional, Sequence, Tuple

from .models import (
    BaseChunk,
    DatasourceChunk,
    DatasourceExampleChunk,
    TableChunk,
    ColumnChunk,
    RelationshipChunk,
    MetricChunk,
    TermChunk,
)
from nl2sql.schema import SchemaSnapshot
from nl2sql_sdk.knowledge import ColumnKnowledge, DatasourceKnowledge
from nl2sql_sdk.schema import TableRef, ColumnRef


def build_example_chunks(datasource_id: str, questions: Sequence[str],
                         schema_version: str) -> List[DatasourceExampleChunk]:
    """One ``schema.datasource_example`` entry per example question, in the caller's order.

    The caller assembles ``questions``: the datasource's ``sample_questions``
    and the glossary's ``examples``. A blank question is skipped and a
    repeated one kept once. The id hashes the question, so the same question
    keeps its id from build to build.
    """
    chunks: List[DatasourceExampleChunk] = []
    seen = set()
    for question in questions:
        text = (question or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
        chunks.append(DatasourceExampleChunk(
            id=f"schema.datasource_example:{datasource_id}:{digest}:{schema_version}",
            datasource_id=datasource_id, question=text, schema_version=schema_version,
        ))
    return chunks


class SchemaChunkBuilder:
    """
    Builds schema chunks from a SchemaSnapshot.

    This class performs a pure transformation from SchemaSnapshot
    to a list of schema chunks used for retrieval and grounding.

    No inference, optimization, or database access is performed.

    ``knowledge`` is the datasource's merged knowledge (wave_b.md, section 4):
    a description is the glossary's, else the snapshot's (the database's
    comment); a column also gets the glossary's synonyms and role; a glossary
    join is a relationship entry of its own; each term is a ``knowledge.term``
    entry; the glossary's ``examples`` join the example questions.
    """

    def __init__(
        self,
        ds_id: str,
        schema_snapshot: SchemaSnapshot,
        schema_version: str,
        questions: List[str],
        knowledge: Optional[DatasourceKnowledge] = None,
        dialect: Optional[str] = None,
    ):
        """
        Initializes the SchemaChunkBuilder.

        Args:
            ds_id: Datasource identifier.
            schema_snapshot: Snapshot containing schema contract and metadata.
            schema_version: Version identifier for the schema snapshot.
            questions: Example questions the datasource answers, each indexed
                as its own entry (:func:`build_example_chunks`).
            knowledge: The datasource's merged knowledge; none for a build
                without a glossary.
            dialect: The sqlglot dialect, to compare the types of a glossary
                join's columns.
        """
        self.ds_id = ds_id
        self.schema_snapshot = schema_snapshot
        self.schema_version = schema_version
        self.questions = questions
        self.knowledge = knowledge or DatasourceKnowledge()
        self.dialect = dialect
        self._tables, self._columns = self._resolved_knowledge()

    def _resolved_knowledge(self) -> Tuple[Dict[str, str], Dict[Tuple[str, str], ColumnKnowledge]]:
        """The glossary's table descriptions and column entries, keyed as the snapshot keys them.

        A name that does not resolve is left out: ``engine.knowledge.show``
        reports it as a problem.
        """
        from nl2sql.knowledge.resolve import resolve_column, resolve_table

        key_of = {contract.table.full_name: key for key, contract in self.schema_snapshot.contract.tables.items()}
        tables: Dict[str, str] = {}
        columns: Dict[Tuple[str, str], ColumnKnowledge] = {}
        for name, item in self.knowledge.tables.items():
            table, _ = resolve_table(name, self.schema_snapshot)
            if table is not None and item.description:
                tables[key_of[table.table.full_name]] = item.description
        for name, item in self.knowledge.columns.items():
            table, column, _ = resolve_column(name, self.schema_snapshot)
            if column is not None:
                columns[(key_of[table.table.full_name], column.name)] = item
        return tables, columns

    def build(self) -> List[BaseChunk]:
        """
        Builds all schema chunks.

        Returns:
            List of schema chunks derived from the schema snapshot.
        """
        chunks: List[BaseChunk] = []

        chunks.extend(self._build_datasource_chunks())
        chunks.extend(build_example_chunks(self.ds_id, [*self.questions, *self.knowledge.examples],
                                           self.schema_version))
        chunks.extend(self._build_table_chunks())
        chunks.extend(self._build_column_chunks())
        chunks.extend(self._build_relationship_chunks())
        chunks.extend(self._build_glossary_join_chunks())
        chunks.extend(self._build_term_chunks())
        chunks.extend(self._build_metric_chunks())

        return chunks

    def _build_datasource_chunks(self) -> List[DatasourceChunk]:
        """
        Builds datasource-level chunks.

        Returns:
            List containing a single DatasourceChunk: the description and
            domains. The example questions are entries of their own.
        """
        md = self.schema_snapshot.metadata

        return [
            DatasourceChunk(
                id=f"schema.datasource:{md.datasource_id}:{self.schema_version}",
                datasource_id=md.datasource_id,
                description=md.description or "",
                domains=md.domains,
                schema_version=self.schema_version,
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
                    description=self._tables.get(table_key) or (table_md.description if table_md else None),
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
                said = self._columns.get((table_key, column_name))
                synonyms = list(column_md.synonyms or []) if column_md else []
                synonyms += [s for s in (said.synonyms if said else []) if s not in synonyms]

                chunks.append(
                    ColumnChunk(
                        id=f"schema.column:{column_ref.table.full_name}:{column_ref.column_name}:{self.schema_version}",
                        datasource_id=self.ds_id,
                        column=column_ref,
                        dtype=column_contract.data_type,
                        description=(said.description if said and said.description else None)
                        or (column_md.description if column_md else None),
                        column_stats=(
                            column_md.statistics.model_dump()
                            if column_md and column_md.statistics
                            else {}
                        ),
                        synonyms=synonyms or None,
                        role=said.role if said else None,
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

    def _build_glossary_join_chunks(self) -> List[RelationshipChunk]:
        """One relationship entry per glossary join that resolves, beside the database's foreign keys."""
        from nl2sql.knowledge.resolve import resolve_join

        chunks: List[RelationshipChunk] = []
        for join in self.knowledge.joins:
            resolved = resolve_join(join, self.schema_snapshot, dialect=self.dialect)
            if resolved is None:
                continue
            left, left_columns, right, right_columns = resolved
            chunks.append(RelationshipChunk(
                id=(f"schema.relationship:{left.table.full_name}->{right.table.full_name}:glossary:"
                    f"{','.join(left_columns)}:{self.schema_version}"),
                datasource_id=self.ds_id, from_table=left.table, to_table=right.table,
                from_columns=left_columns, to_columns=right_columns, cardinality=join.cardinality,
                origin="glossary", schema_version=self.schema_version,
            ))
        return chunks

    def _build_term_chunks(self) -> List[TermChunk]:
        """One ``knowledge.term`` entry per glossary term; its id hashes the term's name."""
        return [
            TermChunk(
                id=(f"knowledge.term:{self.ds_id}:"
                    f"{hashlib.sha256(term.term.strip().lower().encode('utf-8')).hexdigest()[:12]}:"
                    f"{self.schema_version}"),
                datasource_id=self.ds_id, term=term.term, synonyms=list(term.synonyms),
                definition=term.definition, schema_version=self.schema_version,
            )
            for term in self.knowledge.terms
        ]

    def _build_metric_chunks(self) -> List[MetricChunk]:
        """
        Builds metric-level chunks.

        Returns:
            Empty list, as metrics are not part of SchemaSnapshot.
        """
        return []
