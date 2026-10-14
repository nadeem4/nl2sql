#!/bin/bash
# Run once, by the mysql image's init, on a fresh data volume: Sakila, the wide
# schema (one database per schema, `wide_s01` ... `wide_s20` and `wide_audit`),
# and the reader login, which may read Sakila only. The `mysql` client handles
# Sakila's DELIMITER blocks. The init server skips networking, so the
# healthcheck's TCP ping answers only after this has finished.
set -eo pipefail

mysql_run=(mysql --protocol=socket -uroot "-p${MYSQL_ROOT_PASSWORD}")

"${mysql_run[@]}" < /samples/sakila-schema.sql
"${mysql_run[@]}" < /samples/sakila-data.sql
"${mysql_run[@]}" < /samples/wide_mysql.sql

reader_password=${MYSQL_ROOT_PASSWORD//\'/\'\'}
"${mysql_run[@]}" <<SQL
CREATE USER 'nl2sql_reader'@'%' IDENTIFIED BY '${reader_password}';
GRANT SELECT, SHOW VIEW ON sakila.* TO 'nl2sql_reader'@'%';
SQL
