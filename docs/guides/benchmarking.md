# Evaluation & Benchmarking

The platform includes a comprehensive benchmarking suite to measure accuracy, stability, and routing performance.

## Running Benchmarks

Benchmarks are run via the CLI.

```bash
# Run the full Golden Set
python -m nl2sql.cli --benchmark --dataset tests/golden_dataset.yaml
```

## Metrics

1. **Execution Accuracy**: Measures if the returned data matches the "Ground Truth".
    - *Method*: Executes Generated SQL vs Expected SQL and compares row sets.
2. **Semantic Accuracy**: Used when data mismatches. Uses an LLM Judge to check if the *intent* is equivalent.
3. **Routing Accuracy**: Did we pick the right database?
4. **Stability (Pass@K)**: Runs each query `K` times to ensure results are deterministic.

## Benchmarking Options

| Flag | Description |
| :--- | :--- |
| `--routing-only` | Skips SQL generation; tests Decomposer only. |
| `--iterations N` | Runs each queries N times (Pass@N stability). |
| `--export-path` | Saves results to generic JSON/CSV. |
