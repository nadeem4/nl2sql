# Evaluation Framework Documentation

This document explicitly details the testing and evaluation framework for the NL2SQL pipeline.

## 1. Architecture

The benchmarking suite is built into the CLI (`src.nl2sql.cli`) and orchestrated by `src.nl2sql.commands.benchmark`.

**Components:**

- **Benchmark Driver**: Iterates through test cases, handles parallel execution, and aggregates results.
- **Model Evaluator** (`evaluation/evaluator.py`): Pure functions for calculating metrics (e.g., `compare_results`, `evaluate_sql_semantic`).
- **Graph Pipeline**: The core LangGraph application is invoked largely without modification, ensuring we test the exact production code path.

## 2. Metrics Definitions

### Execution Accuracy ("The Gold Standard")

This measures whether the *data* returned by the generated SQL matches the *data* returned by the Ground Truth SQL.

- **Method**: Run `Generated SQL` on `Actual Datasource`. Run `Expected SQL` on `Expected Datasource`. Compare row sets.
- **Column Normalization**: Ignores case and whitespace in header names.
- **Value Matching**: List-of-Dictionary comparison. Order is ignored by default (`order_matters=False`).
- **Status**: `PASS` (Match), `DATA_MISMATCH` (Execution successful, data differs), `INVALID_GT` (Ground Truth SQL is unparseable), `INVALID_SQL` (Generated SQL unparseable).

### Semantic SQL Accuracy

Used when execution fails or data mismatches (e.g., missing records in DB).

- **Method**: Uses an LLM Judge (e.g., GPT-4o) to compare the *intent* of the Generated SQL vs Expected SQL.
- **Criteria**: "Are these queries semantically equivalent? Do they fetch the same conceptual data?"

### Routing Metrics

- **Routing Accuracy**: Percentage of times the Router selected the `expected_datasource`.
- **Layer Distribution**: Analysis of which complexity layer (L1: Vector, L2: Multi-Query, L3: Reasoning) processed the queries. Helps detect "Complexity Drift".

### Stability Metrics (Pass@K)

Measures reliability over `N` iterations (using `--iterations N`).

- **Success Rate**: `(Successful Runs / K) * 100`.
- **Routing Stability**: `(Count(Dominant Datasource) / K) * 100`. consistently picking the same datasource.

## 3. Dataset Format (`golden_dataset.yaml`)

The dataset is a list of test cases defined in YAML:

```yaml
- id: "ops_001"
  question: "List all machines"
  datasource: "manufacturing_ops"        # Expected Datasource ID
  difficulty: "easy"
  expected_routing_layer: "layer_1"      # Expected Complexity
  expected_sql: "SELECT name FROM machines" # Ground Truth SQL
```

## 4. Usage Guide

### Basic Run

Run the full dataset with default settings (parallel execution enabled).

```powershell
python -m src.nl2sql.cli --benchmark --dataset tests/golden_dataset.yaml
```

### Routing-Only Test

Skip SQL generation and execution to quickly verify the Router.

```powershell
python -m src.nl2sql.cli --benchmark --dataset tests/golden_dataset.yaml --routing-only
```

### Stability Analysis (Pass@K)

Run each question 5 times to detect flakiness.

```powershell
python -m src.nl2sql.cli --benchmark --dataset tests/golden_dataset.yaml --iterations 5
```

### Result Persistence

Export results to JSON or CSV for external analysis.

```powershell
python -m src.nl2sql.cli --benchmark --dataset tests/golden_dataset.yaml --export-path results.json
```

### Filtering

Run specific subsets of tests.

```powershell
python -m src.nl2sql.cli --benchmark --dataset tests/golden_dataset.yaml --include-ids ops_001 supply_005
```

## 5. Adding New Metrics

1. **Evaluator**: Add a static method to `src/nl2sql/evaluation/evaluator.py`.
2. **Benchmark**: Call the method in `_evaluate_case` inside `src/nl2sql/commands/benchmark.py`.
3. **Reporting**: Update the `rich.Table` columns in `benchmark.py` to display the new metric.
