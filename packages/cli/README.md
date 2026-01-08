# NL2SQL CLI

The Unified Command Line Interface for the NL2SQL ecosystem. This tool acts as a dedicated manager and frontend for the `nl2sql-core` engine and its adapters.

## Features

- **Lifecycle Management**: Install adapters, check environment health, and setup your database connections.
- **Robustness**: Runs independently of the core engine. Diagnostics work even if the core is broken.
- **Interactive TUI**: Seamlessly launches the Textual-based UI for chat and visualization.
- **Control Plane**: Orchestrates the core engine via a detached subprocess architecture.

## Installation

```bash
# Install from source (dev)
pip install -e packages/cli

# Verify installation
nl2sql --help
```

> **Note**: This CLI does *not* force-install `nl2sql-core`. You can use `nl2sql doctor` or `nl2sql setup` to diagnose and install the core engine if missing.

## Usage

### 1. Setup & Diagnostics

**Health Check**:
Diagnose Python version, installed adapters, core availability, and database connectivity.

```bash
nl2sql doctor
```

**First Run Wizard**:
Interactive guide to install Core, missing adapters, and trigger schema indexing.

```bash
nl2sql setup
```

**Install Adapters**:
Easily install specific database adapters.

```bash
nl2sql install postgres
nl2sql install sqlite
```

### 2. Execution

**Interactive Chat**:
Launch the Textual TUI for a rich, interactive Session.

```bash
nl2sql chat
```

**One-Shot Query**:
Execute a query and see the result (JSON or Text streaming).

```bash
nl2sql run "Show me the top 5 customers by revenue" --role sales_analyst
```

### 3. Quickstart (Demo Mode) 🧪

Try NL2SQL immediately without configuring your own database.

```bash
# 1. Generate Demo Environment (SQLite databases)
nl2sql setup --demo

# 2. Run a query against the demo env
nl2sql --env demo run "Show me broken machines in Austin"
```

### 4. Environment Isolation 🌍

Manage multiple environments (Dev, Prod, Demo) safely with the `--env` flag.
**Note**: The `--env` flag must be placed *before* the command (run/index/setup).

```bash
# Uses configs/datasources.dev.yaml and data/vector_store_dev/
nl2sql --env dev run "..."

# Uses configs/datasources.prod.yaml and data/vector_store_prod/
nl2sql --env prod run "..."
```

## Architecture

The CLI follows a **Subprocess Control Plane** pattern. It does not import `nl2sql.core` directly into its own process space. Instead, it invokes the core module (`python -m nl2sql.cli`) via `subprocess`.

This ensures:

1. **Isolation**: Syntax errors or crashes in Core do not crash the CLI.
2. **Stability**: The CLI remains responsive to help you fix the environment.
3. **Clean State**: Each run starts with a fresh Python interpreter state.

## Configuration

The CLI relies on the standard NL2SQL configuration files:

- `datasources.yaml`: Connection profiles.
- `llm_config.yaml`: LLM provider settings.
- `configs/policies.json`: RBAC policies and permissions.

Run `nl2sql doctor` to see where it expects these files to be.

## Troubleshooting

### "nl2sql is not recognized" (PATH Issue)

If you see a warning about scripts not being on PATH during installation, you need to add your Python Scripts directory to your system PATH.

**PowerShell Command (Current User):**

```powershell
$path = [Environment]::GetEnvironmentVariable('Path', 'User')
$newPath = 'C:\Users\YOUR_USER\AppData\Local\Packages\PythonSoftwareFoundation.Python.3.11_...\Scripts'
[Environment]::SetEnvironmentVariable('Path', "$path;$newPath", 'User')
```

Restart your terminal after running this command.
