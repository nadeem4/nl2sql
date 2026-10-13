
CORE_PACKAGE = "nl2sql"
CORE_MODULE = "nl2sql.cli"

# Every dialect adapter now ships inside the single `nl2sql-engine`
# distribution, so the adapter module is always importable. What is optional
# is the DB driver each one needs to actually connect, carried by an extra.
# Keys are the `nl2sql.adapters` entry-point names, which are also the
# `connection.type` values a datasource is configured with.
KNOWN_ADAPTERS = {
    "sqlite": "nl2sql-engine",
    "duckdb": "nl2sql-engine[duckdb]",
    "postgres": "nl2sql-engine[postgres]",
    "mysql": "nl2sql-engine[mysql]",
    "mssql": "nl2sql-engine[mssql]",
}

# The driver module that must import for the adapter above to be usable.
# sqlite needs none beyond the standard library.
ADAPTER_DRIVERS = {
    "sqlite": "sqlite3",
    "duckdb": "duckdb_engine",
    "postgres": "psycopg2",
    "mysql": "pymysql",
    "mssql": "pyodbc",
}
