# Lessons Learned

A collection of architectural insights/gotchas discovered during development.

## 1. Context Retrieval Bias (Signal Density)

**Issue**: The Decomposer exhibits a "Table-First Bias". It prioritizes datasources with matched **Tables** over those with matched **Examples**.

**Scenario**:

* Query: "Show production runs for 'Widget A'"
* `db_history`: Returns `production_runs` table. (Strong Signal)
* `db_supply`: Returns NO tables, but matches example "Who supplies Widget A?". (Weak Signal)

**Result**: The Decomposer ignores `db_supply` because the prompt prioritized schema.

**Fix**: The `DECOMPOSER_PROMPT` was updated to treat **Matched Examples** as a valid routing signal, even if no tables are returned.
