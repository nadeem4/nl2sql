#!/bin/bash
# The `mssql-init` service: restore AdventureWorksLT, load the wide schema
# into `wide`, then create the reader login, which may read AdventureWorksLT
# only. Each database that already exists is skipped, so a second `up` is
# quick. The reader login is created last: the `mssql` healthcheck waits for it.
set -eo pipefail

sqlcmd=(/opt/mssql-tools18/bin/sqlcmd -C -b -r1 -S mssql -U sa -P "$MSSQL_SA_PASSWORD")

for _ in $(seq 1 90); do
  "${sqlcmd[@]}" -Q "SELECT 1" > /dev/null 2>&1 && break
  sleep 2
done
"${sqlcmd[@]}" -Q "SELECT 1" > /dev/null

exists() {
  "${sqlcmd[@]}" -h -1 -W -Q "SET NOCOUNT ON; SELECT CASE WHEN DB_ID('$1') IS NULL THEN 0 ELSE 1 END" | tr -d '[:space:]'
}

if [ "$(exists AdventureWorksLT2022)" != 1 ]; then
  echo "mssql-init: restoring AdventureWorksLT2022"
  "${sqlcmd[@]}" -Q "RESTORE DATABASE AdventureWorksLT2022 FROM DISK = N'/samples/AdventureWorksLT2022.bak'
    WITH MOVE 'AdventureWorksLT2022_Data' TO '/var/opt/mssql/data/AdventureWorksLT2022.mdf',
         MOVE 'AdventureWorksLT2022_Log' TO '/var/opt/mssql/data/AdventureWorksLT2022_log.ldf'" > /dev/null
fi

if [ "$(exists wide)" != 1 ]; then
  echo "mssql-init: loading the wide schema"
  # A test bed trades durability for loading speed: commits are not
  # flushed one by one.
  "${sqlcmd[@]}" -Q "CREATE DATABASE wide; ALTER DATABASE wide SET RECOVERY SIMPLE; ALTER DATABASE wide SET DELAYED_DURABILITY = FORCED"
  # A half-loaded `wide` would be skipped by the next run, so drop it on failure.
  if ! "${sqlcmd[@]}" -d wide -i /samples/wide_tsql.sql > /dev/null; then
    "${sqlcmd[@]}" -Q "DROP DATABASE wide"
    exit 1
  fi
  echo "mssql-init: wide schema loaded"
fi

reader_password=${MSSQL_SA_PASSWORD//\'/\'\'}
"${sqlcmd[@]}" -d AdventureWorksLT2022 -Q "
IF SUSER_ID('nl2sql_reader') IS NULL
BEGIN
  CREATE LOGIN nl2sql_reader WITH PASSWORD = N'${reader_password}', CHECK_POLICY = OFF;
  CREATE USER nl2sql_reader FOR LOGIN nl2sql_reader;
  ALTER ROLE db_datareader ADD MEMBER nl2sql_reader;
END"
echo "mssql-init: ready"
# Mark the load done for the healthcheck and stay up: `docker compose up --wait`
# fails on an exited container, and a second `up` skips everything and would
# exit at once. /tmp is the container's own, so a restart starts unmarked.
touch /tmp/loaded
exec sleep infinity
