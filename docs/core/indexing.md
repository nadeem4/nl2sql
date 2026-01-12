# Retrieval & Indexing Strategies

The platform uses a specialized **Orchestrator Vector Store** to manage context for the Decomposer Node. This ensures that even with hundreds of tables, the LLM only receives the most relevant schema information.

## Indexing Strategy

We index two main types of documents: **Schema** and **Examples**.

### 1. Schema Indexing

Table schemas are "flattened" into a rich text representation before embedding.

* **Format**: `Table: {name} (Alias: {alias}). Columns: {col_desc}. Primary Key: {pk}. Foreign Keys: {fk}.`
* **Offline Aliasing**: To prevent conflicts, tables are assigned canonical aliases (e.g., `sales_db_t1`) during indexing.
* **Rich Metadata (via Adapter)**:
  * **Sample Values**: Adapters populate `col.statistics.sample_values`. We embed the **Top-5** distinct values for categorical columns (e.g., `status: ['ACTIVE', 'PENDING']`).
  * **Range Data**: Adapters populate `min_value`/`max_value`. These are indexed for numeric/date columns (e.g., `created_at: [2023-01..2024-12]`).

### 2. Example Indexing (Few-Shot)

Routing examples are indexed to help the model distinguish between similar datasources foundationally.

* **Source**: Loaded from `configs/sample_questions.yaml`.
* **Enrichment (Semantic Variants)**:
    The `SemanticAnalysisNode` is used to generate variants for each example to maximize retrieval surface area.
    1. **Original**: "Show me my orders"
    2. **Canonical**: "select orders from sales_db" (Hypothetical SQL-like intent)
    3. **Meta-Text**: "purchases transactions history active items" (Keywords & Synonyms)

    *Each variant is stored as a separate vector document pointing to the same example.*

## Retrieval Strategy

We employ a **Partitioned Retrieval** strategy using **Maximal Marginal Relevance (MMR)**.

### The Problem

If we simple retrieved the top-10 chunks, a query like "Show me users" might return 10 "User" tables from different databases, crowding out helpful "Example" chunks that explain *which* User table is relevant.

### The Solution: Partitioned MMR

1. **Fetch Top-K Tables**: Independent MMR search for `type: table`.
2. **Fetch Top-K Examples**: Independent MMR search for `type: example`.
3. **Merge**: The results are concatenated.

This ensures the Decomposer always receives both the **Schema Candidates** AND the **Instructional Examples**.

### Filtering

Retrieval is strictly scoped by Security considerations:

* **AuthZ**: `filter={'datasource_id': {'$in': allowed_ids}}` is applied to every query. The user can *never* retrieve a schema they don't have access to.

::: nl2sql.services.vector_store.OrchestratorVectorStore.index_schema
::: nl2sql.services.vector_store.OrchestratorVectorStore.retrieve_routing_context
