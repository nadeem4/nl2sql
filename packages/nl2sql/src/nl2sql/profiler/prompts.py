"""The ``profiler`` agent's prompt: it describes the tables and columns the evidence asks for, and nothing else.

Derived from the prompt of the enrichment ``nl2sql index --enrich`` ran (deleted by K3). The datasource's
description, domains and sample questions are the operator's (docs/architecture/wave_b.md, section 6), so
they are no longer asked for.
"""
from langchain_core.prompts import ChatPromptTemplate

DRAFT_PROMPT = ChatPromptTemplate.from_template("""You are drafting database schema descriptions for retrieval.
Use ONLY the evidence provided. Do not introduce any tables, columns, or facts
that are not present in evidence. If evidence is insufficient for an item,
leave its description null.

Describe only the items listed under "describe" in the evidence: the tables in
"describe.tables" and the columns in "describe.columns". Each description is
one short sentence saying what the item holds. Synonyms are other names a
person might use for a column. Leave "datasource" empty.

Return a JSON object that matches this schema:
SchemaEnrichment = {{
  "datasource": {{}},
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
""")
