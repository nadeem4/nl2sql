# Benchmarking

The platform includes a built-in **Matrix Benchmarking** tool to evaluate accuracy across different LLMs and Datasources.

## Running a Benchmark

```bash
nl2sql benchmark --config configs/benchmark_suite.yaml
```

## Matrix Testing

You can define multiple LLM configurations in a `bench_config.yaml` to run head-to-head comparisons.

```yaml
# bench_config.yaml
gpt4_base:
  default: 
    provider: openai
    model: gpt-4
claude3_opus:
  default: 
    provider: anthropic
    model: claude-3-opus
```

Run the matrix:

```bash
nl2sql benchmark --bench-config bench_config.yaml
```

## Metrics

The benchmark reports:

* **Execution Success Rate (ESR)**: % of queries that ran without error.
* **Valid SQL Rate**: % of generated SQL that passed Physical Validation.
* **Accuracy**: (Requires Golden SQL) % of results matching ground truth.

::: nl2sql.cli.commands.benchmark.run_benchmark
