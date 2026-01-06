# CLI Reference

The **NL2SQL** command line interface uses a subcommand structure.

**Command**: `nl2sql [COMMAND] [OPTIONS]`

## Commands

### `setup`

Interactively configure the environment, create config files, and index schemas.

| Option | Description |
| :--- | :--- |
| `None` | Runs the interactive wizard. |

---

### `run`

Execute natural language queries against your datasources.

**Usage**: `nl2sql run "Your query here"`

| Option | Description | Default |
| :--- | :--- | :--- |
| `--config` | Path to datasources YAML. | `configs/datasources.yaml` |
| `--llm-config` | Path to LLM configuration. | `configs/llm.yaml` |
| `--id` | Target specific datasource ID (bypass routing). | auto-route |
| `--json` | Output result as raw JSON only. | `False` |
| `--no-exec` | Generate and Validate SQL only (skip execution). | `False` |
| `--user` | Context user ID for RBAC checks. | `admin` |

---

### `doctor`

Diagnose environment issues, check dependencies, and verify connectivity.

| Option | Description |
| :--- | :--- |
| `None` | Runs diagnositc checks. |

---

### `index`

Manually trigger the schema indexing process.

| Option | Description | Default |
| :--- | :--- | :--- |
| `--config` | Path to datasources YAML. | `configs/datasources.yaml` |
| `--llm-config` | Path to LLM configuration. | `configs/llm.yaml` |

---

### `list-adapters`

List all currently installed database adapter packages.

---

### `benchmark`

Run the evaluation suite.

| Option | Description | Default |
| :--- | :--- | :--- |
| `--config` | Path to benchmark suite YAML. | `configs/benchmark.yaml` |
| `--output` | Directory to save report artifacts. | `benchmarks/` |
