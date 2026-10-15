"""``retrieval_recall``: the share of a case's ``tables`` and ``columns`` the schema retriever sent the planner.

The value is the column recall (the table recall when the case names no
columns); it passes at 1.0. ``detail`` has both, what was missed, and how
much was sent, since sending everything would score 1.0.

In mode ``full`` what was sent is read from the run's schema retriever. In
mode ``retrieval`` no model runs: :func:`sent` runs the retriever the way the
pipeline does, with the vector search forced on (Chinook's 11 tables are
under the full-snapshot limit that would skip it), and the retriever's query
is the question alone, since the decomposer that would add to it is a model.
:func:`routed` ranks the datasources for the ``routing`` scorer, without the
resolver's model check.
"""
from __future__ import annotations

import uuid
from typing import Any, ClassVar, Dict, FrozenSet, List, Optional

from nl2sql.evaluation.scorers import NOT_APPLICABLE, BuiltinScorer, CaseRun, Score
from nl2sql.evaluation.suite import EvalCase
from nl2sql.policies import UserContext

# Mode retrieval's runtime: the vector search runs on a schema of any size.
RETRIEVAL_CHANGES: Dict[str, Any] = {"settings.retrieval.full_snapshot_max_tables": 0}


def recall(case: EvalCase, sent_tables: Dict[str, List[str]]) -> Dict[str, Any]:
    """Recall of ``case``'s tables and columns, given the tables and columns sent (table -> columns)."""
    tables = {t.casefold() for t in sent_tables}
    columns = {f"{t}.{c}".casefold() for t, cols in sent_tables.items() for c in cols}
    missed_tables = [t for t in case.tables if t.casefold() not in tables]
    missed_columns = [c for c in case.columns if c.casefold() not in columns]

    def share(needed, missed):
        return round(1 - len(missed) / len(needed), 4) if needed else None

    return {"table_recall": share(case.tables, missed_tables), "column_recall": share(case.columns, missed_columns),
            "missed_tables": missed_tables, "missed_columns": missed_columns,
            "tables_sent": len(sent_tables), "columns_sent": sum(len(c) for c in sent_tables.values())}


class RetrievalRecall(BuiltinScorer):
    modes: ClassVar[FrozenSet[str]] = frozenset({"full", "retrieval"})

    def score(self, run: CaseRun) -> Score:
        if run.retrieval is None or not run.case.tables or run.expected != "allowed":
            return NOT_APPLICABLE
        detail = recall(run.case, run.retrieval)
        value = detail["column_recall"] if detail["column_recall"] is not None else detail["table_recall"]
        passed = value == 1.0
        if not passed:
            detail["reason"] = "the retriever missed " + ", ".join(detail["missed_tables"] + detail["missed_columns"])
        return Score(value=value, passed=passed, detail=detail)


def sent(ctx: Any, case: EvalCase, role: str) -> Dict[str, List[str]]:
    """The tables and columns the schema retriever sends the planner for ``case``, asked as ``role``."""
    from nl2sql.pipeline.nodes.decomposer.schemas import SubQuery
    from nl2sql.pipeline.nodes.schema_retriever.node import SchemaRetrieverNode
    from nl2sql.pipeline.state import SubgraphExecutionState

    if not case.datasources:
        return {}
    state = SubgraphExecutionState(
        trace_id=uuid.uuid4().hex, user_context=UserContext(roles=[role]),
        sub_query=SubQuery(id="sq1", datasource_id=case.datasources[0], intent=case.question))
    record = SchemaRetrieverNode(ctx)(state).get("retrieval") or {}
    return {t["table"]: list(t["columns"]) for t in record.get("tables", [])}


def routed(ctx: Any, question: str, limit: int) -> List[str]:
    """The datasources the resolver's vector search ranks for ``question``, best first, at most ``limit``."""
    ids = ctx.ds_registry.list_ids()
    if len(ids) == 1:
        return list(ids)
    ranked: List[str] = []
    for doc in ctx.vector_store.retrieve_datasource_candidates(question, k=5):
        ds_id: Optional[str] = doc.metadata.get("datasource_id")
        if ds_id and ds_id not in ranked:
            ranked.append(ds_id)
    return ranked[:limit]


def retrieval_settings(ctx: Any) -> Dict[str, Any]:
    """The retrieval settings a mode retrieval report was measured with."""
    from nl2sql.pipeline.nodes.schema_retriever.node import SchemaRetrieverNode

    return {"table_k": SchemaRetrieverNode.TABLE_K, "planning_k": SchemaRetrieverNode.PLANNING_K,
            "fetch_multiplier": ctx.vector_store.FETCH_MULTIPLIER, "lambda_mult": ctx.vector_store.LAMBDA_MULT,
            "full_snapshot_max_tables": 0, "query": "question"}
