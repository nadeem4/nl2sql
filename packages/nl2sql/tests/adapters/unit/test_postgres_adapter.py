from nl2sql.adapters.postgres.adapter import PostgresAdapter


def test_uri_names_the_driver_the_postgres_extra_installs():
    # A bare `postgresql://` leaves the driver to SQLAlchemy's default, which
    # changed from psycopg2 to psycopg (v3) in SQLAlchemy 2.1. The `postgres`
    # extra installs psycopg2-binary, so the URI must name psycopg2.
    # Arrange
    adapter = PostgresAdapter.__new__(PostgresAdapter)

    # Act
    uri = adapter.construct_uri(
        {
            "type": "postgres",
            "host": "db.internal",
            "user": "analyst",
            "password": "pw",
            "database": "sales",
        }
    )

    # Assert
    assert uri == "postgresql+psycopg2://analyst:pw@db.internal:5432/sales"
