"""Measure the real planner, refiner and decomposer prompts on Chinook, with no LLM call.

Renders each node's prompt for the 12 Chinook demo questions exactly as the node
builds it (the node runs; only its chain is swapped for one that records the
rendered messages and stops), then counts tokens with tiktoken ``o200k_base``.
Without tiktoken it reports characters instead and says so.

It reports, per node, the mean prompt size, the schema block the planner and
refiner carry, and the stable prefix: the leading text identical across all 12
questions, which is what a provider's prompt cache can reuse.

Usage (from the repo root, with the packages importable):

    python scripts/measure_prompt_tokens.py
"""
from __future__ import annotations

import os
import statistics
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Callable, Dict, List, Optional, Tuple

import yaml

REPO = Path(__file__).resolve().parents[1]
CHINOOK_DB = REPO / "packages" / "nl2sql" / "src" / "nl2sql" / "cli" / "demo" / "data" / "chinook.sqlite"
QUESTIONS_FILE = REPO / "configs" / "sample_questions.demo.yaml"
SCHEMA_VERSION = "20260920T000000_0123abcd"
# What the refiner sees on a typical retry.
RETRY_ERROR = "Column 'Revenue' not found in table 'Invoice'."


def questions() -> List[str]:
    return list(yaml.safe_load(QUESTIONS_FILE.read_text(encoding="utf-8"))["chinook"])


def chinook_tables(readable: Callable = lambda _ref: True):
    """The schema_retriever's tables for Chinook: the full snapshot, stats included."""
    from nl2sql.adapters.sqlite.adapter import SqliteAdapter
    from nl2sql.pipeline.nodes.schema_retriever.node import SchemaRetrieverNode

    adapter = SqliteAdapter(
        datasource_id="chinook",
        datasource_engine_type="sqlite",
        connection_args={"type": "sqlite", "database": str(CHINOOK_DB)},
    )
    snapshot = adapter.fetch_schema_snapshot()
    retriever = SchemaRetrieverNode.__new__(SchemaRetrieverNode)
    return retriever._build_tables_from_snapshot(
        snapshot, resolved_tables=None, schema_version=SCHEMA_VERSION, readable=readable
    )


class _Stop(Exception):
    pass


class _Capture:
    """Stands in for a node's chain: records the rendered prompt, then stops the call."""

    def __init__(self, prompt):
        self.prompt = prompt
        self.values: Dict = {}
        self.messages: List = []

    def invoke(self, values):
        self.values = values
        self.messages = self.prompt.format_messages(**values)
        raise _Stop()


def _node(cls):
    ctx = SimpleNamespace(llm_registry=SimpleNamespace(get_llm=lambda _name: None))
    node = cls.__new__(cls)
    try:
        cls.__init__(node, ctx)
    except AttributeError:  # nodes that call llm.with_structured_output on None
        pass
    return node


def _render(node, state) -> Tuple[List, Dict]:
    capture = _Capture(node.prompt)
    node.chain = capture
    node(state)
    return capture.messages, capture.values


def as_text(messages) -> str:
    """The prompt as one string, role markers included, in the order it is sent."""
    return "\n".join(f"<{m.type}>\n{m.content}" for m in messages)


def _sub_query(question: str):
    """The sub-query the decomposer would hand on, with a per-question expected_schema."""
    from nl2sql.pipeline.nodes.decomposer.schemas import ExpectedColumn, SubQuery

    return SubQuery(
        id="sq1", datasource_id="chinook", intent=question,
        expected_schema=[ExpectedColumn(name="_".join(question.lower().split()[:3]), dtype="string")],
    )


def render_planner(question: str, tables) -> Tuple[List, Dict]:
    from nl2sql.pipeline.nodes.ast_planner.node import ASTPlannerNode
    from nl2sql.pipeline.state import SubgraphExecutionState

    state = SubgraphExecutionState(trace_id="measure", sub_query=_sub_query(question), relevant_tables=tables)
    return _render(_node(ASTPlannerNode), state)


def render_refiner(question: str, tables) -> Tuple[List, Dict]:
    from nl2sql.common.errors import ErrorCode, ErrorSeverity, PipelineError
    from nl2sql.pipeline.nodes.refiner.node import RefinerNode
    from nl2sql.pipeline.state import SubgraphExecutionState

    state = SubgraphExecutionState(
        trace_id="measure",
        sub_query=_sub_query(question),
        relevant_tables=tables,
        errors=[PipelineError(node="logical_validator", message=RETRY_ERROR,
                              severity=ErrorSeverity.ERROR, error_code=ErrorCode.COLUMN_NOT_FOUND)],
    )
    return _render(_node(RefinerNode), state)


def render_decomposer(question: str) -> Tuple[List, Dict]:
    from nl2sql.pipeline.nodes.datasource_resolver.schemas import DatasourceResolverResponse, ResolvedDatasource
    from nl2sql.pipeline.nodes.decomposer.node import DecomposerNode
    from nl2sql.pipeline.state import GraphState

    resolved = ResolvedDatasource(
        datasource_id="chinook",
        schema_version=SCHEMA_VERSION,
        metadata={"datasource_id": "chinook", "id": f"schema.datasource:chinook:{SCHEMA_VERSION}",
                  "schema_version": SCHEMA_VERSION, "type": "schema.datasource"},
    )
    state = GraphState(
        user_query=question,
        datasource_resolver_response=DatasourceResolverResponse(
            resolved_datasources=[resolved], allowed_datasource_ids=["chinook"]),
    )
    return _render(_node(DecomposerNode), state)


def token_counter() -> Tuple[Callable[[str], int], str]:
    """``(count, unit)``: tiktoken o200k_base tokens if available, else characters."""
    try:
        import tiktoken

        enc = tiktoken.get_encoding("o200k_base")
        return (lambda text: len(enc.encode(text))), "tokens"
    except Exception:  # not installed, or the encoding cannot be fetched offline
        return len, "chars"


def common_prefix(texts: List[str]) -> str:
    return os.path.commonprefix(texts)


def main() -> int:
    import logging

    logging.disable(logging.CRITICAL)  # the stopped chain makes each node log a failure
    count, unit = token_counter()
    tables = chinook_tables()
    qs = questions()
    rows = []
    for name, render in (
        ("decomposer", lambda q: render_decomposer(q)),
        ("ast_planner", lambda q: render_planner(q, tables)),
        ("refiner", lambda q: render_refiner(q, tables)),
    ):
        texts, schema_sizes = [], []
        for q in qs:
            messages, values = render(q)
            texts.append(as_text(messages))
            if "relevant_tables" in values:
                schema_sizes.append(count(values["relevant_tables"]))
        prefix = common_prefix(texts)
        rows.append((
            name,
            round(statistics.mean(count(t) for t in texts)),
            round(statistics.mean(schema_sizes)) if schema_sizes else None,
            count(prefix),
        ))

    unit_note = "" if unit == "tokens" else " (tiktoken unavailable: CHARACTERS, not tokens)"
    print(f"Chinook, {len(qs)} demo questions, unit: {unit} (o200k_base){unit_note}")
    print(f"{'node':<12} {'prompt (mean)':>14} {'schema block':>13} {'stable prefix':>14}")
    for name, total, schema, prefix in rows:
        print(f"{name:<12} {total:>14} {schema if schema is not None else '-':>13} {prefix:>14}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
