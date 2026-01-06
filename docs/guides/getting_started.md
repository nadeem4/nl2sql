# Getting Started

This guide will help you install the platform and run your first query.

## Prerequisites

* Python 3.10+
* Docker (for running integrations)

## 1. Installation

The platform is designed as a monorepo. You should install the core and the adapters you need.

```bash
# Clone the repository
git clone https://github.com/nadeem4/nl2sql.git
cd nl2sql

# Install Core
pip install -e packages/adapter-sdk
pip install -e packages/cli # Installs 'nl2sql' command

# Install Adapters (e.g. Postgres)
pip install -e packages/adapters/postgres
```

## 2. Configuration

Create a `datasources.yaml` file. This tells the system how to connect to your databases.

```yaml
- id: my_db
  engine: sqlite
  sqlalchemy_url: "sqlite:///./example.db"
```

## 3. Indexing

Before the AI can understand your schema, you must index it. Additional indexing strategies are discussed in the [Configuration Guide](configuration.md).

```bash
nl2sql --index --config datasources.yaml
```

## 4. Run a Query

Now you are ready to ask questions!

```bash
nl2sql --query "Show me the top 5 users by sales"
```

The system will:

1. Plan the query.
2. Route it to `my_db`.
3. Generate and Validate SQL.
4. Execute and display the results.
