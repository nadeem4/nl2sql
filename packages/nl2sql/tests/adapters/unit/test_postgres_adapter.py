from nl2sql.adapters.postgres.adapter import PostgresAdapter, PostgresConnection


def test_uri_names_the_driver_the_postgres_extra_installs():
    # A bare `postgresql://` leaves the driver to SQLAlchemy's default, which
    # changed from psycopg2 to psycopg (v3) in SQLAlchemy 2.1. The `postgres`
    # extra installs psycopg2-binary, so the URI must name psycopg2.
    # Arrange
    adapter = PostgresAdapter.__new__(PostgresAdapter)

    # Act
    url = adapter.url(PostgresConnection(host="db.internal", user="analyst", password="pw", database="sales"))

    # Assert
    assert url.render_as_string(hide_password=False) == (
        "postgresql+psycopg2://analyst:pw@db.internal:5432/sales?sslmode=prefer")
