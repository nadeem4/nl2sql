# Welcome to the NL2SQL Platform

The **NL2SQL Platform** is an enterprise-grade engine for converting natural language into SQL.

It features:

* **Defense-in-Depth Security** (RBAC, Read-Only enforcement).
* **Multi-Database Routing** (Federated queries across Postgres, MySQL, etc.).
* **Agentic Reasoning** (Iterative planning and self-correction).

## Quick Start

```bash
# Install Dependencies
pip install -e packages/core

# Run a Query
python -m nl2sql.cli --query "Show me top 5 users"
```

[Get Started Guide](guides/getting_started.md){ .md-button .md-button--primary }

## Documentation Structure

### [Architecture](architecture/overview.md)

Understand the "Map-Reduce" design, the **SQL Agent** pipeline, and how the **Physical Validator** ensures safety.

### [Guides](guides/getting_started.md)

Step-by-step instructions for installation, configuration, and deploying to production with Docker.

### [Reference](reference/cli.md)

Technical specifications for the CLI, API, and internal Node logic.
