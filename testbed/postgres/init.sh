#!/bin/bash
# Run once, by the postgres image's init, on a fresh data volume: Pagila into
# `pagila`, the wide schema into `wide`, and the reader login, which may read
# Pagila only. The image runs this on a socket-only server, so the
# healthcheck's TCP probe answers only after it has finished.
set -eo pipefail

psql_run=(psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --no-password --quiet)

"${psql_run[@]}" -c "CREATE DATABASE pagila" -c "CREATE DATABASE wide"
"${psql_run[@]}" --dbname pagila -f /samples/pagila-schema.sql > /dev/null
"${psql_run[@]}" --dbname pagila -f /samples/pagila-insert-data.sql > /dev/null
"${psql_run[@]}" --dbname wide -f /samples/wide_postgres.sql > /dev/null

"${psql_run[@]}" --dbname pagila -v reader_password="$POSTGRES_PASSWORD" <<'SQL'
CREATE ROLE nl2sql_reader LOGIN PASSWORD :'reader_password';
REVOKE CONNECT ON DATABASE wide FROM PUBLIC;
GRANT CONNECT ON DATABASE pagila TO nl2sql_reader;
GRANT USAGE ON SCHEMA public TO nl2sql_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO nl2sql_reader;
SQL
