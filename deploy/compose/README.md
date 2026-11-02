# Self-host with Docker Compose

One `api` service (the NL2SQL API and web app) and one data volume, with API keys on. The example config serves
the Chinook sample database.

```bash
cp .env.example .env          # set OPENAI_API_KEY (or the key your provider needs)
docker compose up -d --build
docker compose run --rm api nl2sql index
```

The service is not ready until the index exists. See [`docs/deployment/compose.md`](../../docs/deployment/compose.md)
for your own data, API keys, TLS and upgrades.
