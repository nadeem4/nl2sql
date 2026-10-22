from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable, Dict, Any, List, Optional, TYPE_CHECKING, Set, Tuple

if TYPE_CHECKING:
    from nl2sql.pipeline.state import SubgraphExecutionState
    from nl2sql.pipeline.nodes.decomposer.schemas import SubQuery

from nl2sql.common.errors import ErrorCode, RunWarning
from nl2sql.common.logger import get_logger
from nl2sql.events import publish
from nl2sql.knowledge.models import KnowledgeUsed
from nl2sql.runtime import Runtime
from nl2sql.datasources.models import matches_table
from .schema import Table, Column
from nl2sql_sdk.knowledge import DatasourceKnowledge
from nl2sql_sdk.schema import SchemaSnapshot, TableRef


logger = get_logger("schema_retriever")


@dataclass
class SchemaKnowledge:
    """What the caller may see of a datasource's knowledge, as the schema block uses it (wave_b.md, section 4).

    ``shown`` is the knowledge filtered to the caller's scope; the maps are
    keyed by the snapshot's table key: the chosen table descriptions, each
    column's extra fields (``description``, ``role``, ``unit``, ``grain``,
    ``values``) and the glossary joins as relationships from that table.
    """

    shown: DatasourceKnowledge = field(default_factory=DatasourceKnowledge)
    digest: str = ""
    provenance: Dict[str, str] = field(default_factory=dict)
    tables: Dict[str, str] = field(default_factory=dict)
    columns: Dict[Tuple[str, str], Dict[str, Any]] = field(default_factory=dict)
    relationships: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)


def schema_knowledge(knowledge: Any, datasource_id: str, snapshot: SchemaSnapshot, scope: Any,
                     dialect: Optional[str]) -> SchemaKnowledge:
    """The run's knowledge for one datasource, filtered to ``scope`` and resolved against ``snapshot``.

    A description is the glossary's, else the database's comment (D38), and
    either is dropped when its text mentions a table the caller cannot read
    (D39). A role is the glossary's, else the one the snapshot proves (D61).
    A glossary join is a relationship only between two tables the caller may
    read, with comparable types (D46).
    """
    from nl2sql.knowledge.merge import Knowledge
    from nl2sql.knowledge.resolve import derived_roles, resolve_column, resolve_join, resolve_table
    from nl2sql.knowledge.scope import deny_names, mentions_denied, visible

    knowledge = knowledge if knowledge is not None else Knowledge()
    also_denied = deny_names(scope)
    shown = visible(knowledge.get(datasource_id), snapshot, scope.allows, also_denied)
    out = SchemaKnowledge(shown=shown, digest=knowledge.digest(datasource_id),
                          provenance=dict(knowledge.provenance.get(datasource_id) or {}))
    key_of = {contract.table.full_name: key for key, contract in snapshot.contract.tables.items()}

    def clean(text: Optional[str]) -> Optional[str]:
        return text if text and not mentions_denied(text, snapshot, scope.allows, also_denied) else None

    for key, metadata in snapshot.metadata.tables.items():
        if clean(metadata.description):
            out.tables[key] = metadata.description
        for name, column in metadata.columns.items():
            if clean(column.description):
                out.columns.setdefault((key, name), {})["description"] = column.description
    for name, role in derived_roles(snapshot, dialect=dialect).items():
        table, column, _ = resolve_column(name, snapshot)
        if column is not None:
            out.columns.setdefault((key_of[table.table.full_name], column.name), {})["role"] = role
    for name, item in shown.tables.items():
        table, _ = resolve_table(name, snapshot)
        if table is not None and item.description:
            out.tables[key_of[table.table.full_name]] = item.description
    for name, item in shown.columns.items():
        table, column, _ = resolve_column(name, snapshot)
        if column is None:
            continue
        fields = out.columns.setdefault((key_of[table.table.full_name], column.name), {})
        for attr in ("description", "role", "unit", "grain"):
            if getattr(item, attr):
                fields[attr] = getattr(item, attr)
        if item.values:
            fields["values"] = dict(item.values)
    for join in shown.joins:
        resolved = resolve_join(join, snapshot, dialect=dialect)
        if resolved is None:
            continue
        left, left_columns, right, right_columns = resolved
        out.relationships.setdefault(key_of[left.table.full_name], []).append({
            "from_table": left.table.full_name, "to_table": right.table.full_name,
            "from_columns": left_columns, "to_columns": right_columns,
            "cardinality": join.cardinality, "business_meaning": None, "source": "glossary"})
    return out


class SchemaRetrieverNode:
    """Retrieves relevant schema chunks for planning context."""

    # A preferred table the table search scored but did not pick is added when
    # its similarity is at most this far below the least similar pick.
    PREFERRED_MARGIN = 0.05

    def __init__(self, ctx: Runtime):
        self.node_name = self.__class__.__name__.lower().replace("node", "")
        self.vector_store = ctx.vector_store
        self.state = ctx.state
        self.policy_engine = ctx.policy_engine
        self.ds_registry = getattr(ctx, "ds_registry", None)
        self.full_snapshot_max_tables = ctx.settings.retrieval.full_snapshot_max_tables
        # Entries MMR picks: table_k tables (or columns, when no table
        # matches), then planning_k columns and relationships of those tables.
        self.table_k = ctx.settings.retrieval.table_k
        self.planning_k = ctx.settings.retrieval.planning_k
        # The run's pinned knowledge (Runtime.pin_index), filtered per caller in _knowledge.
        self.knowledge = getattr(ctx, "knowledge", None)
        self.knowledge_settings = ctx.settings.knowledge

    def _preferred(self, datasource_id: str) -> Callable[[TableRef], bool]:
        """Whether a table is one of the datasource's ``preferred_tables``."""
        names = list(self.ds_registry.get_preferred_tables(datasource_id)) if self.ds_registry else []
        return lambda ref: any(matches_table(name, ref) for name in names)

    def _near_preferred(
        self,
        searches: List[Dict[str, Any]],
        snapshot: Optional[SchemaSnapshot],
        preferred: Callable[[TableRef], bool],
    ) -> List[str]:
        """Preferred tables the table search scored but did not pick, close enough to add."""
        if not snapshot:
            return []
        pool = next((s.get("pool") or [] for s in searches if s.get("search") == "tables"), [])
        picked = [e["similarity"] for e in pool if e.get("picked") and e.get("similarity") is not None]
        if not picked:
            return []
        floor = min(picked) - self.PREFERRED_MARGIN
        near = []
        for entry in pool:
            contract = snapshot.contract.tables.get(entry.get("table"))
            if (
                not entry.get("picked")
                and entry.get("type") == "schema.table"
                and contract is not None
                and (entry.get("similarity") if entry.get("similarity") is not None else -1.0) >= floor
                and preferred(contract.table)
            ):
                near.append(entry["table"])
        return near

    def visible_tables(self, user_context: Any, datasource_id: str) -> List[Table]:
        """Every table of the datasource's live build the caller may read, as the planner is shown them.

        For work that plans without a question (V3's seeding): the same tables,
        columns, keys, descriptions and sample values a pinned plan's sub-query
        gets, and nothing the caller's scope hides. Empty before the first build.
        """
        snapshot = self._resolve_snapshot(datasource_id, None)
        visible = self._visible(user_context, datasource_id, snapshot)
        knowledge = self._knowledge(user_context, datasource_id, snapshot)
        return self._build_tables_from_snapshot(snapshot, visible=visible, preferred=self._preferred(datasource_id),
                                                knowledge=knowledge)

    def _visible(self, user_context: Any, datasource_id: str,
                 snapshot: Optional[SchemaSnapshot]) -> Dict[str, Any]:
        """The snapshot's tables the caller's scope allows, by key.

        A denied table is hidden from the planner entirely: its name, columns,
        keys, relationships and data never reach the prompt, the LLM provider
        or the run trace. The vector searches run over these tables only, so
        the top ``k`` is filled with readable ones. A plan that names a hidden
        table anyway is refused by the logical validator, which asks the same
        ``Scope``.
        """
        if not snapshot:
            return {}
        scope = self.policy_engine.scope(user_context, datasource_id)
        return {key: contract for key, contract in snapshot.contract.tables.items()
                if scope.allows(contract.table.schema_name, contract.table.table_name)}

    def _knowledge(self, user_context: Any, datasource_id: str,
                   snapshot: Optional[SchemaSnapshot]) -> Optional[SchemaKnowledge]:
        """The run's pinned knowledge for the datasource, as the caller may see it; None without a snapshot."""
        if not snapshot:
            return None
        scope = self.policy_engine.scope(user_context, datasource_id)
        try:
            dialect = self.ds_registry.get_dialect(datasource_id) if self.ds_registry else None
        except Exception:
            dialect = None
        return schema_knowledge(self.knowledge, datasource_id, snapshot, scope, dialect)

    def _knowledge_used(self, state: SubgraphExecutionState, knowledge: Optional[SchemaKnowledge],
                        snapshot: Optional[SchemaSnapshot], tables: List[Table]) -> Optional[KnowledgeUsed]:
        """The terms and rules the planner and refiner send as ``[BUSINESS_CONTEXT]``, within the caller's scope.

        Terms match the sub-query's intent or the question; rules concern the
        tables sent. The block is cut to ``settings.knowledge.block_max_chars``
        and what was cut is recorded. None when nothing matched, and for a
        pinned plan, which no prompt plans.
        """
        from nl2sql.knowledge.match import match_terms, rules_for
        from nl2sql.knowledge.render import fit_business_context

        if knowledge is None or snapshot is None or getattr(state, "pinned_plan", None) is not None:
            return None
        shown, sources = knowledge.shown, knowledge.provenance
        sub_query = state.sub_query
        terms = match_terms(shown.terms, [sub_query.intent, state.user_query],
                            sources={t.term: sources.get(f"terms.{t.term}", "knowledge") for t in shown.terms},
                            top_k=self.knowledge_settings.top_k)
        rules = rules_for(shown.rules, {t.name for t in tables}, snapshot,
                          sources={r.id: sources.get(f"rules.{r.id}", "knowledge") for r in shown.rules})
        if not terms and not rules:
            return None
        terms, rules, dropped = fit_business_context(terms, rules, max_chars=self.knowledge_settings.block_max_chars)
        used = KnowledgeUsed(datasource_id=sub_query.datasource_id, digest=knowledge.digest, terms=terms,
                             rules=rules, dropped=dropped)
        publish("knowledge.used", {"sub_query_id": sub_query.id, "datasource_id": used.datasource_id,
                                   "terms": {t.term: t.score for t in used.terms},
                                   "rules": [r.id for r in used.rules], "dropped": used.dropped,
                                   "digest": used.digest})
        return used

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
        """The snapshot of ``schema_version``, else of the datasource's live build (the run's pinned pointer)."""
        if schema_version:
            return self.state.get_snapshot(datasource_id, schema_version) if self.state else None
        return self.vector_store.snapshot(datasource_id) if self.vector_store else None

    def _build_tables_from_snapshot(
        self,
        snapshot: SchemaSnapshot,
        resolved_tables: Optional[Dict[str, Set[str]]] = None,
        schema_version: Optional[str] = None,
        *,
        visible: Dict[str, Any],
        preferred: Callable[[TableRef], bool] = lambda _ref: False,
        knowledge: Optional[SchemaKnowledge] = None,
    ) -> List[Table]:
        """The planner's tables: ``resolved_tables`` (all of ``visible`` when empty), never a hidden one.

        Descriptions, roles, units, grains and code values come from
        ``knowledge``, and its glossary joins are listed beside the foreign keys.
        """
        if not snapshot:
            return []
        knowledge = knowledge or SchemaKnowledge()

        tables_out: List[Table] = []
        table_keys = list(visible) if not resolved_tables else list(resolved_tables.keys())
        visible_names = {contract.table.full_name for contract in visible.values()}

        for table_key in table_keys:
            table_contract = visible.get(table_key)
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
                facts = dict(knowledge.columns.get((table_key, col_contract.name)) or {})

                columns.append(
                    Column(
                        name=col_key,
                        type=col_contract.data_type,
                        stats=(
                            col_metadata.statistics.model_dump()
                            if col_metadata and col_metadata.statistics
                            else {}
                        ),
                        description=facts.pop("description", ""),
                        **facts,
                    )
                )

            relationships = []
            for fk in table_contract.foreign_keys:
                # A key into a hidden table would name it.
                if fk.referred_table.full_name not in visible_names:
                    continue
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
            relationships.extend(rel for rel in knowledge.relationships.get(table_key, [])
                                 if rel["to_table"] in visible_names)

            primary_keys = [
                col.name for col in table_contract.columns.values() if col.is_primary_key
            ]

            table = Table(
                    name=table_contract.table.table_name,
                    columns=columns,
                    description=knowledge.tables.get(table_key, ""),
                    primary_key=primary_keys,
                    schema_version=schema_version,
                    relationships=relationships,
                    preferred=True if preferred(table_contract.table) else None,
                )

            tables_out.append(table)

        # Preferred tables first; the sort is stable, so the rest keep their order.
        tables_out.sort(key=lambda t: not t.preferred)
        return tables_out

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
            visible = self._visible(state.user_context, datasource_id, snapshot)
            preferred = self._preferred(datasource_id)
            knowledge = self._knowledge(state.user_context, datasource_id, snapshot)
            update = self._retrieve(state, query, snapshot, visible, preferred, knowledge)
            used = self._knowledge_used(state, knowledge, snapshot, update["relevant_tables"])
            if used is not None:
                update["knowledge_used"] = used
            return update
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

    def _retrieve(self, state: SubgraphExecutionState, query: str, snapshot: Optional[SchemaSnapshot],
                  visible: Dict[str, Any], preferred: Callable[[TableRef], bool],
                  knowledge: Optional[SchemaKnowledge]) -> Dict[str, Any]:
        """The relevant tables: every visible one when they fit, else the vector searches' picks."""
        datasource_id = state.sub_query.datasource_id
        schema_version = state.sub_query.schema_version
        limit = self.full_snapshot_max_tables
        # A pinned plan is not planned here: the validator checks it against every table the caller may
        # read, so it gets them all and no search runs.
        pinned = getattr(state, "pinned_plan", None) is not None
        if snapshot and (len(visible) <= limit or pinned):
            tables_out = self._build_tables_from_snapshot(
                snapshot, resolved_tables=None, schema_version=schema_version,
                visible=visible, preferred=preferred, knowledge=knowledge,
            )
            return {
                "relevant_tables": tables_out,
                "retrieval": self._retrieval(
                    query, [], tables_out,
                    reason=(
                        f"The caller may read {len(visible)} tables (limit {limit}), "
                        "so all of them are sent without retrieval."
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

        # Only the tables the caller may read are searched, so the top k is
        # filled with readable ones.
        searchable = [contract.table.full_name for contract in visible.values()]
        if self.vector_store and searchable:
            schema_docs = self.vector_store.retrieve_schema_context(
                query, datasource_id, k=self.table_k, explain=searches, tables=searchable
            )
            if schema_docs:
                for doc in schema_docs:
                    table = doc.metadata.get("table")
                    if table:
                        tables[table].update([])
                for table in self._near_preferred(searches, snapshot, preferred):
                    tables[table].update([])
            else:
                column_docs = self.vector_store.retrieve_column_candidates(
                    query, datasource_id, k=self.table_k, explain=searches, tables=searchable
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
                query, datasource_id, list(tables.keys()), k=self.planning_k, explain=searches
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
                    tables[from_table].update(doc.metadata.get("from_columns") or [])
                if to_table and to_table in searchable:
                    tables[to_table].update(doc.metadata.get("to_columns") or [])

        if not tables:
            relevant_tables = self._build_tables_from_snapshot(
                snapshot,
                resolved_tables=None,
                schema_version=schema_version,
                visible=visible,
                preferred=preferred,
                knowledge=knowledge,
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
                    RunWarning(
                        code=ErrorCode.SCHEMA_RETRIEVAL_FAILED.value,
                        node=self.node_name,
                        message="Vector retrieval produced no candidates. Using full schema snapshot.",
                    )
                ],
            }

        relevant_tables = self._build_tables_from_snapshot(
            snapshot,
            resolved_tables=tables,
            schema_version=schema_version,
            visible=visible,
            preferred=preferred,
            knowledge=knowledge,
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
