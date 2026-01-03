# Data Indexing Strategy

**Objective**: Move beyond schema-only understanding by indexing structured knowledge about data. This improves routing accuracy, prevents hallucinations, and enhances SQL planning.

## The Problem: Data Blindness

Without indexing, the AI knows the *tables* but not the *data*.

1. **Hallucinations**: Asking for "Gold Tier" users when the data uses `tier='premium_level_1'`.
2. **Inefficient Routing**: Searching legacy tables for recent data.
3. **Invalid Assumptions**: Querying NULL columns.

## Layered Indexing Model

We use a layered approach to balance cost and accuracy.

### Layer 1: Statistical Index (Must Have)

Stores metadata *about* the data, not the data itself.

* Row counts
* Distinct values
* Null percentages
* Min/Max sets (e.g. date ranges)

**Benefit**: Helps the Planner know if a query will return 0 rows before writing SQL.

### Layer 2: Sample Data (Controlle)

Stores a small, stratified sample of actual rows (e.g. 100 rows).

* **Security**: PII is masked.
* **Usage**: Used for few-shot prompting in the Generator.

### Layer 3: Business Entity Index

Maps business terms to database realities.

* "Client" -> `customers` table.
* "Revenue" -> `total_amount` column.
* **Storage**: Vector embeddings for semantic search.

## Retrieval Strategy

When a query arrives, the **Decomposer** and **Planner** retrieve context in this order:

1. **Schema Context**: Table definitions.
2. **Entity Index**: Mapping synonyms.
3. **Statistical Profile**: Validating values.
4. **Sample Data**: Only if needed for complex join logic.

## Security & Privacy

> [!IMPORTANT]
> We **NEVER** index full raw data blindly.

* **PII**: Automatically masked or excluded.
* **Financials**: Sensitive identifiers are hashed.
* **Tenant Isolation**: Separate indexes for separate logical tenants.
