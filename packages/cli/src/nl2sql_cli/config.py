
CORE_PACKAGE = "nl2sql-core"
CORE_MODULE = "nl2sql.cli"

# Check packages/adapters/* for correct package names (e.g. pyproject.toml)
KNOWN_ADAPTERS = {
    "sqlite": "nl2sql-sqlite",
    "postgresql": "nl2sql-postgres",
    "mysql": "nl2sql-mysql",
    "mssql": "nl2sql-mssql",
}
