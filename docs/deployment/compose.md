# Docker Compose

Self-host NL2SQL with the web app: one `api` service, one data volume, API keys on. The files are in
`deploy/compose/`:

```text
deploy/compose/
  docker-compose.yml    one service, one volume, a /ready healthcheck
  config/nl2sql.yaml    the example config: the Chinook sample, api.auth: api_keys
  .env.example          the provider key, NL2SQL_DATA_DIR and NL2SQL_MODE
```

The image is built from the `Dockerfile` at the repository root: a Node stage builds the web app, and the API
stage carries all five adapters, Microsoft's ODBC driver for SQL Server, the sample databases and the local
embedding model. See [Docker](docker.md) for the image on its own.

## Up

```bash
cd deploy/compose
cp .env.example .env          # set OPENAI_API_KEY (or the key your provider needs); .env is git-ignored
docker compose up -d --build
```

The service starts but is **not ready** until an index exists: `GET /api/v1/ready` answers `503`, and an admin
gets the reason. Nothing indexes on `up`.

## Index

```bash
docker compose run --rm api nl2sql index
```

Run it once, and again when the schema changes (or `POST /api/v1/jobs/index` as an admin). The index lives in the
`nl2sql-data` volume.

## The first key

`api.auth: api_keys` means every request carries a key. Create the first admin key; it is printed once:

```bash
docker compose run --rm api nl2sql keys create --as admin --user admin --role admin
```

Open <http://localhost:8000>, paste the key where the app asks, and ask a question. From code, send
`Authorization: Bearer <key>` to `/api/v1`.

## Connect your database

Edit `config/nl2sql.yaml`: replace the `chinook` datasource and the roles' `allow` lists, for example

```yaml
datasources:
  - id: sales
    type: postgres
    connection:
      host: db.internal
      database: sales
      user: nl2sql_reader
      password: "${env:SALES_PASSWORD}"
```

add `SALES_PASSWORD` to `.env`, then `docker compose up -d` and index again. Keep `api.auth: api_keys`: the
server refuses to listen beyond loopback with `auth: none`.

## Demo or your data

`mode:` in `nl2sql.yaml` and `NL2SQL_MODE` in `.env` choose between `demo`, `your_data` and `hosted`; the
variable wins. The example uses `your_data`.

## TLS

Put a reverse proxy in front. With [Caddy](https://caddyserver.com):

```text
nl2sql.example.com {
    reverse_proxy api:8000
}
```

## Backups and upgrades

Everything the service writes is in the `nl2sql-data` volume (index, state store, traces, results, audit log). Back
it up with a throwaway container:

```bash
docker run --rm -v nl2sql_nl2sql-data:/data -v "$PWD":/backup alpine tar czf /backup/nl2sql-data.tgz -C /data .
```

To upgrade, pull the new source, `docker compose up -d --build`, and re-index if the release notes say the index
format changed.
