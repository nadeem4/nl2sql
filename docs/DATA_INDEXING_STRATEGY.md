# NL2SQL DATA INDEXING STRATEGY - DESIGN DOCUMENT

## Objective

Move beyond schema-only understanding by indexing structured knowledge about data to improve grounding, routing accuracy, hallucination prevention, and SQL planning — without exploding cost, latency, or security risk.

---

## CURRENT STATE vs. PROBLEM

## 1. Intent-Driven Architecture

The pipeline now employs a dedicated **Semantic Analysis Node** at the entrance.

1. **Refinement**:
    * **Canonicalization**: Rewrites the query to be explicit and entity-centric.
    * **Enrichment**: Extracts keywords and generates synonyms for improved retrieval.

2. **Context Retrieval (Routing)**:
    * The `DecomposerNode` utilizes the **Expanded Query** (Canonical + Synonyms) from the Semantic Node.
    * It queries the `OrchestratorVectorStore` to identify relevant tables across datasources.

**Why Change? (The "Data Blindness" Problem)**

1. **Hallucinated Values**: User asks for "Gold Customers". Model queries `WHERE tier='Gold'`. Real data is `tier='premium_level_1'`. Query returns 0 rows.
2. **Inefficient Routing**: Model searches `sales_archive` for "recent orders" because it doesn't know the table range ended in 2019.
3. **Invalid Assumptions**: Model assumes a column is populated when it's actually 100% NULL in production.

---

## Core Principle

**Do NOT index full raw data blindly.**
Index *representative knowledge* that helps the model reason about what data exists and how it behaves.

---

## LAYERED INDEXING MODEL

### Layer 1 — Statistical / Summary Index (MUST HAVE)

**Purpose**: Describe what data “looks like” without storing data itself.

For every table maintain:

* row_count
* distinct counts per column
* null percentage per column
* min / max / range values
* top N frequent values
* time coverage window
* data freshness indicator
* approximate cardinality

**Benefits**:

* Improves datasource routing
* Prevents hallucinated queries
* Validates feasibility ("does this exist?")
* Enhances planner cost awareness

---

### Layer 2 — Sample Data Index (SELECTIVE & CONTROLLED)

**Purpose**: Provide semantic grounding but only via controlled representative samples.

**Recommended**:

* 100–500 rows per table max
* stratified sampling, not random
* include frequent + rare + recent slices
* anonymize / mask sensitive values

**Indexing**:

* Vector embedding for semantic reasoning
* BM25 for literal matching
* MMR / hybrid retrieval to balance relevance and diversity

---

### Layer 3 — Business Entity Index

**Purpose**: Align business language with database reality.

**Store per Entity**:

* canonical name
* synonyms
* entity identifiers
* owning datasource
* backup datasource if exists
* relationship hints
* confidence score

**Examples**:
Customer / Product / Vendor / Region / Plant / Project

**Benefits**:

* Solves naming ambiguity
* Improves intent grounding
* Enables reliable routing

---

### Layer 4 — Knowledge Graph (OPTIONAL FUTURE)

**Purpose**: Power intelligent join reasoning and lineage awareness.

**Graph Captures**:

* Table relationships
* Column semantic links
* Fact-dimension relationships
* Temporal alignment constraints

---

## RETRIEVAL STRATEGY

When a query arrives:

1. Retrieve schema context
2. Retrieve entity index context
3. Retrieve statistical profiles
4. Retrieve sample data ONLY if needed
5. Feed to planner + validator

*Always treat retrieved data as hints, NOT truth — final SQL is validated via engine.*

---

## FRESHNESS MODEL

* **statistical index** → daily or per ETL completion
* **sample index** → rolling refresh
* **entity index** → schema-change triggered

Prefer incremental refresh over full rebuilds.

---

## SECURITY & PRIVACY

**ABSOLUTELY DO NOT INDEX**:

* PII without masking
* financial identifiers
* unbounded raw historical data

Mask, hash, anonymize wherever needed.
Separate tenant indexes.
Encrypt embeddings where required.

---

## EVAL STRATEGY

Measure improvements:

* routing accuracy
* hallucination reduction
* SQL validity
* retries reduced
* latency + cost impact

Use deterministic SQL validator loop.

---

## FINAL ROLL OUT STRATEGY

* **Phase 1** — Implement statistics + entity index
* **Phase 2** — Introduce controlled sample indexing
* **Phase 3** — Evolve to knowledge graph + intelligent planner

---

## SUMMARY

This strategy gives:

* grounding
* correctness
* scalability
* security
* predictable cost

Without drowning in unnecessary data.
