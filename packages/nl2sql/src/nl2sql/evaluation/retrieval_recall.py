"""Schema retrieval recall against the gold set: key-free, no model, no cost.

Every answerable gold question lists the tables its SQL reads
(``needed_tables``) and the ``Table.Column`` names the answer needs
(``needed_columns``). For each one this runs the schema retriever the way the
pipeline does, with the vector search forced on, and measures how much of
that reached the tables and columns the planner is given:

* table recall: needed tables sent / needed tables;
* column recall: needed columns sent / needed columns;
* tables and columns sent, since sending everything would score 1.0.

Chinook has 11 tables, under the full-snapshot limit
(``SCHEMA_RETRIEVAL_FULL_SNAPSHOT_MAX_TABLES``, default 15) that skips
retrieval on a small schema, so the limit is set to 0 for the run. No LLM is
called: the datasource is the one registered (or, with several, the top hit
of the resolver's own vector search; the resolver's answerability check is an
LLM call and is skipped), and the retriever's query is the question alone,
since the decomposer that would add filters and expected columns is an LLM.
What the retriever sent is read from its retrieval record
(``nl2sql.indexing.retrieval_trace``), the same one a run trace keeps.
"""
from __future__ import annotations

import pathlib
import uuid
from typing import Any, Dict, List, Optional, Sequence

from nl2sql.auth import UserContext
from nl2sql.common.settings import settings
from nl2sql.context import NL2SQLContext
from nl2sql.evaluation.gold import GOLD_DATASET_PATH, GoldQuestion, load_gold_dataset


def score_question(question: GoldQuestion, sent: Dict[str, List[str]]) -> Dict[str, Any]:
    """Recall for one question, given the tables and columns sent to the planner."""
    sent_tables = {t.casefold() for t in sent}
    sent_columns = {f"{t}.{c}".casefold() for t, cols in sent.items() for c in cols}
    missed_tables = [t for t in question.needed_tables if t.casefold() not in sent_tables]
    missed_columns = [c for c in question.needed_columns if c.casefold() not in sent_columns]

    def recall(needed, missed):
        return round(1 - len(missed) / len(needed), 4) if needed else None

    return {
        "id": question.id, "question": question.question,
        "table_recall": recall(question.needed_tables, missed_tables),
        "column_recall": recall(question.needed_columns, missed_columns),
        "missed_tables": missed_tables, "missed_columns": missed_columns,
        "tables_sent": len(sent), "columns_sent": sum(len(c) for c in sent.values()),
        "sent": {t: sorted(c) for t, c in sorted(sent.items())},
    }


def _mean(values: Sequence[float]) -> Optional[float]:
    return round(sum(values) / len(values), 4) if values else None


def summarize(results: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """The means over questions, and how many had every needed table or column."""
    return {
        "questions": len(results),
        "table_recall": _mean([r["table_recall"] for r in results]),
        "column_recall": _mean([r["column_recall"] for r in results]),
        "perfect_tables": sum(r["table_recall"] == 1.0 for r in results),
        "perfect_columns": sum(r["column_recall"] == 1.0 for r in results),
        "tables_sent": _mean([r["tables_sent"] for r in results]),
        "columns_sent": _mean([r["columns_sent"] for r in results]),
    }


def worst(results: Sequence[Dict[str, Any]], n: int = 5) -> List[Dict[str, Any]]:
    """The ``n`` questions with the lowest column recall, then table recall."""
    return sorted(results, key=lambda r: (r["column_recall"], r["table_recall"], r["id"]))[:n]


def compare_reports(new: Dict[str, Any], old: Dict[str, Any]) -> Dict[str, Any]:
    """``new`` minus ``old`` for each mean, and every question whose recall changed, as [old, new]."""
    fields = ("table_recall", "column_recall", "tables_sent", "columns_sent")
    summary = {f: round(new["summary"][f] - old["summary"][f], 4)
               for f in fields if new["summary"].get(f) is not None and old["summary"].get(f) is not None}
    before = {r["id"]: r for r in old.get("results", [])}
    moved = []
    for r in new.get("results", []):
        b = before.get(r["id"])
        if b and (b["table_recall"], b["column_recall"]) != (r["table_recall"], r["column_recall"]):
            moved.append({"id": r["id"], "table_recall": [b["table_recall"], r["table_recall"]],
                          "column_recall": [b["column_recall"], r["column_recall"]]})
    return {"summary": summary, "moved": moved}


def _embedding_name(ctx: NL2SQLContext) -> str:
    embeddings = getattr(ctx.vector_store, "embeddings", None)
    from nl2sql.indexing.embeddings import LOCAL_EMBEDDING_MODEL, LocalEmbeddings

    if isinstance(embeddings, LocalEmbeddings):
        return LOCAL_EMBEDDING_MODEL
    return str(getattr(embeddings, "model", None) or type(embeddings).__name__)


def _datasource(ctx: NL2SQLContext, question: str) -> Optional[str]:
    """The datasource the resolver would pick, without its LLM answerability check."""
    ids = ctx.ds_registry.list_ids()
    if len(ids) == 1:
        return ids[0]
    docs = ctx.vector_store.retrieve_datasource_candidates(question, k=5)
    return docs[0].metadata.get("datasource_id") if docs else None


def _sent(ctx: NL2SQLContext, question: GoldQuestion, datasource_id: Optional[str]) -> Dict[str, List[str]]:
    """The tables and columns the schema retriever sends the planner for the question."""
    from nl2sql.pipeline.nodes.decomposer.schemas import SubQuery
    from nl2sql.pipeline.nodes.schema_retriever.node import SchemaRetrieverNode
    from nl2sql.pipeline.state import SubgraphExecutionState

    if not datasource_id:
        return {}
    state = SubgraphExecutionState(
        trace_id=uuid.uuid4().hex, user_context=UserContext(roles=["admin"]),
        sub_query=SubQuery(id="sq1", datasource_id=datasource_id, intent=question.question))
    record = SchemaRetrieverNode(ctx)(state).get("retrieval") or {}
    return {t["table"]: list(t["columns"]) for t in record.get("tables", [])}


def run_retrieval_recall(ctx: NL2SQLContext, dataset_path: pathlib.Path = GOLD_DATASET_PATH,
                         question_ids: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """Runs the schema retriever on every answerable gold question and returns the report."""
    dataset = [q for q in load_gold_dataset(dataset_path) if q.needed_tables]
    if question_ids:
        dataset = [q for q in dataset if q.id in set(question_ids)]
        if not dataset:
            raise ValueError(f"No answerable question matches: {', '.join(question_ids)}")

    saved = settings.schema_retrieval_full_snapshot_max_tables
    settings.schema_retrieval_full_snapshot_max_tables = 0  # force the vector search on a small schema
    try:
        results = []
        for q in dataset:
            datasource_id = _datasource(ctx, q.question)
            results.append({**score_question(q, _sent(ctx, q, datasource_id)), "datasource_id": datasource_id})
    finally:
        settings.schema_retrieval_full_snapshot_max_tables = saved

    from nl2sql.pipeline.nodes.schema_retriever.node import SchemaRetrieverNode

    return {
        "kind": "retrieval",
        "dataset": str(dataset_path),
        "settings": {"table_k": SchemaRetrieverNode.TABLE_K, "planning_k": SchemaRetrieverNode.PLANNING_K,
                     "fetch_multiplier": ctx.vector_store.FETCH_MULTIPLIER,
                     "lambda_mult": ctx.vector_store.LAMBDA_MULT, "full_snapshot_max_tables": 0,
                     "embedding": _embedding_name(ctx), "query": "question"},
        "summary": summarize(results),
        "results": results,
    }
