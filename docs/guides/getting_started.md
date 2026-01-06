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

## 2. Setup

Run the interactive setup wizard. This will inspect your environment, guide you through creating a configuration, and index your database schema.

```bash
nl2sql setup
```

The wizard will ask for:

1. **Database Details** (Host, Port, User, Password).
2. **LLM Provider** (OpenAI, Gemini, Ollama).
3. Confirmation to install required **Adapters** (e.g. `nl2sql-postgres`).

## 3. Run a Query

Now you are ready to ask questions!

```bash
nl2sql run "Show me the top 5 users by sales"
```

The system will:

1. Plan the query.
2. Route it to the correct database.
3. Generate and Validate SQL.
4. Execute and display the results.
