# Lessons Learned

## 1. Context Retrieval Bias (Signal Density)

**Issue:**
The Decomposer exhibits a "Table-First Bias". When retrieving context for multi-datasource queries, it prioritizes datasources that return matched **Tables** over those that only return matched **Examples**.

**Scenario:**

- Query: "Show production runs for 'Widget A'"
- `manufacturing_history`: Returns `production_runs` table + examples. (Strong Signal)
- `manufacturing_supply`: Returns NO tables + "Who supplies Widget A?" example. (Weak Signal)

**Result:**
The Decomposer ignores `manufacturing_supply` because the prompt explicitly instructs it to prioritize "Schema Context" (tables). It treats the example match as noise because it lacks a corresponding table definition in the context window.

**Fix:**
Update the `DECOMPOSER_PROMPT` to explicitly state that **Matched Examples** are a valid signal for datasource selection, even if no specific tables were retrieved (e.g., due to strict vector search thresholds).

---
