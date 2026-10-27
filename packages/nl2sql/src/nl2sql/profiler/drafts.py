"""The profiler's drafted descriptions: the evidence a model is shown, the cleaning of its answer, and the run.

``profile(draft=True)`` (docs/architecture/wave_b.md, section 6) calls :func:`draft_descriptions`. The evidence
and cleaning functions were ``indexing/enrichment_service.py``'s, moved here unchanged by K3, which stopped the
index from writing a model's text into the schema snapshot (B31): a draft is a proposal in the review queue
and reaches nothing until a person accepts it (D38).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from nl2sql.limits import Budget, LimitReached, describe
from nl2sql.schema import SchemaSnapshot

# Name of the agent in the llm section of nl2sql.yaml that drafts descriptions (docs/architecture/wave_b.md, D44).
ENRICHMENT_LLM_NAME = "profiler"


class DatasourceEnrichment(BaseModel):
    description: Optional[str] = None
    domains: List[str] = Field(default_factory=list)
    sample_questions: List[str] = Field(default_factory=list)
    citations: List[str] = Field(default_factory=list)


class TableEnrichment(BaseModel):
    description: Optional[str] = None
    citations: List[str] = Field(default_factory=list)


class ColumnEnrichment(BaseModel):
    description: Optional[str] = None
    synonyms: List[str] = Field(default_factory=list)
    citations: List[str] = Field(default_factory=list)


class SchemaEnrichment(BaseModel):
    datasource: DatasourceEnrichment = Field(default_factory=DatasourceEnrichment)  # the profiler never asks
    tables: Dict[str, TableEnrichment] = Field(default_factory=dict)
    columns: Dict[str, ColumnEnrichment] = Field(default_factory=dict)


def build_evidence(
    snapshot: SchemaSnapshot,
    datasource_description: Optional[str],
    existing_questions: List[str],
) -> Dict[str, object]:
    contract = snapshot.contract
    metadata = snapshot.metadata

    tables_payload = []
    relationships_payload = []

    for table_key, table_contract in contract.tables.items():
        table_md = metadata.tables.get(table_key)
        columns_payload = []
        for col_name, col_contract in table_contract.columns.items():
            col_md = table_md.columns.get(col_name) if table_md else None
            stats = col_md.statistics.model_dump() if col_md and col_md.statistics else {}
            columns_payload.append(
                {
                    "name": col_name,
                    "type": col_contract.data_type,
                    "description": col_md.description if col_md else None,
                    "statistics": stats,
                    "sample_values": stats.get("sample_values", []),
                    "pii": bool(col_md.pii) if col_md else False,
                }
            )

        tables_payload.append(
            {
                "table_key": table_key,
                "description": table_md.description if table_md else None,
                "row_count": table_md.row_count if table_md else None,
                "columns": columns_payload,
            }
        )

        for fk in table_contract.foreign_keys:
            relationships_payload.append(
                {
                    "from_table": table_key,
                    "to_table": fk.referred_table.full_name,
                    "from_columns": fk.constrained_columns,
                    "to_columns": fk.referred_columns,
                }
            )

    return {
        "datasource_id": contract.datasource_id,
        "engine_type": contract.engine_type,
        "datasource_description": datasource_description or metadata.description or "",
        "domains": metadata.domains or [],
        "existing_questions": existing_questions,
        "tables": tables_payload,
        "relationships": relationships_payload,
    }


def _normalize_text(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def _dedupe_list(items: List[str]) -> List[str]:
    seen = set()
    result = []
    for item in items:
        cleaned = item.strip()
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(cleaned)
    return result


def _question_mentions_schema(question: str, table_names: List[str], column_names: List[str]) -> bool:
    text = question.lower()
    return any(name in text for name in table_names + column_names)


def sanitize_enrichment(
    snapshot: SchemaSnapshot,
    enrichment: SchemaEnrichment,
    max_questions: int = 100,
) -> SchemaEnrichment:
    contract = snapshot.contract
    valid_tables = set(contract.tables.keys())
    valid_columns = set()
    table_names = []
    column_names = []

    for table_key, table_contract in contract.tables.items():
        table_names.append(table_contract.table.table_name.lower())
        table_names.append(table_key.lower())
        for col_name in table_contract.columns.keys():
            valid_columns.add(f"{table_key}.{col_name}")
            column_names.append(col_name.lower())

    sanitized_tables: Dict[str, TableEnrichment] = {}
    for table_key, table_enrichment in enrichment.tables.items():
        if table_key not in valid_tables:
            continue
        sanitized_tables[table_key] = TableEnrichment(
            description=_normalize_text(table_enrichment.description),
            citations=_dedupe_list(table_enrichment.citations),
        )

    sanitized_columns: Dict[str, ColumnEnrichment] = {}
    for column_key, column_enrichment in enrichment.columns.items():
        if column_key not in valid_columns:
            continue
        sanitized_columns[column_key] = ColumnEnrichment(
            description=_normalize_text(column_enrichment.description),
            synonyms=_dedupe_list(column_enrichment.synonyms),
            citations=_dedupe_list(column_enrichment.citations),
        )

    questions = [
        q.strip()
        for q in enrichment.datasource.sample_questions
        if q and q.strip()
    ]
    questions = [
        q for q in questions if _question_mentions_schema(q, table_names, column_names)
    ]
    questions = questions[:max_questions]

    return SchemaEnrichment(
        datasource=DatasourceEnrichment(
            description=_normalize_text(enrichment.datasource.description),
            domains=_dedupe_list(enrichment.datasource.domains),
            sample_questions=questions,
            citations=_dedupe_list(enrichment.datasource.citations),
        ),
        tables=sanitized_tables,
        columns=sanitized_columns,
    )


@dataclass
class Drafted:
    """What the model drafted and the cleaning kept: ``tables`` by table key, ``columns`` by ``(table key, column)``."""

    tables: Dict[str, TableEnrichment] = field(default_factory=dict)
    columns: Dict[Tuple[str, str], ColumnEnrichment] = field(default_factory=dict)

    @property
    def count(self) -> int:
        return len(self.tables) + len(self.columns)


def draft_descriptions(
    llm: Any,
    budget: Budget,
    snapshot: SchemaSnapshot,
    *,
    allows: Callable[[Optional[str], str], bool],
    has_table_text: Callable[[str], bool],
    has_column_text: Callable[[str, str], bool],
    max_chars: int,
    issues: List[str],
) -> Drafted:
    """Ask ``llm`` for the descriptions the glossary and the database leave blank, one call per table.

    A table is shown to the model only when ``allows`` reaches it, and asked about only the items for which
    ``has_table_text`` / ``has_column_text`` say nobody has written a description. Every call runs under
    ``budget``; a refused call ends the run with what was drafted so far (``budget.stop`` says why). An answer
    that cannot be parsed, or a call that fails, is an entry in ``issues``; the next table is still tried. A draft
    longer than ``max_chars`` is dropped, since accepting it would fail the glossary's own check.
    """
    from nl2sql.llm.wires import StructuredOutputError, structured
    from nl2sql.profiler.prompts import DRAFT_PROMPT

    chain = DRAFT_PROMPT | structured(llm, SchemaEnrichment)
    drafted = Drafted()
    too_long = 0
    for key, table in snapshot.contract.tables.items():
        if budget.stop is not None:
            break
        schema_name, name = table.table.schema_name, table.table.table_name
        if not allows(schema_name, name):
            continue
        asks_table = not has_table_text(key)
        asks_columns = [c for c in table.columns if not has_column_text(key, c)]
        if not asks_table and not asks_columns:
            continue
        # A foreign key to a table the caller cannot read would put that table's name in the prompt.
        shown = table.model_copy(update={"foreign_keys": [
            fk for fk in table.foreign_keys
            if allows(fk.referred_table.schema_name, fk.referred_table.table_name)]})
        one = SchemaSnapshot(
            contract=snapshot.contract.model_copy(update={"tables": {key: shown}}),
            metadata=snapshot.metadata.model_copy(
                update={"tables": {k: v for k, v in snapshot.metadata.tables.items() if k == key}}))
        evidence = build_evidence(one, None, [])
        evidence["describe"] = {"tables": [key] if asks_table else [],
                                "columns": [f"{key}.{c}" for c in asks_columns]}
        try:
            answer = chain.invoke({"evidence_json": json.dumps(evidence, default=str)},
                                  config={"callbacks": budget.callbacks()})
        except LimitReached:
            break
        except StructuredOutputError as error:
            issues.append(f"The model's answer for {key} could not be used: {error}")
            continue
        except Exception as error:  # a provider's message can quote the prompt: the class alone
            issues.append(f"Drafting {key} failed ({type(error).__name__}).")
            continue
        clean = sanitize_enrichment(one, answer)
        if asks_table and (entry := clean.tables.get(key)) is not None and entry.description:
            if len(entry.description) > max_chars:
                too_long += 1
            else:
                drafted.tables[key] = entry
        for column in asks_columns:
            entry = clean.columns.get(f"{key}.{column}")
            if entry is None or not entry.description:
                continue
            if len(entry.description) > max_chars or any(len(s) > max_chars for s in entry.synonyms):
                too_long += 1
            else:
                drafted.columns[(key, column)] = entry
    if too_long:
        issues.append(f"{too_long} drafted description{'s were' if too_long != 1 else ' was'} dropped for being "
                      f"longer than {max_chars} characters (settings.knowledge.item_max_chars).")
    if budget.stop is not None:
        issues.append(f"Drafting stopped at the {describe(budget.stop, budget.setting)}; "
                      f"what it drafted so far is in the review queue.")
    return drafted
