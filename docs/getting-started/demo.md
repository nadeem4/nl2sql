# Quickstart (Demo)

The CLI comes with a powerful `setup` command that can generate a fully functional **Demo Environment**, allowing you to test the platform without connecting to your production databases immediately.

## 1. Lite Mode (Fastest)

**Lite Mode** uses **SQLite** databases. It requires no Docker containers and runs entirely locally. Ideal for quick logic verification.

```bash
nl2sql setup --demo --lite
```

**What happens?**

1. Creates a `.env.demo` file.
2. Generates a local `my_sqlite_db.db`.
3. Populates it with sample data (e.g., "Users", "Orders").
4. Configures `configs/datasources.yaml` to point to this file.

**Run a Query:**

```bash
nl2sql --env demo run "Show me all users in the system"
```

## 2. Docker Mode (Full Stack)

**Docker Mode** spins up real **PostgreSQL** or **MySQL** containers using `docker-compose`. This simulates a real production environment.

```bash
nl2sql setup --demo --docker --api-key sk-...
```

* `--api-key`: Optional. If provided, sets up your LLM configuration immediately.

**What happens?**

1. Creates a `deploy/docker` directory with `docker-compose.yml`.
2. Starts PostgreSQL/MySQL containers.
3. Waits for health checks.
4. Seeds the databases with sample schema and data.
5. Creates `.env.demo` pointing to these containers (localhost ports).

## 3. Interactive Wizard

If you run `setup` without flags, you enter the Interactive Wizard:

```bash
nl2sql setup
```

The wizard will guide you through:

1. **Environment Selection**: Dev, Demo, or Prod.
2. **Datasource Configuration**: Host, Port, User, Password (securely handled).
3. **LLM Configuration**: OpenAI, Gemini, or Ollama selection.
4. **RBAC Policies**: Generating default Admin roles.

## Next Steps

Once set up, you should run **Indexing** to prepare the Vector Store:

```bash
nl2sql --env demo index
```
