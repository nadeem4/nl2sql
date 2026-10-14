"""The checks on a verified query's SQL, parsed in its datasource's dialect.

A verified query is SQL a person wrote. It must be exactly one statement, a
query at the root, with no write or DDL node anywhere in the tree, and its
``:name`` placeholders must be exactly the declared params.
"""
from __future__ import annotations

import pytest

from nl2sql.config import Curated
from nl2sql.config.curated import (
    CuratedSqlError,
    check_curated,
    inspect_verified_sql,
)

# The bundled adapters' get_dialect() values.
DIALECTS = ["sqlite", "postgres", "mysql", "duckdb", "tsql"]


@pytest.mark.parametrize("dialect", DIALECTS)
def test_named_placeholders_and_tables_are_read_in_every_built_in_dialect(dialect):
    sql = ("WITH recent AS (SELECT CustomerId FROM sales.Invoice WHERE Total > :min_total) "
           "SELECT c.CustomerId FROM Customer c JOIN recent r ON r.CustomerId = c.CustomerId "
           "WHERE c.Country = :country")

    found = inspect_verified_sql(sql, dialect)

    assert found.placeholders == {"min_total", "country"}
    # CTE names are not tables.
    assert sorted(found.tables) == [("", "Customer"), ("sales", "Invoice")]


@pytest.mark.parametrize("sql", [
    "SELECT 1 UNION SELECT 2",
    "SELECT a FROM t;",
    "WITH x AS (SELECT 1 AS a) SELECT a FROM x",
])
@pytest.mark.parametrize("dialect", DIALECTS)
def test_read_only_queries_pass(sql, dialect):
    inspect_verified_sql(sql, dialect)


@pytest.mark.parametrize("sql, fragment", [
    ("SELECT 1; SELECT 2", "one statement"),
    ("", "one statement"),
    ("DELETE FROM Customer", "read-only"),
    ("UPDATE Customer SET Country = 'X'", "read-only"),
    ("INSERT INTO Customer SELECT * FROM Customer", "read-only"),
    ("DROP TABLE Customer", "read-only"),
    ("CREATE TABLE x AS SELECT 1 AS a", "read-only"),
    ("ALTER TABLE Customer ADD COLUMN y INT", "read-only"),
    ("WITH d AS (DELETE FROM Customer RETURNING *) SELECT * FROM d", "read-only"),
    ("SELECT * INTO copy_of_customer FROM Customer", "read-only"),
    ("SELECT * FROM Customer WHERE CustomerId = ?", "positional"),
    ("SELECT FROM WHERE", "parse"),
])
def test_unsafe_or_broken_sql_is_refused(sql, fragment):
    with pytest.raises(CuratedSqlError, match=fragment):
        inspect_verified_sql(sql, "postgres")


@pytest.mark.parametrize("dialect", ["sqlite", "tsql"])
def test_a_command_the_parser_cannot_type_is_refused(dialect):
    with pytest.raises(CuratedSqlError, match="read-only"):
        inspect_verified_sql("VACUUM", dialect)


def test_an_unknown_dialect_is_reported_not_raised_as_something_else():
    with pytest.raises(CuratedSqlError, match="dialect"):
        inspect_verified_sql("SELECT 1", "no-such-dialect")


# --------------------------------------------------------------------------
# check_curated: the whole file against the configured datasources
# --------------------------------------------------------------------------

def _cfg(sql: str, datasource: str = "chinook") -> Curated:
    return Curated.model_validate({"datasources": {datasource: {"verified_queries": [{
        "id": "top_customers",
        "description": "Top customers in a year",
        "questions": ["Top {n} customers in {year}"],
        "params": {"n": {"type": "int", "default": 10}, "year": {"type": "int", "example": 2012}},
        "sql": sql,
    }]}}})


GOOD_SQL = "SELECT CustomerId FROM Invoice WHERE BillingYear = :year LIMIT :n"


def test_a_good_file_has_no_issues():
    assert check_curated(_cfg(GOOD_SQL), {"chinook": "sqlite"}) == []


def test_a_datasource_that_is_not_configured_is_an_issue():
    [issue] = check_curated(_cfg(GOOD_SQL, datasource="chinok"), {"chinook": "sqlite"})

    assert issue.datasource_id == "chinok"
    assert "not a configured datasource" in issue.message


def test_a_placeholder_with_no_param_is_an_issue():
    [issue] = check_curated(_cfg(GOOD_SQL + " OFFSET :skip"), {"chinook": "sqlite"})

    assert issue.query_id == "top_customers"
    assert "skip" in issue.message


def test_a_param_the_sql_never_uses_is_an_issue():
    [issue] = check_curated(_cfg("SELECT CustomerId FROM Invoice LIMIT :n"), {"chinook": "sqlite"})

    assert "year" in issue.message


def test_unsafe_sql_is_an_issue_naming_the_query():
    [issue] = check_curated(_cfg("DELETE FROM Invoice WHERE BillingYear = :year AND n = :n"),
                            {"chinook": "sqlite"})

    assert (issue.datasource_id, issue.query_id) == ("chinook", "top_customers")
    assert "read-only" in issue.message


def test_the_sql_is_parsed_in_the_datasource_dialect():
    # Square-bracket identifiers are T-SQL; the same text is not one valid
    # Postgres query.
    sql = "SELECT TOP 5 [CustomerId] FROM [Invoice] WHERE [BillingYear] = :year AND :n > 0"

    assert check_curated(_cfg(sql), {"chinook": "tsql"}) == []
    assert check_curated(_cfg(sql), {"chinook": "postgres"}) != []
