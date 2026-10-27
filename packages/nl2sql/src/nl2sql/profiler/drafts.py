"""The profiler's drafted descriptions: the evidence a model is shown, the prompt, and the cleaning of its answer.

P2 wires these into ``profile(draft=True)`` (docs/architecture/wave_b.md, section 6). They were
``indexing/enrichment_service.py``'s, moved here unchanged by K3, which stopped the index from
writing a model's text into the schema snapshot (B31): a draft is a proposal in the review queue
and reaches nothing until a person accepts it.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field

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
    datasource: DatasourceEnrichment
    tables: Dict[str, TableEnrichment] = Field(default_factory=dict)
    columns: Dict[str, ColumnEnrichment] = Field(default_factory=dict)


ENRICHMENT_PROMPT = """You are enriching database schema metadata for retrieval.
Use ONLY the evidence provided. Do not introduce any tables, columns, or facts
that are not present in evidence. If evidence is insufficient, return empty values.

Return a JSON object that matches this schema:
SchemaEnrichment = {{
  "datasource": {{
    "description": string|null,
    "domains": [string],
    "sample_questions": [string],
    "citations": [string]
  }},
  "tables": {{
    "<table_key>": {{
      "description": string|null,
      "citations": [string]
    }}
  }},
  "columns": {{
    "<table_key>.<column_name>": {{
      "description": string|null,
      "synonyms": [string],
      "citations": [string]
    }}
  }}
}}

Evidence (JSON):
{evidence_json}
"""


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
